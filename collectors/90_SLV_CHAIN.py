"""Usage: python 90_SLV_CHAIN.py [--loop] [--width 10]
Source: Tradier (primary, delayed)
  https://api.tradier.com/v1/markets/options/{expirations,chains}
  else CBOE delayed quotes
  https://cdn.cboe.com/api/global/delayed_quotes/options/SLV.json
  else Yahoo (crumb session)
  https://query2.finance.yahoo.com/v7/finance/options/SLV
Scope: SLV front-2 expirations, ATM+-10 strikes (calls+puts);
  single snapshot, --loop every 15min.
Save: data/SLV_CHAIN/YYYY-MM-DD.jsonl e.g.
  {"source":"tradier-delayed","symbol":"SLV","expiry":"2026-11-21",
   "strike":28.0,"type":"call","status":"ok"}
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYM = "SLV"
OUT = Path(__file__).parent / "data" / "SLV_CHAIN"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/SLV.json"
_OPT_RE = re.compile(r"^[A-Z]+?(\d{6})([CP])(\d{8})$")


def now_s():
    return datetime.now(timezone.utc).replace(microsecond=0)


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def tradier(sym, key):
    h = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    r = requests.get("https://api.tradier.com/v1/markets/options/expirations",
                     headers=h, params={"symbol": sym}, timeout=20)
    r.raise_for_status()
    exps = ((r.json().get("expirations") or {}).get("date")) or []
    exps = sorted([e if isinstance(e, str) else e.get("date") for e in exps])[:2]
    out, und = [], None
    for e in exps:
        q = requests.get("https://api.tradier.com/v1/markets/options/chains", headers=h,
                         params={"symbol": sym, "expiration": e, "greeks": "true"}, timeout=20)
        q.raise_for_status()
        opts = ((q.json().get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        for o in opts:
            try:
                otype = str(o.get("option_type")).lower()
                g = o.get("greeks")
                out.append({"expiry": e, "strike": float(o.get("strike")),
                            "type": ("call" if otype.startswith("c")
                                     else "put"),
                            "contract": o.get("symbol"),
                            "bid": o.get("bid"), "ask": o.get("ask"),
                            "last": o.get("last"),
                            "volume": o.get("volume"),
                            "openInterest": o.get("open_interest"),
                            "iv": (g.get("mid_iv") if isinstance(g, dict)
                                   else None)})
            except Exception:
                continue
    return out, und, "tradier-delayed"


def cboe(sym=SYM):
    r = requests.get(CBOE_URL, headers=UA, timeout=20)
    r.raise_for_status()
    data = (r.json().get("data") or {})
    und = data.get("current_price")
    opts = data.get("options") or []
    if not opts:
        raise ValueError("no cboe options")
    rows = []
    for o in opts:
        try:
            code = str(o.get("option") or "")
            m = _OPT_RE.match(code)
            if not m:
                continue
            exp = datetime.strptime("20" + m.group(1), "%Y%m%d").strftime("%Y-%m-%d")
            rows.append({"expiry": exp,
                         "strike": int(m.group(3)) / 1000.0,
                         "type": ("call" if m.group(2) == "C" else "put"),
                         "contract": code,
                         "bid": o.get("bid"), "ask": o.get("ask"),
                         "last": o.get("last_trade_price"),
                         "volume": o.get("volume"),
                         "openInterest": o.get("open_interest"),
                         "iv": o.get("iv")})
        except Exception:
            continue
    if not rows:
        raise ValueError("no cboe rows parsed")
    fronts = sorted({x["expiry"] for x in rows})[:2]
    rows = [x for x in rows if x["expiry"] in fronts]
    return rows, und, "cboe-delayed"


def _yahoo_session():
    s = requests.Session()
    s.headers.update(UA)
    crumb = None
    try:
        s.get("https://fc.yahoo.com", timeout=15)
        c = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb",
                  headers={"Accept": "*/*"}, timeout=15)
        if c.ok and c.text and "{" not in c.text:
            crumb = c.text.strip()
    except Exception:
        crumb = None
    return s, crumb


def _yahoo_chain(s, crumb, sym, ts=None):
    params = dict(crumb=crumb) if crumb else {}
    if ts is not None:
        params["date"] = ts
    last_err = None
    for host in ("https://query2.finance.yahoo.com",
                 "https://query1.finance.yahoo.com"):
        try:
            r = s.get(f"{host}/v7/finance/options/{sym}",
                      params=params or None, timeout=20)
            r.raise_for_status()
            res = (r.json().get("optionChain") or {}).get("result") or []
            if res:
                return res[0]
            last_err = ValueError("no yahoo result")
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else ValueError("no yahoo result")


def yahoo(sym):
    s, crumb = _yahoo_session()
    root = _yahoo_chain(s, crumb, sym)
    out = []
    und = ((root.get("quote") or {}).get("regularMarketPrice"))
    exps = sorted(root.get("expirationDates") or [])[:2]
    first_opts = root.get("options") or [{}]
    def push(lst, typ):
        for o in lst or []:
            try:
                exp = datetime.fromtimestamp(
                    o.get("expiration"),
                    tz=timezone.utc).strftime("%Y-%m-%d")
                out.append({"expiry": exp,
                            "strike": float(o.get("strike")),
                            "type": typ,
                            "contract": o.get("contractSymbol"),
                            "bid": o.get("bid"), "ask": o.get("ask"),
                            "last": o.get("lastPrice"),
                            "volume": o.get("volume"),
                            "openInterest": o.get("openInterest"),
                            "iv": o.get("impliedVolatility")})
            except Exception:
                continue
    if exps:
        d0 = first_opts[0]
        push(d0.get("calls"), "call")
        push(d0.get("puts"), "put")
        for ts in exps[1:]:
            rr = _yahoo_chain(s, crumb, sym, ts)
            oo = (rr.get("options") or [{}])[0]
            push(oo.get("calls"), "call")
            push(oo.get("puts"), "put")
    if not out:
        raise ValueError("no yahoo rows parsed")
    return out, und, "yahoo-delayed"


def atm_slice(rows, und, width):
    if und is None:
        try:
            strikes = sorted({x["strike"] for x in rows})
            und = strikes[len(strikes) // 2]
        except Exception:
            return rows
    strikes = sorted({x["strike"] for x in rows})
    atm = min(strikes, key=lambda s: abs(s - und))
    i = strikes.index(atm)
    keep = set(strikes[max(0, i - width):i + width + 1])
    return [x for x in rows if x["strike"] in keep]


def _key(d):
    return (d.get("expiry"), d.get("strike"), d.get("type"),
            d.get("bid"), d.get("ask"), d.get("last"),
            d.get("volume"), d.get("openInterest"))


def append(recs):
    day = now_s().strftime("%Y-%m-%d")
    p = OUT / (day + ".jsonl")
    OUT.mkdir(parents=True, exist_ok=True)
    seen = set()
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(_key(json.loads(ln)))
            except Exception:
                continue
    w = 0
    with p.open("a", encoding="utf-8") as fh:
        for r in recs:
            k = _key(r)
            if k in seen:
                continue
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            seen.add(k)
            w += 1
    return w, str(p)


def snapshot(width):
    ts = now_s()
    key = os.getenv("TRADIER_API_KEY") or os.getenv("TRADIER_TOKEN")
    src, und, rows, err = "none", None, [], None
    if key:
        try:
            rows, und, src = tradier(SYM, key)
        except Exception as e:
            err = f"tradier:{type(e).__name__}"
    if not rows:
        try:
            rows, und, src = cboe(SYM)
        except Exception as e:
            err = ((err + " " if err else "") + f"cboe:{type(e).__name__}").strip()
    if not rows:
        try:
            rows, und, src = yahoo(SYM)
        except Exception as e:
            err = ((err + " " if err else "") + f"yahoo:{type(e).__name__}").strip()
    if not rows:
        rec = {"source": src, "symbol": SYM,
               "retrieved_at_utc": fmt(ts), "underlying": und,
               "status": err or "http_error"}
        return [rec], ts
    rows = atm_slice(rows, und, width)
    recs, seen = [], set()
    for x in rows:
        k = (x["expiry"], x["strike"], x["type"])
        if k in seen:
            continue
        seen.add(k)
        recs.append({"source": src, "symbol": SYM,
                     "retrieved_at_utc": fmt(ts), "underlying": und,
                     "expiry": x["expiry"], "strike": x["strike"],
                     "type": x["type"], "contract": x["contract"],
                     "bid": x["bid"], "ask": x["ask"],
                     "last": x["last"], "volume": x["volume"],
                     "openInterest": x["openInterest"], "iv": x["iv"],
                     "status": "ok"})
    return recs, ts


def main(argv=None):
    ap = argparse.ArgumentParser(description="SLV option chain (front-2, ATM+-10)")
    ap.add_argument("--loop", action="store_true", help="repeat every 15min")
    ap.add_argument("--width", type=int, default=10)
    args = ap.parse_args(argv)
    while True:
        recs, _ = snapshot(args.width)
        n, p = append(recs)
        print(f"{recs[0].get('source')} {recs[0].get('status')} +{n} -> {p}")
        if not args.loop:
            break
        time.sleep(900)


if __name__ == "__main__":
    main()
