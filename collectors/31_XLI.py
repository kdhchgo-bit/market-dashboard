"""XLI(산업재업종) 5분봉 수집 (Yahoo Finance 공개 차트, 키 불필요).
Usage: python 31_XLI.py [--days 20] [--loop]
  --days N: 최근 N거래일 백필(기본 20) | --loop: ET정규장만 5분마다 완성봉 수집
  keys: 없음(공개 차트) | out: data/XLI/YYYY-MM-DD.jsonl
  api: GET https://query1.finance.yahoo.com/v8/finance/chart/XLI?period1={s}&period2={e}&interval=5m
  docs: https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=5m
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/XLI",
    "https://query2.finance.yahoo.com/v8/finance/chart/XLI",
]
SRC, SYM, TF = "yahoo-5m", "XLI", "5Min"
OUT = Path(__file__).parent / "data" / SYM
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def et_now():
    u = datetime.now(timezone.utc)
    y = u.year
    d0 = datetime(y, 3, 1, tzinfo=timezone.utc)
    dst_s = (d0 + timedelta(days=(6 - d0.weekday()) % 7 + 7)).replace(hour=7)
    d1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    dst_e = (d1 + timedelta(days=(6 - d1.weekday()) % 7)).replace(hour=6)
    off = timedelta(hours=-4) if dst_s <= u < dst_e else timedelta(hours=-5)
    return u, u + off

def et_off(dt):
    d0 = datetime(dt.year, 3, 1, tzinfo=timezone.utc)
    s3 = (d0 + timedelta(days=(6 - d0.weekday()) % 7 + 7)).replace(hour=7)
    d1 = datetime(dt.year, 11, 1, tzinfo=timezone.utc)
    s11 = (d1 + timedelta(days=(6 - d1.weekday()) % 7)).replace(hour=6)
    return timedelta(hours=-4) if s3 <= dt < s11 else timedelta(hours=-5)

def is_regular(opex):
    if opex.weekday() >= 5:
        return False
    t = timedelta(hours=opex.hour, minutes=opex.minute)
    return timedelta(hours=9, minutes=30) <= t < timedelta(hours=16)

def is_closed(et):
    return et.weekday() >= 5 or not (timedelta(hours=9, minutes=30) <= timedelta(hours=et.hour, minutes=et.minute) < timedelta(hours=16))

def _to_epoch(s):
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None

def fetch_bars(start, end):
    p1, p2 = _to_epoch(start), _to_epoch(end)
    if p1 is None or p2 is None:
        return [], "http_error"
    last_err = "http_error"
    for base in BASES:
        q = {"period1": p1, "period2": p2, "interval": "5m", "includePrePost": "false",
             "events": "div|split"}
        try:
            r = requests.get(base, params=q, headers=UA, timeout=30)
        except Exception:
            last_err = "http_error"
            continue
        if r.status_code == 429:
            return [], "rate_limited"
        if r.status_code != 200:
            last_err = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = "http_error"
            continue
        try:
            err = (j.get("chart") or {}).get("error")
            res = ((j.get("chart") or {}).get("result") or [None])[0]
            if err or not res:
                last_err = "http_error"
                continue
            ts = res.get("timestamp") or []
            quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            o_, h_, l_, c_, v_ = (quote.get("open") or []), (quote.get("high") or []), \
                (quote.get("low") or []), (quote.get("close") or []), (quote.get("volume") or [])
            bars = []
            for i, t in enumerate(ts):
                try:
                    op = datetime.fromtimestamp(int(t), tz=timezone.utc)
                except Exception:
                    continue
                def _at(a):
                    return a[i] if i < len(a) else None
                oo, hh, ll, cc, vv = _at(o_), _at(h_), _at(l_), _at(c_), _at(v_)
                if oo is None and hh is None and ll is None and cc is None:
                    continue
                bars.append({"t": op.strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "o": oo, "h": hh, "l": ll, "c": cc, "v": vv})
            return bars, "ok"
        except Exception:
            last_err = "http_error"
            continue
    return [], last_err

def save_bars(bars, status="ok"):
    u, et = et_now()
    recv = u.strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    done = 0
    grp = {}
    for b in bars:
        try:
            op = datetime.fromisoformat(str(b["t"]).replace("Z", "+00:00"))
        except Exception:
            continue
        if op + timedelta(minutes=5) > u:
            continue
        opex = op + et_off(op)
        if not is_regular(opex):
            continue
        grp.setdefault(opex.strftime("%Y-%m-%d"), []).append((op, b))
    for day, rows in grp.items():
        fp = OUT / f"{day}.jsonl"
        seen = set()
        if fp.exists():
            for ln in fp.read_text(encoding="utf-8").splitlines():
                try:
                    k = json.loads(ln).get("bar_open_utc")
                    if k:
                        seen.add(k)
                except Exception:
                    pass
        with fp.open("a", encoding="utf-8") as f:
            for op, b in sorted(rows, key=lambda x: x[0]):
                o = op.strftime("%Y-%m-%dT%H:%M:%SZ")
                if o in seen:
                    continue
                c, v = b.get("c"), b.get("v")
                try:
                    notional = c * v if c is not None and v is not None else None
                except Exception:
                    notional = None
                f.write(json.dumps({"source": SRC, "symbol": SYM, "bar_open_utc": o,
                    "received_at_utc": recv, "o": b.get("o"), "h": b.get("h"),
                    "l": b.get("l"), "c": c, "v": v,
                    "n": None, "vwap": None, "notional": notional, "status": status}) + "\n")
                seen.add(o)
                done += 1
    if status != "ok" or not bars:
        st = status if status != "ok" else ("closed" if is_closed(et) else "delayed")
        fp = OUT / f"{et.strftime('%Y-%m-%d')}.jsonl"
        f = {"source": SRC, "symbol": SYM, "bar_open_utc": None, "received_at_utc": recv,
            "o": None, "h": None, "l": None, "c": None, "v": None, "n": None, "vwap": None,
            "notional": None, "status": st}
        with fp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(f) + "\n")
    return done

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    if not a.loop:
        u, _ = et_now()
        back = max(a.days * 7 // 5 + 2, 30) if a.days >= 20 else a.days + 4
        s = (u - timedelta(days=back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = u.strftime("%Y-%m-%dT%H:%M:%SZ")
        bars, st = fetch_bars(s, e)
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"once days~{a.days} status={st} saved={n}")
        return
    while True:
        u, et = et_now()
        if is_closed(et):
            print(f"closed {et:%Y-%m-%d %H:%M} ET skip")
            time.sleep(300)
            continue
        s = (u - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = u.strftime("%Y-%m-%dT%H:%M:%SZ")
        bars, st = fetch_bars(s, e)
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"loop {et:%H:%M} ET status={st} saved={n}")
        time.sleep(300)

if __name__ == "__main__":
    main()
