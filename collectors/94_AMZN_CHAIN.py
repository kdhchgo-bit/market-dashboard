"""AMZN 옵션 체인 수집기 (딜레이, 근월 2개 만기 ATM±10).
원천: Tradier 우선 (https://api.tradier.com/v1/markets/options), 다음 CBOE
  (https://cdn.cboe.com/api/global/delayed_quotes/options/AMZN.json),
  마지막 Yahoo crumb 폴백 (https://query2.finance.yahoo.com/v7/finance/options/AMZN).
인증: env TRADIER_TOKEN (없으면 CBOE→Yahoo로 폴백).
용법: python 94_AMZN_CHAIN.py (1회) / python 94_AMZN_CHAIN.py --loop (15분 폴링).
저장: <this_dir>/data/AMZN_CHAIN/YYYY-MM-DD.jsonl (당일 파일, expiration+strike+type 중복금지).
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "AMZN"
OUT = Path(__file__).parent / "data" / "AMZN_CHAIN"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/AMZN.json"
YAHOO_OPT = "https://query2.finance.yahoo.com/v7/finance/options/AMZN"
YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/AMZN"
UA = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 30
LOOP_INTERVAL = 900
SPAN = 10


def utcnow():
    return datetime.now(timezone.utc)


def seen(path):
    s = set()
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                s.add((d.get("expiration"), d.get("strike"), d.get("type")))
            except Exception:
                pass
    return s


def atm_filter(opts, und):
    """opts: [{strike,...}] -> ATM 기준 ±10개씩."""
    if not opts:
        return []
    sk = sorted(opts, key=lambda o: o.get("strike") or 0)
    idx = min(range(len(sk)), key=lambda i: abs((sk[i].get("strike") or 0) - und))
    return sk[max(0, idx - SPAN):idx + SPAN + 1]


def tradier():
    tok = os.environ.get("TRADIER_TOKEN", "")
    if not tok:
        raise RuntimeError("no TRADIER_TOKEN")
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    e = requests.get("https://api.tradier.com/v1/markets/options/expirations",
                     headers=h, params={"symbol": SYMBOL}, timeout=TIMEOUT)
    e.raise_for_status()
    exps = (e.json().get("expirations") or {}).get("date") or []
    if isinstance(exps, str):
        exps = [exps]
    recs = []
    q = requests.get("https://api.tradier.com/v1/markets/quotes",
                     headers=h, params={"symbols": SYMBOL}, timeout=TIMEOUT).json()
    try:
        und = float(q["quotes"]["quote"]["last"])
    except Exception:
        und = 0.0
    for exp in exps[:2]:
        r = requests.get("https://api.tradier.com/v1/markets/options/chains", headers=h,
                         params={"symbol": SYMBOL, "expiration": exp, "greeks": "true"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        opts = (r.json().get("options") or {}).get("option") or []
        calls = [o for o in opts if o.get("option_type") == "call"]
        puts = [o for o in opts if o.get("option_type") == "put"]
        for o in atm_filter(calls, und) + atm_filter(puts, und):
            g = o.get("greeks") or {}
            recs.append({"expiration": exp, "strike": o.get("strike"),
                         "type": o.get("option_type"), "bid": o.get("bid"), "ask": o.get("ask"),
                         "last": o.get("last"), "volume": o.get("volume"),
                         "openInterest": o.get("open_interest"), "iv": o.get("implied_volatility")
                         or (g.get("mid_iv") if isinstance(g, dict) else None),
                         "delta": g.get("delta") if isinstance(g, dict) else None,
                         "gamma": g.get("gamma") if isinstance(g, dict) else None})
    return recs, und, "tradier-delayed"


def cboe():
    r = requests.get(CBOE_URL, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json().get("data", {})
    und = float(data.get("current_price") or data.get("close") or 0.0)
    opts = data.get("options") or []
    groups = {}
    for o in opts:
        occ = str(o.get("option") or "")
        m = re.match(r"^[A-Z]+(\d{6})([CP])(\d{8})", occ)
        if not m:
            continue
        yymmdd, cp, strike8 = m.group(1), m.group(2), m.group(3)
        exp = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
        typ = "call" if cp == "C" else "put"
        try:
            strike = int(strike8) / 1000.0
        except Exception:
            continue
        rec = {"expiration": exp, "strike": strike,
               "type": typ, "bid": o.get("bid"), "ask": o.get("ask"),
               "last": o.get("last_trade_price"), "volume": o.get("volume"),
               "openInterest": o.get("open_interest"), "iv": o.get("iv"),
               "delta": o.get("delta"), "gamma": o.get("gamma")}
        groups.setdefault(exp, []).append(rec)
    recs = []
    for exp in sorted(groups.keys())[:2]:
        calls = [o for o in groups[exp] if o.get("type") == "call"]
        puts = [o for o in groups[exp] if o.get("type") == "put"]
        for o in atm_filter(calls, und) + atm_filter(puts, und):
            recs.append(o)
    if not recs:
        raise RuntimeError("cboe empty")
    return recs, und, "cboe-delayed"


def _yahoo_session():
    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get("https://fc.yahoo.com", timeout=TIMEOUT)
    except Exception:
        pass
    crumb = ""
    try:
        r = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=TIMEOUT)
        if r.status_code == 200 and r.text.strip() and "error" not in r.text.lower():
            crumb = r.text.strip()
    except Exception:
        pass
    return s, crumb


def yahoo():
    s, crumb = _yahoo_session()
    params = {"crumb": crumb} if crumb else None
    m = s.get(YAHOO_CHART, headers=UA, params=params, timeout=TIMEOUT).json()
    meta = m["chart"]["result"][0]["meta"]
    und = float(meta.get("regularMarketPrice") or 0.0)
    base = s.get(YAHOO_OPT, headers=UA, params=params,
                 timeout=TIMEOUT).json()["optionChain"]["result"][0]
    exps = base.get("expirationDates") or []
    recs = []
    for ts in exps[:2]:
        if ts == (base.get("options") or [{}])[0].get("expirationDate"):
            chain = base
        else:
            p2 = dict(params) if params else {}
            p2["date"] = ts
            j = s.get(YAHOO_OPT, headers=UA, params=p2, timeout=TIMEOUT).json()
            chain = j["optionChain"]["result"][0]
        exp = (chain.get("options") or [{}])[0].get("expirationDate")
        try:
            exp = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            exp = str(exp)
        for typ in ("calls", "puts"):
            opts = [{"strike": o.get("strike"), "bid": o.get("bid"), "ask": o.get("ask"),
                     "last": o.get("lastPrice"), "volume": o.get("volume"),
                     "openInterest": o.get("openInterest"), "iv": o.get("impliedVolatility"),
                     "type": typ[:-1]} for o in (chain.get("options") or [{}])[0].get(typ) or []]
            for o in atm_filter(opts, und):
                o["expiration"] = exp
                recs.append(o)
    return recs, und, "yahoo-delayed"


def save(recs, und, source):
    now = utcnow()
    path = OUT / f"{now.strftime('%Y-%m-%d')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    s = seen(path)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for o in recs:
            key = (o.get("expiration"), o.get("strike"), o.get("type"))
            if key in s:
                continue
            rec = {"source": source, "symbol": SYMBOL, "underlying": und, "delayed": True,
                   "received_at_utc": now.isoformat().replace("+00:00", "Z"),
                   "expiration": o.get("expiration"), "strike": o.get("strike"), "type": o.get("type"),
                   "bid": o.get("bid"), "ask": o.get("ask"), "last": o.get("last"),
                   "volume": o.get("volume"), "openInterest": o.get("openInterest"),
                   "iv": o.get("iv"), "delta": o.get("delta"), "gamma": o.get("gamma"),
                   "status": "ok"}
            f.write(json.dumps(rec) + "\n")
            s.add(key)
            n += 1
    return n, str(path)


def once():
    try:
        recs, und, src = tradier()
    except Exception as e:
        print(f"tradier fail ({e}), cboe fallback")
        try:
            recs, und, src = cboe()
        except Exception as e2:
            print(f"cboe fail ({e2}), yahoo fallback")
            recs, und, src = yahoo()
    n, p = save(recs, und, src)
    print(f"src={src} und={und} contracts={len(recs)} new={n} file={p}")


def loop():
    while True:
        try:
            once()
        except Exception as e:
            print(f"loop error: {e}")
        time.sleep(LOOP_INTERVAL)


def main():
    ap = argparse.ArgumentParser(description="AMZN 옵션 체인 수집기 (딜레이, 근월 2개 만기 ATM±10)")
    ap.add_argument("--loop", action="store_true", help="15분 간격 반복")
    a = ap.parse_args()
    if not a.loop:
        once()
        return
    loop()


if __name__ == "__main__":
    main()
