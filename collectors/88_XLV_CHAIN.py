"""XLV option chain collector (delayed, front 2 expiries ATM+-10).

Tradier first (needs TRADIER_TOKEN env), then CBOE delayed
(https://cdn.cboe.com/api/global/delayed_quotes/options/XLV.json),
then Yahoo Finance with crumb (fc.yahoo.com cookie).
One-shot by default; --loop polls every 15 min. Appends JSONL to
data/XLV_CHAIN/YYYY-MM-DD.jsonl next to this file. Dedupes on
(expiry|strike|type|minute).
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYM = "XLV"
UA = {"User-Agent": "Mozilla/5.0"}
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/XLV.json"
OUT = Path(__file__).parent / "data" / "XLV_CHAIN"


def now():
    return datetime.now(timezone.utc)


def outpath(dt=None):
    dt = dt or now()
    OUT.mkdir(parents=True, exist_ok=True)
    return OUT / f"{dt:%Y-%m-%d}.jsonl"


def load_keys(p):
    ks = set()
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                ks.add((d.get("expiry"), str(d.get("strike")), d.get("type"), d.get("ts", "")[:16]))
            except Exception:
                pass
    return ks


def tradier(path, token, params):
    r = requests.get(f"https://api.tradier.com/v1{path}", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def tradier_chain(tok):
    q = tradier("/markets/quotes", tok, {"symbols": SYM})["quotes"]["quote"]
    if isinstance(q, list):
        q = q[0]
    px = float(q["last"] or q["close"])
    ex = tradier("/markets/options/expirations", tok, {"symbol": SYM})["expirations"]["date"]
    ex = ex if isinstance(ex, list) else [ex]
    rows = []
    for e in ex[:2]:
        ch = tradier("/markets/options/chains", tok, {"symbol": SYM, "expiry": e})["options"]["option"]
        ch = ch if isinstance(ch, list) else [ch]
        strikes = sorted({float(c["strike"]) for c in ch})
        atm = min(strikes, key=lambda s: abs(s - px))
        i = strikes.index(atm)
        want = set(strikes[max(0, i - 10): i + 11])
        for c in ch:
            if float(c["strike"]) in want:
                rows.append({"expiry": e, "strike": float(c["strike"]), "type": c["option_type"].upper()[:1],
                             "bid": c.get("bid"), "ask": c.get("ask"), "last": c.get("last"), "volume": c.get("volume"), "oi": c.get("open_interest")})
    return px, rows, "tradier-delayed"


def cboe_chain():
    r = requests.get(CBOE_URL, headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()["data"]
    px = float(data["current_price"])
    by_exp = {}
    for o in data.get("options", []):
        m = re.match(r"[A-Z]+(\d{6})([CP])(\d+)", o.get("option", ""))
        if not m:
            continue
        ymd, cp, sk = m.groups()
        ed = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}"
        by_exp.setdefault(ed, []).append({
            "expiry": ed,
            "strike": int(sk) / 1000.0,
            "type": cp,
            "bid": o.get("bid"),
            "ask": o.get("ask"),
            "last": o.get("last_trade_price"),
            "volume": o.get("volume"),
            "oi": o.get("open_interest"),
        })
    rows = []
    for e in sorted(by_exp)[:2]:
        grp = by_exp[e]
        strikes = sorted({c["strike"] for c in grp})
        if not strikes:
            continue
        atm = min(strikes, key=lambda s: abs(s - px))
        i = strikes.index(atm)
        want = set(strikes[max(0, i - 10): i + 11])
        rows.extend(c for c in grp if c["strike"] in want)
    if not rows:
        raise RuntimeError("cboe-empty")
    return px, rows, "cboe-delayed"


def _yahoo_session():
    s = requests.Session()
    s.headers.update(UA)
    s.get("https://fc.yahoo.com", timeout=20)
    cr = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=20)
    cr.raise_for_status()
    crumb = cr.text.strip()
    if not crumb or "Unauthorized" in crumb:
        raise RuntimeError("yahoo-crumb-fail")
    return s, crumb


def yahoo_chain():
    s, crumb = _yahoo_session()

    def _get(url, params):
        p = dict(params or {})
        p["crumb"] = crumb
        rr = s.get(url, params=p, timeout=20)
        rr.raise_for_status()
        return rr.json()

    res = _get(f"https://query2.finance.yahoo.com/v7/finance/options/{SYM}", {})["optionChain"]["result"][0]
    px = float(res["quote"]["regularMarketPrice"])
    exps = res.get("expirationDates", [])[:2]
    rows = []
    for e in exps:
        ed = datetime.fromtimestamp(e, tz=timezone.utc).strftime("%Y-%m-%d")
        o = _get(f"https://query2.finance.yahoo.com/v7/finance/options/{SYM}", {"date": e})["optionChain"]["result"][0]["options"][0]
        calls, puts = o.get("calls", []), o.get("puts", [])
        strikes = sorted({float(c["strike"]) for c in calls + puts})
        if not strikes:
            continue
        atm = min(strikes, key=lambda s: abs(s - px))
        i = strikes.index(atm)
        want = set(strikes[max(0, i - 10): i + 11])
        for c in calls:
            if float(c["strike"]) in want:
                rows.append({"expiry": ed, "strike": float(c["strike"]), "type": "C", "bid": c.get("bid"), "ask": c.get("ask"),
                             "last": c.get("lastPrice"), "volume": c.get("volume"), "oi": c.get("openInterest")})
        for c in puts:
            if float(c["strike"]) in want:
                rows.append({"expiry": ed, "strike": float(c["strike"]), "type": "P", "bid": c.get("bid"), "ask": c.get("ask"),
                             "last": c.get("lastPrice"), "volume": c.get("volume"), "oi": c.get("openInterest")})
    return px, rows, "yahoo-delayed"


def collect(keys, ts):
    tok = os.getenv("TRADIER_TOKEN", "")
    src, px, rows = "", 0.0, []
    try:
        if tok:
            px, rows, src = tradier_chain(tok)
        else:
            raise RuntimeError("no-token")
    except Exception:
        try:
            px, rows, src = cboe_chain()
        except Exception:
            px, rows, src = yahoo_chain()
    minute = ts.strftime("%Y-%m-%dT%H:%M")
    out = []
    for c in rows:
        k = (c["expiry"], str(c["strike"]), c["type"], minute)
        if k in keys:
            continue
        keys.add(k)
        out.append({"ts": ts.isoformat(), "symbol": SYM, "underlying": px, "source": src, **c})
    return out


def once():
    ts = now()
    p = outpath(ts)
    keys = load_keys(p)
    rows = collect(keys, ts)
    if rows:
        with p.open("a", encoding="utf-8") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"{p} +{len(rows)} src-ok")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="XLV chain ATM+-10 collector")
    ap.add_argument("--loop", action="store_true", help="poll every 15min")
    ap.add_argument("--interval", type=int, default=900)
    a = ap.parse_args()
    once()
    while a.loop:
        time.sleep(a.interval)
        try:
            once()
        except Exception as e:
            print(f"loop-error: {e}")


if __name__ == "__main__":
    main()
