"""QQQ option chain collector (delayed, front 2 expiries ATM+-10).

Priority: Tradier (if TRADIER_API_KEY) -> CBOE delayed JSON -> Yahoo (crumb).
CBOE: https://cdn.cboe.com/api/global/delayed_quotes/options/QQQ.json (15min delayed)
Yahoo: query2 finance options/QQQ with fc.yahoo.com cookie + getcrumb (401 fix).
One-shot + --loop every 15min. Save: data/QQQ_CHAIN/YYYY-MM-DD.jsonl (ET date).
Dedup: contract|snapshot_at_utc. Existing fields/CLI preserved.
Docs: https://documentation.tradier.com/brokerage-api/markets/get-options-chain
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "QQQ"
ET = ZoneInfo("America/New_York")
OUTDIR = Path(__file__).parent / "data" / "QQQ_CHAIN"
TBASE = "https://api.tradier.com/v1/markets"
YBASE = "https://query2.finance.yahoo.com/v7/finance/options/QQQ"
YBASE1 = "https://query1.finance.yahoo.com/v7/finance/options/QQQ"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/QQQ.json"
UA = {"User-Agent": "Mozilla/5.0"}
CBOE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept": "application/json,text/plain,*/*",
           "Accept-Language": "en-US,en;q=0.9",
           "Referer": "https://www.cboe.com/",
           "Origin": "https://www.cboe.com"}


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0, second=0)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def load_keys(p):
    s = set()
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                s.add(f"{d.get('contract')}|{d.get('snapshot_at_utc')}")
            except Exception:
                pass
    return s


def tget(path, params, timeout=30):
    k = os.getenv("TRADIER_API_KEY")
    if not k:
        raise RuntimeError("no TRADIER_API_KEY")
    r = requests.get(TBASE + path, headers={"Authorization": f"Bearer {k}", "Accept": "application/json"},
                      params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def as_list(x):
    return x if isinstance(x, list) else ([x] if x else [])


def tradier(timeout=30):
    ex = tget("/options/expirations", {"symbol": SYMBOL, "includeAllRoots": "true"}, timeout)
    dates = sorted(as_list((ex.get("expirations") or {}).get("date")))
    today = datetime.now(ET).date().isoformat()
    dates = [d for d in dates if d >= today][:2]
    q = tget("/quotes", {"symbols": SYMBOL, "greeks": "false"}, timeout)
    qq = (q.get("quotes") or {}).get("quote")
    qq = as_list(qq)[0] if as_list(qq) else {}
    spot = qq.get("last") or qq.get("close")
    raws = []
    for d in dates:
        c = tget("/options/chains", {"symbol": SYMBOL, "expiration": d, "greeks": "false"}, timeout)
        raws.append((d, as_list((c.get("options") or {}).get("option"))))
    return "tradier-delayed", spot, raws


def occ_parse(sym):
    """OCC like QQQ261111C00500000 -> (2026-11-11, 500.0, call)."""
    import re
    m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d{8})$", str(sym or "").strip())
    if not m:
        return None, None, None
    yymmdd, cp, strike8 = m.group(2), m.group(3), m.group(4)
    try:
        exp = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
        datetime.strptime(exp, "%Y-%m-%d")
        strike = int(strike8) / 1000.0
        return exp, strike, ("call" if cp == "C" else "put")
    except Exception:
        return None, None, None


def cboe(timeout=30):
    """CBOE delayed quotes: single GET. QQQ=flat OCC list; SPX-like grouped also handled."""
    r = requests.get(CBOE_URL, headers=CBOE_UA, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j) if isinstance(j, dict) else {}
    spot = num(data.get("current_price") or data.get("close") or data.get("price"))
    opts = data.get("options", [])
    if not isinstance(opts, list) or not opts:
        raise RuntimeError("cboe empty")
    # grouped shape: [{expiration_date, calls:[], puts:[]}] -> sort, take 2
    if opts and isinstance(opts[0], dict) and ("calls" in opts[0] or "puts" in opts[0]):
        groups = sorted(opts, key=lambda g: str(g.get("expiration_date", "")))[:2]
        out = []
        for g in groups:
            exp = str(g.get("expiration_date", ""))[:10]
            chain = []
            for t, leg in (("call", g.get("calls") or []), ("put", g.get("puts") or [])):
                for o in leg:
                    if not isinstance(o, dict):
                        continue
                    o = dict(o)
                    o["_otype"] = t
                    chain.append(o)
            out.append((exp, chain))
        return "cboe-delayed", spot, out
    # flat OCC shape (QQQ): [{option: QQQ261111C00500000, bid, ask, ...}] -> parse symbol
    if opts and isinstance(opts[0], dict) and "option" in opts[0]:
        by_exp = {}
        for o in opts:
            if not isinstance(o, dict):
                continue
            exp, strike, typ = occ_parse(o.get("option"))
            if not exp or strike is None:
                continue
            oo = dict(o)
            oo["_otype"] = typ
            oo["_exp"] = exp
            oo["_strike"] = strike
            by_exp.setdefault(exp, []).append(oo)
        today = datetime.now(ET).date().isoformat()
        exps = sorted(e for e in by_exp if e >= today)[:2]
        if not exps:
            exps = sorted(by_exp)[:2]
        out = [(e, by_exp[e]) for e in exps]
        if not out:
            raise RuntimeError("cboe no expiries")
        return "cboe-delayed", spot, out
    # flat shape fallback: [{expiration_date, option_type/call_put, strike...}]
    flat = [o for o in opts if isinstance(o, dict)]
    exps = sorted({str(o.get("expiration_date") or o.get("expiry") or "")[:10] for o in flat if str(o.get("expiration_date") or o.get("expiry") or "")})[:2]
    out = []
    for e in exps:
        chain = []
        for o in flat:
            if str(o.get("expiration_date") or o.get("expiry") or "")[:10] != e:
                continue
            o = dict(o)
            t = str(o.get("option_type") or o.get("type") or "call").lower()
            o["_otype"] = "put" if t.startswith("p") else "call"
            chain.append(o)
        out.append((e, chain))
    if not out:
        raise RuntimeError("cboe no expiries")
    return "cboe-delayed", spot, out


def yahoo_crumb(sess, timeout=30):
    """fc.yahoo.com cookie -> getcrumb (Yahoo v7 401 fix)."""
    try:
        sess.get("https://fc.yahoo.com/", headers=UA, timeout=timeout)
    except Exception:
        pass
    r = sess.get("https://query2.finance.yahoo.com/v1/test/getcrumb", headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text.strip()


def yahoo_chain(sess, crumb, params=None, timeout=30):
    q = dict(params or {})
    if crumb:
        q["crumb"] = crumb
    for host in (YBASE, YBASE1):
        r = sess.get(host, headers=UA, params=q, timeout=timeout)
        if r.status_code == 401:
            crumb = yahoo_crumb(sess, timeout)
            q["crumb"] = crumb
            r = sess.get(host, headers=UA, params=q, timeout=timeout)
        if r.status_code == 200:
            return r.json(), crumb
    r.raise_for_status()
    return r.json(), crumb


def yahoo(timeout=30):
    sess = requests.Session()
    sess.headers.update(UA)
    crumb = yahoo_crumb(sess, timeout)
    j, crumb = yahoo_chain(sess, crumb, None, timeout)
    res = (j.get("optionChain") or {}).get("result") or []
    if not res:
        raise RuntimeError("yahoo empty")
    r0 = res[0]
    spot = (r0.get("quote") or {}).get("regularMarketPrice")
    exps = sorted(r0.get("expirationDates") or [])
    out = []
    for e in exps[:2]:
        try:
            jj, crumb = yahoo_chain(sess, crumb, {"date": e}, timeout)
        except Exception:
            continue
        rr = ((jj.get("optionChain") or {}).get("result") or [{}])[0]
        d = datetime.fromtimestamp(e, tz=timezone.utc).date().isoformat()
        chain = []
        for o in (rr.get("options") or [{}])[0:1]:
            for t, lst in (("call", o.get("calls") or []), ("put", o.get("puts") or [])):
                for c in lst:
                    c = dict(c)
                    c["_otype"] = t
                    chain.append(c)
        out.append((d, chain))
    if not out:
        raise RuntimeError("yahoo no expiries")
    return "yahoo-delayed", spot, out


def norm(src, spot, raws, snap):
    recs = []
    for exp, chain in raws:
        items = []
        for o in chain:
            if src.startswith("tradier"):
                t = str(o.get("option_type") or o.get("type") or "").lower()
                items.append({"contract": o.get("symbol"), "strike": o.get("strike"),
                              "type": "call" if t.startswith("c") else "put",
                              "bid": o.get("bid"), "ask": o.get("ask"), "last": o.get("last"),
                              "vol": o.get("volume"), "oi": o.get("open_interest"),
                              "iv": (o.get("greeks") or {}).get("mid_iv") if isinstance(o.get("greeks"), dict) else None})
            elif src.startswith("cboe"):
                strike = o.get("_strike") if o.get("_strike") is not None else (o.get("strike_price") if o.get("strike_price") is not None else o.get("strike"))
                last = o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last")
                ivv = o.get("iv") if o.get("iv") is not None else (o.get("implied_volatility") if o.get("implied_volatility") is not None else o.get("impliedVolatility"))
                contract = (o.get("option") or o.get("option_symbol") or o.get("symbol") or o.get("contract") or o.get("contractSymbol"))
                t = str(o.get("_otype") or o.get("option_type") or o.get("type") or "call").lower()
                typ = "put" if t.startswith("p") else "call"
                try:
                    skf = float(strike) if strike is not None else None
                except Exception:
                    skf = None
                if contract is None and skf is not None:
                    contract = f"{SYMBOL}-{exp}-{typ[0].upper()}-{skf:g}"
                items.append({"contract": contract, "strike": strike, "type": typ,
                              "bid": o.get("bid"), "ask": o.get("ask"), "last": last,
                              "vol": o.get("volume"), "oi": o.get("open_interest") if o.get("open_interest") is not None else o.get("openInterest"),
                              "iv": ivv})
            else:
                items.append({"contract": o.get("contractSymbol"), "strike": o.get("strike"),
                              "type": o.get("_otype") or "call",
                              "bid": o.get("bid"), "ask": o.get("ask"), "last": o.get("lastPrice"),
                              "vol": o.get("volume"), "oi": o.get("openInterest"), "iv": o.get("impliedVolatility")})
        try:
            strikes = sorted({float(i["strike"]) for i in items if i["strike"] is not None})
        except Exception:
            continue
        if not strikes or spot is None:
            keep = set(strikes)
        else:
            atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - float(spot)))
            keep = set(strikes[max(0, atm - 10):atm + 11])
        for i in items:
            try:
                sk = float(i["strike"])
            except Exception:
                continue
            if sk not in keep:
                continue
            recs.append({"source": src, "symbol": SYMBOL, "underlying": SYMBOL, "contract": i["contract"],
                         "expiration": exp, "strike": sk, "option_type": i["type"],
                         "snapshot_at_utc": iso(snap), "received_at_utc": iso(snap),
                         "underlying_price": spot, "bid": i["bid"], "ask": i["ask"],
                         "last": i["last"], "volume": i["vol"], "open_interest": i["oi"],
                         "iv": i["iv"], "is_delayed": True, "status": "ok"})
    return recs


def collect_once(timeout=30):
    now = utcnow()
    src, spot, raws = None, None, []
    errs = []
    if os.getenv("TRADIER_API_KEY"):
        try:
            src, spot, raws = tradier(timeout)
        except Exception as e:
            errs.append(f"tradier:{e}")
    try:
        if src is None:
            src, spot, raws = cboe(timeout)
    except Exception as e:
        errs.append(f"cboe:{e}")
        print(f"cboe fail ({e}), yahoo fallback", file=sys.stderr)
    if src is None:
        try:
            src, spot, raws = yahoo(timeout)
        except Exception as e:
            errs.append(f"yahoo:{e}")
            print(f"yahoo fail ({e}) [{'; '.join(errs)}]", file=sys.stderr)
            return
    recs = norm(src, spot, raws, now)
    if not recs:
        print("no contracts")
        return
    p = OUTDIR / f"{now.astimezone(ET).date().isoformat()}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = load_keys(p)
    new = [r for r in recs if f"{r['contract']}|{r['snapshot_at_utc']}" not in seen]
    if new:
        with open(p, "a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r) + "\n")
    print(f"{p.name}: +{len(new)}/{len(recs)} src={src} spot={spot}")


def main():
    a = argparse.ArgumentParser(description="QQQ option chain collector (delayed, front-2 ATM+-10)")
    a.add_argument("--loop", action="store_true", help="repeat every 15 minutes")
    a.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")
    o = a.parse_args()
    collect_once(o.timeout)
    if not o.loop:
        return
    while True:
        time.sleep(900)
        try:
            collect_once(o.timeout)
        except Exception as e:
            print(f"loop warn: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
