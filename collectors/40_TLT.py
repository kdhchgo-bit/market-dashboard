"""Usage: python 40_TLT.py [--loop] [--days N]
Source: https://query1.finance.yahoo.com/v8/finance/chart/TLT?range=5d&interval=5m (keyless public chart)
Symbol: TLT (iShares 20+ Year Treasury Bond ETF, 장기채) | Save: data/TLT/YYYY-MM-DD.jsonl (bar-start UTC date)
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = 20-trading-day backfill (within Yahoo range) then incremental; drops in-progress bar; VWAP=null (source has none), turnover approx in notional.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "TLT"
SRC = "yahoo-chart"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/TLT"
OUT = Path(__file__).parent / "data" / "TLT"
BAR = timedelta(minutes=5)
UA = {"User-Agent": "Mozilla/5.0"}


def et_open(now):
    y = now.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(y, 3, s2, 7, tzinfo=timezone.utc) <= now < datetime(y, 11, s1, 6, tzinfo=timezone.utc)
    e = now + timedelta(hours=-4 if dst else -5)
    if e.weekday() >= 5:
        return False
    return (e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)


def et_regular(o):
    # o: aware UTC datetime of bar open; keep only ET Mon-Fri 09:30-16:00 regular session
    y = o.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(y, 3, s2, 7, tzinfo=timezone.utc) <= o < datetime(y, 11, s1, 6, tzinfo=timezone.utc)
    e = o + timedelta(hours=-4 if dst else -5)
    if e.weekday() >= 5:
        return False
    return (e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)


def fetch(start_unix, end_unix):
    try:
        r = requests.get(URL, headers=UA, params={"period1": int(start_unix), "period2": int(end_unix), "interval": "5m", "includePrePost": "false"}, timeout=25)
    except Exception:
        return None, "delayed"
    if r.status_code == 429:
        return None, "rate_limited"
    if r.status_code != 200:
        return None, "http_error"
    try:
        j = r.json()
    except Exception:
        return None, "http_error"
    try:
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return None, "delayed"
        res = res[0]
        ts = res.get("timestamp") or []
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        closes = q.get("close") or []
        vols = q.get("volume") or []
        out = []
        for i, t in enumerate(ts):
            try:
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                lo = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = vols[i] if i < len(vols) else None
            except Exception:
                continue
            out.append((int(t), o, h, lo, c, v))
        return out, None
    except Exception:
        return None, "http_error"


def append(recs):
    w, seen = 0, {}
    for r in recs:
        d = r["bar_open_utc"][:10]
        if d not in seen:
            p = OUT / (d + ".jsonl")
            okset, anyset = set(), set()
            if p.exists():
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    t = row.get("bar_open_utc")
                    if not t:
                        continue
                    anyset.add(t)
                    if row.get("status") == "ok":
                        okset.add(t)
            seen[d] = (p, okset, anyset)
        p, okset, anyset = seen[d]
        if r.get("status") == "ok":
            if r["bar_open_utc"] in okset:
                continue
        else:
            if r["bar_open_utc"] in anyset:
                continue
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        anyset.add(r["bar_open_utc"])
        if r.get("status") == "ok":
            okset.add(r["bar_open_utc"])
        w += 1
    return w


def parse_args():
    p = argparse.ArgumentParser(description="TLT 5Min bars collector (yahoo-chart, keyless)")
    p.add_argument("--loop", action="store_true", help="repeat every 5min during ET open")
    p.add_argument("--days", type=int, default=20, help="backfill trading days (default: 20)")
    return p.parse_args()


def max_ok():
    mx = None
    try:
        for p in OUT.glob("*.jsonl"):
            try:
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    if row.get("status") != "ok":
                        continue
                    t = row.get("bar_open_utc")
                    if t and (mx is None or t > mx):
                        mx = t
            except Exception:
                pass
    except Exception:
        pass
    return mx


def main():
    args = parse_args()
    loop = args.loop
    days = args.days
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        f = "%Y-%m-%dT%H:%M:%SZ"
        cut = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
        base = {"source": SRC, "symbol": SYM, "bar_open_utc": cut.strftime(f), "bar_close_utc": (cut + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": None, "h": None, "l": None, "c": None, "v": None, "n": None, "vwap": None, "notional": None}
        if loop and not et_open(now):
            print(f"skip closed {now.strftime(f)}")
        else:
            mx = max_ok()
            if mx:
                st = datetime.strptime(mx, f).replace(tzinfo=timezone.utc) + BAR
            else:
                cur, n = now, 0
                while n < days:
                    cur -= timedelta(days=1)
                    if cur.weekday() < 5:
                        n += 1
                st = cur.strftime("%Y-%m-%dT00:00:00Z")
                st = datetime.strptime(st, f).replace(tzinfo=timezone.utc)
            bars, err = fetch(st.timestamp(), now.timestamp())
            if err:
                if err == "delayed" and not et_open(now):
                    append([dict(base, status="closed")])
                    print("closed")
                else:
                    append([dict(base, status=err)])
                    print(err)
            else:
                recs = []
                for (t, o, h, lo, c, v) in bars:
                    if t % 300 != 0:
                        continue
                    o_dt = datetime.fromtimestamp(t, tz=timezone.utc)
                    if o_dt >= cut:
                        continue
                    if not et_regular(o_dt):
                        continue
                    if o is None and h is None and lo is None and c is None:
                        continue
                    try:
                        vv = int(v) if v is not None else None
                    except Exception:
                        vv = None
                    notional = None
                    try:
                        if vv is not None and c is not None:
                            if h is not None and lo is not None:
                                notional = round(((h + lo + c) / 3) * vv, 2)
                            else:
                                notional = round(c * vv, 2)
                    except Exception:
                        notional = None
                    recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": o_dt.strftime(f), "bar_close_utc": (o_dt + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": o, "h": h, "l": lo, "c": c, "v": vv, "n": None, "vwap": None, "notional": notional, "status": "ok"})
                if recs:
                    print(f"ok +{append(recs)}")
                else:
                    if not et_open(now):
                        append([dict(base, status="closed")])
                        print("closed")
                    else:
                        append([dict(base, status="delayed")])
                        print("delayed")
        if not loop:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
