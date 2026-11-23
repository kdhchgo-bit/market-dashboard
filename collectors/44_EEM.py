"""EEM (신흥국) 5분봉 수집 (Yahoo Finance 공개 차트, 키 불필요).
Usage: python 44_EEM.py [--loop] | Env: 없음 (키 없이 무료로 작동)
Source: https://query1.finance.yahoo.com/v8/finance/chart/EEM?range=5d&interval=5m
Save: data/EEM/YYYY-MM-DD.jsonl (bar-start UTC date) e.g. {"source":"yahoo-5m","symbol":"EEM","bar_open_utc":"2026-11-11T14:30:00Z","status":"ok"}
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = 20-trading-day backfill then incremental; drops in-progress bar.
Notes: 정규장 완성 5분봉 OHLCV만 저장. Yahoo 미제공 n/vwap는 null 유지. 거래대금 근삿값 notional=c*v 별도 필드.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "EEM"
SRC = "yahoo-5m"
HOSTS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/EEM",
    "https://query2.finance.yahoo.com/v8/finance/chart/EEM",
]
UA = {"User-Agent": "Mozilla/5.0"}
OUT = Path(__file__).parent / "data" / "EEM"
BAR = timedelta(minutes=5)


def _et_in_session(t):
    y = t.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(y, 3, s2, 7, tzinfo=timezone.utc) <= t < datetime(y, 11, s1, 6, tzinfo=timezone.utc)
    e = t + timedelta(hours=-4 if dst else -5)
    if e.weekday() >= 5:
        return False
    return (e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)


def et_open(now):
    return _et_in_session(now)


def et_regular(o):
    return _et_in_session(o)


def fetch(range_):
    last_err = "http_error"
    for base in HOSTS:
        try:
            r = requests.get(base, headers=UA, params={"range": range_, "interval": "5m"}, timeout=20)
        except Exception:
            last_err = "http_error"
            continue
        if r.status_code == 429:
            return None, "rate_limited"
        if r.status_code != 200:
            last_err = "http_error"
            continue
        try:
            j = r.json()
            res = (j.get("chart") or {}).get("result") or []
            res = res[0]
            ts = res.get("timestamp") or []
            quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        except Exception:
            last_err = "http_error"
            continue
        return ts, quote, None
    return None, last_err


def append(recs):
    w, cache = 0, {}
    for r in recs:
        d = (r["bar_open_utc"] or "")[:10]
        if d not in cache:
            p = OUT / (d + ".jsonl")
            okset, allset = set(), set()
            if p.exists():
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        jj = json.loads(ln)
                    except Exception:
                        continue
                    t = jj.get("bar_open_utc")
                    if t:
                        allset.add(t)
                        if jj.get("status") == "ok":
                            okset.add(t)
            cache[d] = (p, okset, allset)
        p, okset, allset = cache[d]
        t = r["bar_open_utc"]
        if r.get("status") == "ok":
            if t in okset:
                continue
        else:
            if t in allset:
                continue
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        allset.add(t)
        if r.get("status") == "ok":
            okset.add(t)
        w += 1
    return w


def parse_args():
    p = argparse.ArgumentParser(description="EEM 5min bars (Yahoo chart) -> data/EEM/YYYY-MM-DD.jsonl")
    p.add_argument("--loop", action="store_true", help="repeat every 5min during ET 09:30-16:00 Mon-Fri")
    p.add_argument("--days", type=int, default=20, help="backfill trading days on first run (default: 20)")
    return p.parse_args()


def max_ok():
    mx = None
    f = "%Y-%m-%dT%H:%M:%SZ"
    for p in OUT.glob("*.jsonl"):
        try:
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    jj = json.loads(ln)
                except Exception:
                    continue
                if jj.get("status") != "ok":
                    continue
                t = jj.get("bar_open_utc")
                if t and (mx is None or t > mx):
                    mx = t
        except Exception:
            pass
    return mx


def main():
    args = parse_args()
    loop, days = args.loop, max(1, args.days)
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        f = "%Y-%m-%dT%H:%M:%SZ"
        cut = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
        base = {"source": SRC, "symbol": SYM, "bar_open_utc": cut.strftime(f), "bar_close_utc": (cut + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": None, "h": None, "l": None, "c": None, "v": None, "n": None, "vwap": None, "notional": None}
        if not et_open(now):
            if loop:
                print(f"skip closed {now.strftime(f)}")
            else:
                append([dict(base, status="closed")])
                print("closed")
        else:
            mx = max_ok()
            rng = "5d" if mx else ("1mo" if days >= 15 else "5d")
            got = fetch(rng)
            if len(got) == 2:
                _, err = got
                append([dict(base, status=err)])
                print(err)
            else:
                ts, quote, _ = got
                opens = quote.get("open") or []
                highs = quote.get("high") or []
                lows = quote.get("low") or []
                closes = quote.get("close") or []
                vols = quote.get("volume") or []
                recs = []
                for i, epoch in enumerate(ts):
                    try:
                        o = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
                    except Exception:
                        continue
                    if o >= cut:
                        continue
                    if mx and o.strftime(f) <= mx:
                        continue
                    if not et_regular(o):
                        continue
                    try:
                        oo = opens[i] if i < len(opens) else None
                        hh = highs[i] if i < len(highs) else None
                        ll = lows[i] if i < len(lows) else None
                        cc = closes[i] if i < len(closes) else None
                        vv = vols[i] if i < len(vols) else None
                    except Exception:
                        continue
                    if oo is None or hh is None or ll is None or cc is None:
                        continue
                    try:
                        notional = float(cc) * float(vv) if vv is not None else None
                    except Exception:
                        notional = None
                    try:
                        recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": o.strftime(f), "bar_close_utc": (o + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": oo, "h": hh, "l": ll, "c": cc, "v": vv, "n": None, "vwap": None, "notional": notional, "status": "ok"})
                    except Exception:
                        continue
                if recs:
                    print(f"ok +{append(recs)}")
                else:
                    append([dict(base, status="delayed")])
                    print("delayed")
        if not loop:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
