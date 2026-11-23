"""DIA delayed option chain (front 2 expirations, ATM+-10).

Usage: python 81_DIA_CHAIN.py [--loop] | Env: TRADIER_TOKEN (fallback TRADIER_API_KEY)
Source: Tradier https://api.tradier.com/v1/markets/options/{expirations,chains} symbol=DIA (priority),
  fallback CBOE https://cdn.cboe.com/api/global/delayed_quotes/options/DIA.json,
  fallback Yahoo https://query2.finance.yahoo.com/v7/finance/options/DIA (crumb via fc.yahoo.com)
Scope: delayed chain, front 2 expirations ATM+-10 strikes.
Save: data/DIA_CHAIN/YYYY-MM-DD.jsonl (UTC date) dedup on expiration|strike|option_type|quote_utc.
Loop: --loop repeats every 15min; single run collects once.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "DIA"
OUT_DIR = Path(__file__).parent / "data" / "DIA_CHAIN"
USER_AGENT = {"User-Agent": "Mozilla/5.0"}
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
TRADIER_EXPIRATIONS_URL = "https://api.tradier.com/v1/markets/options/expirations"
TRADIER_QUOTES_URL = "https://api.tradier.com/v1/markets/quotes"
TRADIER_CHAINS_URL = "https://api.tradier.com/v1/markets/options/chains"
CBOE_OPTIONS_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/DIA.json"
YAHOO_OPTIONS_URL = "https://query2.finance.yahoo.com/v7/finance/options/DIA"
YAHOO_OPTIONS_URL1 = "https://query1.finance.yahoo.com/v7/finance/options/DIA"


def atm_slice(strikes, underlying, window=10):
    """Keep ATM+-window strikes sorted around underlying."""
    ordered = sorted(strikes)
    if not ordered or underlying is None:
        return ordered[: (2 * window + 1)]
    idx = min(range(len(ordered)), key=lambda k: abs(ordered[k] - underlying))
    return ordered[max(0, idx - window): idx + window + 1]


def tradier_chain(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(TRADIER_EXPIRATIONS_URL,
                        headers=headers, params={"symbol": SYMBOL, "includeAllRoots": "true"}, timeout=20)
    resp.raise_for_status()
    expirations = sorted((resp.json().get("expirations") or {}).get("date") or [])
    today = datetime.now(timezone.utc).date().isoformat()
    expirations = [e for e in expirations if e >= today][:2]
    if not expirations:
        raise RuntimeError("no expirations")
    quote_resp = requests.get(TRADIER_QUOTES_URL, headers=headers,
                              params={"symbols": SYMBOL}, timeout=20)
    quote_resp.raise_for_status()
    quote_json = quote_resp.json()
    quote = (quote_json.get("quotes") or {}).get("quote")
    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    underlying = (quote or {}).get("last")
    rows = []
    for exp in expirations:
        chain_resp = requests.get(TRADIER_CHAINS_URL, headers=headers,
                                  params={"symbol": SYMBOL, "expiration": exp, "greeks": "false"}, timeout=20)
        chain_resp.raise_for_status()
        opts = ((chain_resp.json().get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        strikes = {float(o["strike"]) for o in opts if o.get("strike") is not None}
        keep = set(atm_slice(strikes, float(underlying) if underlying else None))
        for opt in opts:
            try:
                strike = float(opt.get("strike"))
            except (TypeError, ValueError):
                continue
            if strike not in keep:
                continue
            opt_type = str(opt.get("option_type", "")).lower()
            opt_type = "call" if opt_type.startswith("c") else "put" if opt_type.startswith("p") else opt_type
            rows.append({"expiration": exp, "strike": strike, "option_type": opt_type,
                         "bid": opt.get("bid"), "ask": opt.get("ask"), "last": opt.get("last"),
                         "volume": opt.get("volume"), "open_interest": opt.get("open_interest"),
                         "iv": None, "underlying": float(underlying) if underlying else None})
    return rows, "tradier-delayed", underlying


def parse_occ(sym):
    """Parse OCC symbol DIA261113C00355000 -> (expiration_iso, strike, option_type)."""
    date_part = sym[3:9]
    cp = sym[9:10]
    strike_part = sym[10:]
    exp = datetime.strptime(date_part, "%y%m%d").date().isoformat()
    strike = int(strike_part) / 1000.0
    opt_type = "call" if cp == "C" else "put" if cp == "P" else cp.lower()
    return exp, strike, opt_type


def cboe_chain():
    resp = requests.get(CBOE_OPTIONS_URL, headers=USER_AGENT, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}
    underlying = data.get("current_price")
    opts = data.get("options") or []
    grouped = {}
    for opt in opts:
        sym = opt.get("option") or ""
        try:
            exp, strike, opt_type = parse_occ(sym)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(exp, []).append((strike, opt_type, opt))
    today = datetime.now(timezone.utc).date().isoformat()
    expirations = sorted(e for e in grouped if e >= today)[:2]
    if not expirations:
        raise RuntimeError("no expirations")
    rows = []
    for exp in expirations:
        items = grouped[exp]
        strikes = {s for s, _, _ in items}
        keep = set(atm_slice(strikes, float(underlying) if underlying else None))
        for strike, opt_type, opt in items:
            if strike not in keep:
                continue
            rows.append({"expiration": exp, "strike": strike, "option_type": opt_type,
                         "bid": opt.get("bid"), "ask": opt.get("ask"),
                         "last": opt.get("last_trade_price"),
                         "volume": opt.get("volume"), "open_interest": opt.get("open_interest"),
                         "iv": opt.get("iv"), "underlying": float(underlying) if underlying else None})
    return rows, "cboe-delayed", underlying


def yahoo_session():
    sess = requests.Session()
    sess.headers.update(USER_AGENT)
    sess.get("https://fc.yahoo.com", timeout=20)
    crumb_resp = sess.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20)
    crumb_resp.raise_for_status()
    crumb = crumb_resp.text.strip()
    if not crumb or "error" in crumb.lower():
        raise RuntimeError("no crumb")
    return sess, crumb


def yahoo_get(sess, crumb, url, params=None):
    params = dict(params or {})
    params["crumb"] = crumb
    resp = sess.get(url, params=params, timeout=20)
    if resp.status_code == 401 and url == YAHOO_OPTIONS_URL:
        resp = sess.get(YAHOO_OPTIONS_URL1, params=params, timeout=20)
    resp.raise_for_status()
    return resp


def yahoo_chain():
    sess, crumb = yahoo_session()
    first = yahoo_get(sess, crumb, YAHOO_OPTIONS_URL)
    first.raise_for_status()
    result = first.json()["optionChain"]["result"][0]
    underlying = (result.get("quote") or {}).get("regularMarketPrice")
    exp_ts = result.get("expirationDates") or []
    today_ts = datetime.now(timezone.utc).timestamp()
    exp_ts = sorted(e for e in exp_ts if e >= today_ts - 86400)[:2]
    rows = []
    for ts in exp_ts:
        detail_resp = yahoo_get(sess, crumb, YAHOO_OPTIONS_URL, params={"date": ts})
        detail_resp.raise_for_status()
        detail = detail_resp.json()["optionChain"]["result"][0]
        underlying = (detail.get("quote") or {}).get("regularMarketPrice", underlying)
        exp = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        chain = detail.get("options")[0] if detail.get("options") else {}
        calls, puts = chain.get("calls") or [], chain.get("puts") or []
        strikes = set()
        for opt in calls + puts:
            try:
                strikes.add(float(opt.get("strike")))
            except (TypeError, ValueError):
                pass
        keep = set(atm_slice(strikes, float(underlying) if underlying else None))
        for opt in calls:
            try:
                strike = float(opt.get("strike"))
            except (TypeError, ValueError):
                continue
            if strike not in keep:
                continue
            rows.append({"expiration": exp, "strike": strike, "option_type": "call",
                         "bid": opt.get("bid"), "ask": opt.get("ask"), "last": opt.get("lastPrice"),
                         "volume": opt.get("volume"), "open_interest": opt.get("openInterest"),
                         "iv": opt.get("impliedVolatility"), "underlying": float(underlying) if underlying else None})
        for opt in puts:
            try:
                strike = float(opt.get("strike"))
            except (TypeError, ValueError):
                continue
            if strike not in keep:
                continue
            rows.append({"expiration": exp, "strike": strike, "option_type": "put",
                         "bid": opt.get("bid"), "ask": opt.get("ask"), "last": opt.get("lastPrice"),
                         "volume": opt.get("volume"), "open_interest": opt.get("openInterest"),
                         "iv": opt.get("impliedVolatility"), "underlying": float(underlying) if underlying else None})
    return rows, "yahoo-delayed", underlying


def append_records(records, path):
    seen = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                doc = json.loads(line)
                seen.add((doc.get("expiration"), doc.get("strike"), doc.get("option_type"), doc.get("quote_utc")))
            except (TypeError, ValueError):
                pass
    written = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            key = (rec["expiration"], rec["strike"], rec["option_type"], rec["quote_utc"])
            if key in seen:
                continue
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            seen.add(key)
            written += 1
    return written


def collect(now):
    slot = now.replace(second=0, microsecond=0)
    slot = slot.replace(minute=(slot.minute // 15) * 15)
    slot_str = slot.strftime(TS_FMT)
    template = {"symbol": SYMBOL, "expiration": None, "strike": None, "option_type": None, "bid": None,
                "ask": None, "last": None, "volume": None, "open_interest": None, "iv": None,
                "underlying": None, "is_delayed": True, "quote_utc": slot_str,
                "received_at_utc": now.strftime(TS_FMT)}
    token = os.getenv("TRADIER_TOKEN") or os.getenv("TRADIER_API_KEY")
    rows, src = None, None
    if token:
        try:
            rows, src, _ = tradier_chain(token)
        except Exception:
            rows, src = None, None
    if rows is None:
        try:
            rows, src, _ = cboe_chain()
        except Exception:
            rows, src = None, None
    if rows is None:
        try:
            rows, src, _ = yahoo_chain()
        except Exception as exc:
            return [dict(template, source="yahoo-delayed", status=f"http_error:{type(exc).__name__}")], src or "yahoo-delayed"
    if not rows:
        return [dict(template, source=src, status="delayed")], src
    return [dict(template, source=src, status="ok", **r) for r in rows], src


def main(argv=None):
    parser = argparse.ArgumentParser(description="DIA delayed option chain (front2 ATM+-10)")
    parser.add_argument("--loop", action="store_true", help="repeat every 15min")
    args = parser.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        records, _ = collect(now)
        out_path = OUT_DIR / (now.date().isoformat() + ".jsonl")
        print(f"{records[0].get('source')} {records[0].get('status')} +{append_records(records, out_path)}")
        if not args.loop:
            break
        time.sleep(900)


if __name__ == "__main__":
    main()
