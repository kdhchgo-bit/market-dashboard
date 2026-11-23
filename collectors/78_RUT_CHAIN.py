"""RUT 옵션체인 스냅샷(딜레이, CBOE→Yahoo ^RUT 폴백).
Usage: python 78_RUT_CHAIN.py [--loop]
out: data/RUT_CHAIN/YYYY-MM-DD.jsonl(ET일자), is_delayed=true
"""
import argparse as A, json as J, time as T
from datetime import datetime as D, timedelta as TD, timezone as TZ
from pathlib import Path as P
import requests as R

SYM, OUTN = "RUT", "RUT_CHAIN"
CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/RUT.json"
YHQ = "https://query2.finance.yahoo.com/v7/finance/options/%5ERUT"
YHQ1 = "https://query1.finance.yahoo.com/v7/finance/options/%5ERUT"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
      "Referer": "https://www.cboe.com/", "Origin": "https://www.cboe.com"}
OUT = P(__file__).parent / "data" / OUTN
_SESS = None
_CRUMB = None

def etn(u):
    y = u.year
    s = (D(y, 3, 1, tzinfo=TZ.utc) + TD(days=(6 - D(y, 3, 1, tzinfo=TZ.utc).weekday()) % 7 + 7)).replace(hour=7)
    e = (D(y, 11, 1, tzinfo=TZ.utc) + TD(days=(6 - D(y, 11, 1, tzinfo=TZ.utc).weekday()) % 7)).replace(hour=6)
    return u + (TD(hours=-4) if s <= u < e else TD(hours=-5))

def fnum(x):
    try: return float(x) if x is not None and str(x).strip() not in ("", "N/A", "-") else None
    except (ValueError, TypeError): return None

def fint(x):
    try: return int(float(x)) if x is not None and str(x).strip() not in ("", "N/A", "-") else None
    except (ValueError, TypeError): return None

def getj(url, src):
    r = R.get(url, headers=UA, timeout=30)
    if r.status_code == 429: raise RuntimeError("rate_limited")
    r.raise_for_status()
    return r.json(), src

def norm_cboe(j):
    d = (j.get("data") or j or {})
    px = fnum(d.get("close") or d.get("price") or d.get("underlying_price") or d.get("current_price"))
    rows = []
    opts = d.get("options") or d.get("data") or []
    # grouped shape: [{expiration_date, calls:[], puts:[]}] -> flatten
    if opts and isinstance(opts[0], dict) and ("calls" in opts[0] or "puts" in opts[0]):
        for g in opts:
            if not isinstance(g, dict): continue
            ex = str(g.get("expiration_date") or g.get("expiry") or "")[:10]
            for k, t in (("calls", "C"), ("puts", "P")):
                for o in (g.get(k) or []):
                    if not isinstance(o, dict): continue
                    rows.append({"expiry": ex,
                                 "strike": fnum(o.get("strike_price") if o.get("strike_price") is not None else o.get("strike")),
                                 "type": t,
                                 "bid": fnum(o.get("bid")), "ask": fnum(o.get("ask")),
                                 "last": fnum(o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last")),
                                 "volume": fint(o.get("volume")), "oi": fint(o.get("open_interest") or o.get("openInterest")),
                                 "iv": fnum(o.get("iv") if o.get("iv") is not None else o.get("implied_volatility")), "und": px})
        return rows, "cboe-delayed"
    for o in opts:
        if not isinstance(o, dict): continue
        t = str(o.get("option_type") or o.get("type") or "C").upper()
        rows.append({"expiry": str(o.get("expiration_date") or o.get("expiry") or "")[:10],
                     "strike": fnum(o.get("strike_price") or o.get("strike")), "type": "C" if t.startswith("C") else "P",
                     "bid": fnum(o.get("bid")), "ask": fnum(o.get("ask")), "last": fnum(o.get("last")),
                     "volume": fint(o.get("volume")), "oi": fint(o.get("open_interest") or o.get("openInterest")),
                     "iv": fnum(o.get("iv") or o.get("implied_volatility")), "und": px})
    return rows, "cboe-delayed"

def _sess():
    global _SESS
    if _SESS is None:
        _SESS = R.Session()
        _SESS.headers.update(UA)
    return _SESS

def _crumb(force=False):
    """Yahoo v7 401(Invalid Crumb) fix: fc.yahoo.com cookie -> getcrumb."""
    global _CRUMB
    if _CRUMB and not force:
        return _CRUMB
    s = _sess()
    try:
        s.get("https://fc.yahoo.com/", timeout=20)
    except Exception:
        pass
    r = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=20)
    if r.status_code == 429:
        raise RuntimeError("rate_limited")
    r.raise_for_status()
    _CRUMB = r.text.strip()
    if not _CRUMB:
        raise RuntimeError("http_error")
    return _CRUMB

def yahoo_get(url, params=None):
    s = _sess()
    crumb = _crumb()
    q = dict(params or {})
    if crumb:
        q["crumb"] = crumb
    r = s.get(url, params=q, timeout=30)
    if r.status_code == 429:
        raise RuntimeError("rate_limited")
    if r.status_code == 401:  # crumb expired -> refresh once
        crumb = _crumb(force=True)
        q["crumb"] = crumb
        r = s.get(url, params=q, timeout=30)
        if r.status_code == 429:
            raise RuntimeError("rate_limited")
    r.raise_for_status()
    return r.json()

def norm_yahoo_chain(opt, und):
    rows = []
    for k, t in (("calls", "C"), ("puts", "P")):
        for o in (opt.get(k) or []):
            ex = o.get("expiration")
            es = str(ex)[:10] if isinstance(ex, str) else (D.fromtimestamp(ex, TZ.utc).strftime("%Y-%m-%d") if ex else "")
            rows.append({"expiry": es, "strike": fnum(o.get("strike")), "type": t, "bid": fnum(o.get("bid")),
                         "ask": fnum(o.get("ask")), "last": fnum(o.get("lastPrice")),
                         "volume": fint(o.get("volume")), "oi": fint(o.get("openInterest")),
                         "iv": fnum(o.get("impliedVolatility")), "und": und})
    return rows

def fetch():
    cboe_limited = False
    try:
        j, src = getj(CBOE, "cboe-delayed")
        rows, src = norm_cboe(j)
        if rows: return rows, src
    except RuntimeError: cboe_limited = True  # CBOE 429 -> Yahoo 폴백 계속
    except Exception: pass
    # Yahoo crumb fallback (v7 401 fix): query2 -> query1
    try:
        j = yahoo_get(YHQ)
        res = (j.get("optionChain") or {}).get("result") or []
        if not res: return [], "http_error"
        und = fnum((res[0].get("quote") or {}).get("regularMarketPrice"))
        exps = sorted(x for x in (res[0].get("expirationDates") or []) if x)[:2]
        if not exps: return norm_yahoo_chain((res[0].get("options") or [{}])[0], und), "yahoo-delayed"
        allr = []
        for e in exps:
            try:
                je = yahoo_get(YHQ, {"date": e})
            except Exception:
                je = yahoo_get(YHQ1, {"date": e})
            rr = ((je.get("optionChain") or {}).get("result") or [{}])[0]
            dd = D.fromtimestamp(e, TZ.utc).strftime("%Y-%m-%d")
            u2 = und or fnum((rr.get("quote") or {}).get("regularMarketPrice"))
            for r in norm_yahoo_chain((rr.get("options") or [{}])[0], u2):
                r["expiry"] = r["expiry"] or dd
                allr.append(r)
        return (allr, "yahoo-delayed") if allr else ([], "delayed")
    except RuntimeError: return [], "rate_limited"
    except Exception:
        try:  # last resort: query1 host with fresh crumb
            je = yahoo_get(YHQ1)
            rr = ((je.get("optionChain") or {}).get("result") or [{}])[0]
            return norm_yahoo_chain((rr.get("options") or [{}])[0], fnum((rr.get("quote") or {}).get("regularMarketPrice"))), "yahoo-delayed"
        except RuntimeError: return [], "rate_limited"
        except Exception: return [], "http_error"

def pick(rows):
    """근월 2개 만기 x ATM±10."""
    valid = [r for r in rows if r.get("expiry") and r.get("strike") is not None]
    if not valid: return []
    und = next((r["und"] for r in valid if r.get("und")), None)
    if und is None: und = sorted(r["strike"] for r in valid)[len(valid) // 2]
    exps = sorted({r["expiry"] for r in valid})[:2]
    out = [r for e in exps for r in valid if r["expiry"] == e and r["strike"] in
           sorted({x["strike"] for x in valid if x["expiry"] == e}, key=lambda s: abs(s - und))[:21]]
    return sorted(out, key=lambda r: (r["expiry"], r["strike"] or 0, r["type"]))

def save(rows, src, status):
    u, et = D.now(TZ.utc), etn(D.now(TZ.utc))
    recv = u.strftime("%Y-%m-%dT%H:%M:%SZ")
    snap = u.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / f"{et.strftime('%Y-%m-%d')}.jsonl"
    seen = set()
    if fp.exists():
        for ln in fp.read_text(encoding="utf-8").splitlines():
            try:
                d = J.loads(ln)
                if d.get("snapshot_utc"): seen.add((d.get("expiry"), d.get("strike"), d.get("option_type"), d.get("snapshot_utc")))
            except Exception: pass
    n = 0
    with fp.open("a", encoding="utf-8") as f:
        w = lambda d: f.write(J.dumps(d, separators=(",", ":")) + "\n")
        if status == "ok":
            for r in rows:
                k = (r["expiry"], r["strike"], r["type"], snap)
                if k in seen: continue
                w({"source": src, "symbol": SYM, "expiry": r["expiry"], "strike": r["strike"],
                   "option_type": r["type"], "bid": r["bid"], "ask": r["ask"], "last": r["last"],
                   "volume": r["volume"], "open_interest": r["oi"], "iv": r["iv"], "underlying": r["und"],
                   "is_delayed": True, "snapshot_utc": snap, "received_at_utc": recv, "status": "ok"})
                seen.add(k); n += 1
        else:
            w({"source": src if src else "cboe-delayed", "symbol": SYM, "expiry": None, "strike": None,
               "option_type": None, "bid": None, "ask": None, "last": None, "volume": None,
               "open_interest": None, "iv": None, "underlying": None, "is_delayed": True,
               "snapshot_utc": snap, "received_at_utc": recv, "status": status})
    return n

def once():
    rows, src = fetch()
    if not rows:
        st = src if src != "yahoo-delayed" else "delayed"
        if src in ("rate_limited", "http_error"): st = src
        save([], src, st)
        return print(f"once status={st} saved=0")
    sel = pick(rows)
    if not sel:
        save([], src, "delayed")
        return print("once status=delayed saved=0")
    print(f"once status=ok src={src} saved={save(sel, src, 'ok')} n={len(sel)}")

def main():
    ap = A.ArgumentParser(description="RUT option chain snapshot (delayed, front2 x ATM+-10)")
    ap.add_argument("--loop", action="store_true", help="15분마다 반복")
    a = ap.parse_args()
    if not a.loop: return once()
    while True:
        try: once()
        except Exception as e: print(f"loop error {e}")
        T.sleep(900)

if __name__ == "__main__": main()
