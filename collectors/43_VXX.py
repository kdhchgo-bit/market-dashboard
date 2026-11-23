"""VXX 5분봉 수집 (Yahoo Finance 공개 차트, 키 불필요).
Usage: python 43_VXX.py [--loop]
  single: 최근 20거래일 백필 후 완성봉 저장 | --loop: ET정규장(월-금 09:30-16:00)만 5분마다 수집
  keys: 불필요 (공개 차트) | out: data/VXX/YYYY-MM-DD.jsonl (ET 일자)
  docs: https://query1.finance.yahoo.com/v8/finance/chart/VXX?range=5d&interval=5m
"""
import argparse as A
import json as J
import time as T
from datetime import datetime as D, timedelta as TD, timezone as TZ
from pathlib import Path as P

import requests as R

SYM, SRC, TF = "VXX", "yahoo", "5Min"
URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/VXX",
    "https://query2.finance.yahoo.com/v8/finance/chart/VXX",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT = P(__file__).parent / "data" / SYM
BAR = TD(minutes=5)


def et_of(u):
    y = u.year
    s = (D(y, 3, 1, tzinfo=TZ.utc) + TD(days=(6 - D(y, 3, 1, tzinfo=TZ.utc).weekday()) % 7 + 7)).replace(hour=7)
    e = (D(y, 11, 1, tzinfo=TZ.utc) + TD(days=(6 - D(y, 11, 1, tzinfo=TZ.utc).weekday()) % 7)).replace(hour=6)
    return u + (TD(hours=-4) if s <= u < e else TD(hours=-5))


def is_open(u):
    e = et_of(u)
    if e.weekday() >= 5:
        return False
    t = TD(hours=e.hour, minutes=e.minute)
    return TD(hours=9, minutes=30) <= t < TD(hours=16)


def fetch(p1, p2):
    q = {"period1": int(p1), "period2": int(p2), "interval": "5m",
         "includePrePost": "false", "events": "div|split"}
    last = "http_error"
    for base in URLS:
        try:
            r = R.get(base, headers=UA, params=q, timeout=20)
        except Exception:
            last = "delayed"
            continue
        if r.status_code == 429:
            return None, "rate_limited"
        if r.status_code != 200:
            last = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            last = "http_error"
            continue
        ch = (j.get("chart") or {})
        if ch.get("error"):
            last = "http_error"
            continue
        res = (ch.get("result") or [None])[0]
        if not res:
            last = "http_error"
            continue
        ts = res.get("timestamp") or []
        qq = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        if not ts:
            last = "delayed"
            continue
        return (ts, qq), None
    return None, last


def save(payload, now, st_epoch):
    f = "%Y-%m-%dT%H:%M:%SZ"
    recv = now.strftime(f)
    cut = now.replace(second=0, microsecond=0) - TD(minutes=now.minute % 5)
    ts, qq = payload
    oo, hh, ll, cc, vv = (qq.get("open") or []), (qq.get("high") or []), \
        (qq.get("low") or []), (qq.get("close") or []), (qq.get("volume") or [])
    grp = {}
    for i, t in enumerate(ts):
        try:
            o = D.fromtimestamp(int(t), tz=TZ.utc)
        except Exception:
            continue
        if int(t) < int(st_epoch):
            continue
        if o >= cut:
            continue
        e = et_of(o)
        if e.weekday() >= 5 or not (TD(hours=9, minutes=30) <= TD(hours=e.hour, minutes=e.minute) < TD(hours=16)):
            continue
        g = lambda a: (a[i] if i < len(a) else None)
        bo, bh, bl, bc, bv = g(oo), g(hh), g(ll), g(cc), g(vv)
        if bo is None or bh is None or bl is None or bc is None:
            continue
        try:
            bv = int(bv) if bv is not None else None
        except Exception:
            bv = None
        notional = round(((bh + bl + bc) / 3.0) * bv, 2) if bv is not None else None
        grp.setdefault(e.strftime("%Y-%m-%d"), []).append(
            (o, {"o": bo, "h": bh, "l": bl, "c": bc, "v": bv, "notional": notional}))
    n = 0
    OUT.mkdir(parents=True, exist_ok=True)
    for day, rows in grp.items():
        p = OUT / f"{day}.jsonl"
        seen = set()
        if p.exists():
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    d = J.loads(ln)
                except Exception:
                    continue
                if d.get("status") == "ok" and d.get("o") is not None:
                    seen.add(d.get("bar_open_utc"))
        with p.open("a", encoding="utf-8") as fh:
            for o, b in sorted(rows):
                k = o.strftime(f)
                if k in seen:
                    continue
                fh.write(J.dumps({"source": SRC, "symbol": SYM, "bar_open_utc": k,
                                  "bar_close_utc": (o + BAR).strftime(f), "received_at_utc": recv,
                                  "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
                                  "v": b["v"], "n": None, "vwap": None,
                                  "notional": b["notional"],
                                  "status": "ok"}, separators=(",", ":")) + "\n")
                seen.add(k)
                n += 1
    return n


def mark(status, now):
    OUT.mkdir(parents=True, exist_ok=True)
    f = "%Y-%m-%dT%H:%M:%SZ"
    cut = now.replace(second=0, microsecond=0) - TD(minutes=now.minute % 5)
    base = {"source": SRC, "symbol": SYM, "bar_open_utc": cut.strftime(f),
            "bar_close_utc": (cut + BAR).strftime(f), "received_at_utc": now.strftime(f),
            "o": None, "h": None, "l": None, "c": None, "v": None, "n": None,
            "vwap": None, "notional": None, "status": status}
    p = OUT / f"{et_of(now).strftime('%Y-%m-%d')}.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        fh.write(J.dumps(base, separators=(",", ":")) + "\n")


def backfill_start(now, days=20):
    cur, n = now, 0
    while n < days:
        cur -= TD(days=1)
        if cur.weekday() < 5:
            n += 1
    return cur.strftime("%Y-%m-%dT00:00:00Z")


def max_ok():
    mx = None
    for p in OUT.glob("*.jsonl"):
        try:
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    d = J.loads(ln)
                except Exception:
                    continue
                if d.get("status") != "ok" or d.get("o") is None:
                    continue
                t = d.get("bar_open_utc")
                if t and (mx is None or t > mx):
                    mx = t
        except Exception:
            pass
    return mx


def once(days=20):
    now = D.now(TZ.utc).replace(microsecond=0)
    if not is_open(now):
        mark("closed", now)
        return print("closed")
    mx = max_ok()
    if mx:
        st = (D.strptime(mx, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=TZ.utc) + BAR)
    else:
        st = D.strptime(backfill_start(now, days), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=TZ.utc)
    payload, err = fetch(st.timestamp(), now.timestamp())
    if err:
        mark(err, now)
        return print(err)
    n = save(payload, now, st.timestamp())
    if n:
        print(f"ok +{n}")
    else:
        mark("delayed", now)
        print("delayed")


def main(argv=None):
    ap = A.ArgumentParser(description="VXX 5Min collector (Yahoo chart, keyless)")
    ap.add_argument("--loop", action="store_true", help="ET regular hours, collect every 5min")
    ap.add_argument("--days", type=int, default=20, help="backfill trading days (default: 20)")
    a = ap.parse_args(argv)
    if not a.loop:
        return once(a.days)
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = D.now(TZ.utc).replace(microsecond=0)
        if not is_open(now):
            print(f"skip closed {now:%Y-%m-%dT%H:%M:%SZ}")
        else:
            once(a.days)
        T.sleep(300)


if __name__ == "__main__":
    main()
