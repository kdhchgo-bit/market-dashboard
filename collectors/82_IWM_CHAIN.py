"""IWM option chain (delayed, front-2 expiries ATM+-10) via Tradier -> CBOE -> Yahoo fallback.
Usage: python 82_IWM_CHAIN.py [--loop]
Tradier: GET api.tradier.com/v1/markets/options/{expirations,chains}?symbol=IWM (env TRADIER_TOKEN|TRADIER_API_KEY)
CBOE (no key): https://cdn.cboe.com/api/global/delayed_quotes/options/IWM.json (15min delayed)
Yahoo fallback (no key, crumb): cookie from https://fc.yahoo.com + crumb from
  query2.finance.yahoo.com/v1/test/getcrumb, then query2.finance.yahoo.com/v7/finance/options/IWM[?date=epoch&crumb=...]
Out: data/IWM_CHAIN/YYYY-MM-DD.jsonl (UTC date), dedup on expiration|strike|type. --loop: every 15min.
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "IWM"
OUTPUT_DIR = Path(__file__).parent / "data" / "IWM_CHAIN"
USER_AGENT = {"User-Agent": "Mozilla/5.0"}
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TRADIER_BASE = "https://api.tradier.com/v1/markets"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/IWM.json"
YAHOO_URL = "https://query2.finance.yahoo.com/v7/finance/options/" + SYMBOL
YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
OCC_SUFFIX = re.compile(r"(\d{6})([CP])(\d{8})$")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def tradier_headers():
    token = os.getenv("TRADIER_TOKEN") or os.getenv("TRADIER_API_KEY")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def tradier_expirations(headers):
    r = requests.get(TRADIER_BASE + "/options/expirations", headers=headers, params={"symbol": SYMBOL}, timeout=20)
    if r.status_code != 200:
        return None
    expirations = (r.json().get("expirations") or {}).get("expiration") or []
    if isinstance(expirations, dict):
        expirations = [expirations]
    ordered = sorted(
        str(x.get("date") if isinstance(x, dict) else x)[:10]
        for x in expirations
        if (x.get("date") if isinstance(x, dict) else x)
    )
    return ordered or None


def tradier_chain(expiration, headers):
    r = requests.get(
        TRADIER_BASE + "/options/chains",
        headers=headers,
        params={"symbol": SYMBOL, "expiration": expiration, "greeks": "true"},
        timeout=20,
    )
    if r.status_code != 200:
        return None
    try:
        options = (r.json().get("options") or {}).get("option") or []
    except Exception:
        return None
    return [options] if isinstance(options, dict) else options


def tradier_quote(headers):
    try:
        r = requests.get(TRADIER_BASE + "/quotes", headers=headers, params={"symbols": SYMBOL}, timeout=20)
        if r.status_code != 200:
            return None
        quote = (r.json().get("quotes") or {}).get("quote")
        if isinstance(quote, list):
            quote = quote[0] if quote else {}
        return float((quote or {}).get("last"))
    except Exception:
        return None


def yahoo_fetch(expiry_epoch=None, sess=None, crumb=None):
    try:
        client = sess or requests
        params = {}
        if expiry_epoch:
            params["date"] = int(expiry_epoch)
        if crumb:
            params["crumb"] = crumb
        r = client.get(YAHOO_URL, headers=USER_AGENT, params=params or None, timeout=20)
        if r.status_code != 200:
            return None
        res = (r.json().get("optionChain") or {}).get("result") or []
        return res[0] if res else None
    except Exception:
        return None


def yahoo_session_with_crumb():
    """Return (session, crumb) or (None, None). Cookie fetch may 404 yet still set cookies."""
    try:
        sess = requests.Session()
        sess.headers.update(USER_AGENT)
        try:
            sess.get(YAHOO_COOKIE_URL, timeout=20)
        except Exception:
            pass
        r = sess.get(YAHOO_CRUMB_URL, timeout=20)
        if r.status_code != 200:
            return None, None
        crumb = (r.text or "").strip()
        if not crumb or "<" in crumb:
            return None, None
        return sess, crumb
    except Exception:
        return None, None


def parse_occ_suffix(option_symbol):
    """Parse trailing YYMMDD+C/P+8-digit-strike. Returns (expiration, kind, strike) or None."""
    try:
        m = OCC_SUFFIX.search(str(option_symbol or ""))
        if not m:
            return None
        yymmdd, cp, strike8 = m.group(1), m.group(2), m.group(3)
        expiration = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
        datetime.strptime(expiration, "%Y-%m-%d")
        return expiration, ("call" if cp == "C" else "put"), int(strike8) / 1000.0
    except Exception:
        return None


def cboe_fetch():
    """GET CBOE delayed IWM.json. Returns (underlying, options_list) or (None, None)."""
    try:
        r = requests.get(CBOE_URL, headers=USER_AGENT, timeout=20)
        if r.status_code != 200:
            return None, None
        data = (r.json().get("data") or {})
        options = data.get("options") or []
        try:
            underlying = float(data.get("current_price"))
        except Exception:
            underlying = None
        if not options:
            return underlying, None
        return underlying, options
    except Exception:
        return None, None


def normalize_cboe(options, underlying, received, source="cboe-delayed"):
    """Normalize flat CBOE option list -> per-expiry front-2 ATM+-10 records."""
    grouped = {}
    for o in options or []:
        try:
            parsed = parse_occ_suffix(o.get("option"))
            if not parsed:
                continue
            expiration, kind, strike = parsed
            grouped.setdefault(expiration, []).append((o, kind, strike))
        except Exception:
            continue
    if not grouped:
        return []
    if underlying is None:
        try:
            all_strikes = sorted(s for _, _, s in (v for vs in grouped.values() for v in vs))
            underlying = all_strikes[len(all_strikes) // 2]
        except Exception:
            pass
    out = []
    for expiration in sorted(grouped)[:2]:
        recs = []
        for o, kind, strike in grouped[expiration]:
            try:
                recs.append(make_record(
                    source, expiration, strike, kind, o.get("bid"), o.get("ask"),
                    o.get("last_trade_price"), o.get("volume"), o.get("open_interest"),
                    o.get("iv"), underlying, received,
                ))
            except Exception:
                continue
        out += filter_atm(recs, underlying)
    return out


def make_record(source, expiration, strike, option_type, bid, ask, last, volume, open_interest, iv, underlying, received):
    return {
        "source": source,
        "symbol": SYMBOL,
        "expiration": expiration,
        "strike": strike,
        "option_type": option_type,
        "bid": bid,
        "ask": ask,
        "last": last,
        "volume": volume,
        "open_interest": open_interest,
        "iv": iv,
        "underlying": underlying,
        "quote_time_utc": received,
        "received_at_utc": received,
        "status": "ok",
    }


def normalize_tradier(options, underlying, expiration, received, source):
    out = []
    for o in options or []:
        try:
            kind = str(o.get("option_type", "")).lower()
            if kind not in ("call", "put"):
                continue
            greeks = o.get("greeks") or {}
            out.append(make_record(
                source, str(o.get("expiration_date", expiration))[:10], float(o.get("strike")), kind,
                o.get("bid"), o.get("ask"), o.get("last"), o.get("volume"), o.get("open_interest"),
                greeks.get("mid_iv") or greeks.get("smv_vol"), underlying, received,
            ))
        except Exception:
            continue
    return out


def normalize_yahoo(items, underlying, expiration, received, source):
    out = []
    for o in items or []:
        try:
            inferred = "call" if str(o.get("contractSymbol", "")).split("IWM")[-1][8:9] == "C" else "put"
            kind = o.get("putCall") or inferred
            if kind not in ("call", "put"):
                continue
            out.append(make_record(
                source, expiration, float(o.get("strike")), kind, o.get("bid"), o.get("ask"),
                o.get("lastPrice"), o.get("volume"), o.get("openInterest"),
                o.get("impliedVolatility"), underlying, received,
            ))
        except Exception:
            continue
    return out


def filter_atm(records, underlying, width=10):
    if underlying is None:
        try:
            underlying = sorted(r["strike"] for r in records)[len(records) // 2]
        except Exception:
            return records
    keep = set(sorted({r["strike"] for r in records}, key=lambda s: abs(s - underlying))[:2 * width + 1])
    return [r for r in records if r["strike"] in keep]


def seen_keys(path):
    keys = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("expiration") is not None:
                    keys.add(f"{row.get('expiration')}|{row.get('strike')}|{row.get('option_type')}")
            except Exception:
                pass
    return keys


def median_strike(options):
    try:
        return sorted(float(o.get("strike", 0)) for o in options)[len(options) // 2]
    except Exception:
        return None


def dedup_snapshot(records):
    seen = set()
    out = []
    for row in records or []:
        try:
            key = f"{row.get('expiration')}|{row.get('strike')}|{row.get('option_type')}"
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        except Exception:
            continue
    return out


def collect():
    received = now().strftime(TIMESTAMP_FORMAT)
    records = []
    headers = tradier_headers()
    if headers:
        try:
            expirations = tradier_expirations(headers)
            if expirations:
                underlying = tradier_quote(headers)
                for expiration in expirations[:2]:
                    options = tradier_chain(expiration, headers)
                    if not options:
                        continue
                    if underlying is None:
                        underlying = median_strike(options)
                    records += filter_atm(
                        normalize_tradier(options, underlying, expiration, received, "tradier-delayed"),
                        underlying,
                    )
        except Exception:
            records = []
    if not records:
        try:
            underlying, options = cboe_fetch()
            if options:
                if underlying is None:
                    try:
                        fallback = [o for o in options if isinstance(o, dict)]
                        underlying = median_strike(
                            [{"strike": parse_occ_suffix(o.get("option"))[2]}
                             for o in fallback if parse_occ_suffix(o.get("option"))]
                        )
                    except Exception:
                        underlying = None
                records = normalize_cboe(options, underlying, received)
        except Exception:
            records = []
    if not records:
        try:
            sess, crumb = yahoo_session_with_crumb()
            first = yahoo_fetch(sess=sess, crumb=crumb)
            if first is None and (sess is not None or crumb is not None):
                first = yahoo_fetch()
            if first:
                underlying = (first.get("quote") or {}).get("regularMarketPrice")
                epochs = (first.get("expirationDates") or [])[:2]
                chains = [first.get("options", [{}])[0]]
                if len(epochs) > 1:
                    nxt = yahoo_fetch(epochs[1], sess=sess, crumb=crumb)
                    if nxt is None:
                        nxt = yahoo_fetch(epochs[1])
                    if nxt:
                        chains.append((nxt.get("options") or [{}])[0])
                for i, chain in enumerate(chains):
                    if i >= len(epochs):
                        continue
                    expiration = datetime.fromtimestamp(epochs[i], tz=timezone.utc).strftime("%Y-%m-%d")
                    for o in chain.get("calls") or []:
                        o["putCall"] = "call"
                    for o in chain.get("puts") or []:
                        o["putCall"] = "put"
                    records += filter_atm(
                        normalize_yahoo(
                            (chain.get("calls") or []) + (chain.get("puts") or []),
                            underlying, expiration, received, "yahoo-delayed",
                        ),
                        underlying,
                    )
        except Exception:
            records = []
    return dedup_snapshot(records), received


def save(records, received):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{received[:10]}.jsonl"
    known = seen_keys(path)
    if not records:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "source": "none", "symbol": SYMBOL, "expiration": None, "strike": None,
                "option_type": None, "bid": None, "ask": None, "last": None, "volume": None,
                "open_interest": None, "iv": None, "underlying": None, "quote_time_utc": None,
                "received_at_utc": received, "status": "delayed",
            }) + "\n")
        return path, 0
    written = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in records:
            key = f"{row['expiration']}|{row['strike']}|{row['option_type']}"
            if key in known:
                continue
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            known.add(key)
            written += 1
    return path, written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Collect IWM option chain (front-2 ATM+-10) into data/IWM_CHAIN/"
    )
    parser.add_argument("--loop", action="store_true", help="repeat every 15min")
    args = parser.parse_args(argv)
    while True:
        records, received = collect()
        path, written = save(records, received)
        source = records[0]["source"] if records else "none"
        print(f"{received} src={source} saved={written} file={path.name}")
        if not args.loop:
            break
        time.sleep(900)


if __name__ == "__main__":
    main()
