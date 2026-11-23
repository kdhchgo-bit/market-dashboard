"""Usage: python 50_FXY.py [--loop] [--days 20]
Source: https://query1.finance.yahoo.com/v8/finance/chart/FXY?range=5d&interval=5m (keyless public chart, fallback query2)
Save: data/FXY/YYYY-MM-DD.jsonl (bar-start UTC date) e.g. {"source":"yahoo-chart","symbol":"FXY","bar_open_utc":"2026-11-11T14:30:00Z","status":"ok"}
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = 20-trading-day backfill then incremental; drops in-progress bar.
Notes: Yahoo provides no VWAP/trade-count -> vwap=null, n=null; notional(c*v) is a separate approx field. Regular-session completed 5m bars only.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "FXY"
TAG = "FXY"
SRC = "yahoo-chart"
URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/FXY",
    "https://query2.finance.yahoo.com/v8/finance/chart/FXY",
]
OUT = Path(__file__).parent / "data" / "FXY"
BAR = timedelta(minutes=5)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


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


def et_regular_ok(o):
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


def yahoo_get(params):
    last_err = "http_error"
    for u in URLS:
        try:
            r = requests.get(u, headers=UA, params=params, timeout=20)
        except Exception:
            last_err = "delayed"
            continue
        if r.status_code == 429:
            return None, "rate_limited"
        if r.status_code != 200:
            last_err = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = "http_error"
            continue
        err = (j.get("chart") or {}).get("error")
        if err:
            last_err = "http_error"
            continue
        res = ((j.get("chart") or {}).get("result") or [None])[0]
        if not res:
            last_err = "http_error"
            continue
        return res, None
    return None, last_err


def to_recs(res, cut, now_f, st_floor=None):
    recs = []
    if not res:
        return recs
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    o_, h_, l_, c_, v_ = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or [], q.get("volume") or []
    for i, t in enumerate(ts):
        try:
            o = datetime.fromtimestamp(int(t), tz=timezone.utc)
        except Exception:
            continue
        o = o.replace(second=0, microsecond=0) - timedelta(minutes=o.minute % 5)
        if o >= cut:
            continue
        if st_floor is not None and o < st_floor:
            continue
        if not et_regular_ok(o):
            continue
        try:
            oo, hh, ll, cc = o_[i], h_[i], l_[i], c_[i]
        except IndexError:
            continue
        if oo is None or hh is None or ll is None or cc is None:
            continue
        try:
            vv = v_[i]
        except IndexError:
            vv = None
        notional = None
        try:
            if cc is not None and vv is not None:
                notional = round(float(cc) * float(vv), 2)
        except Exception:
            notional = None
        recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": o.strftime("%Y-%m-%dT%H:%M:%SZ"), "bar_close_utc": (o + BAR).strftime("%Y-%m-%dT%H:%M:%SZ"), "received_at_utc": now_f, "o": oo, "h": hh, "l": ll, "c": cc, "v": vv, "n": None, "vwap": None, "notional": notional, "status": "ok"})
    seen = set()
    uniq = []
    for r in recs:
        if r["bar_open_utc"] in seen:
            continue
        seen.add(r["bar_open_utc"])
        uniq.append(r)
    uniq.sort(key=lambda r: r["bar_open_utc"])
    return uniq


def append(recs):
    # ponytail: append-only, never rewrite history; ok dedup by bar, status rows by (bar,status)
    w, seen = 0, {}
    for r in recs:
        t0 = r.get("bar_open_utc")
        if not t0:
            continue
        d = t0[:10]
        if d not in seen:
            p = OUT / (d + ".jsonl")
            okset, keys = set(), set()
            if p.exists():
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        o = json.loads(ln)
                    except Exception:
                        continue
                    t = o.get("bar_open_utc")
                    if not t:
                        continue
                    keys.add((t, o.get("status")))
                    if o.get("status") == "ok":
                        okset.add(t)
            seen[d] = (p, okset, keys)
        p, okset, keys = seen[d]
        if r.get("status") == "ok":
            if r["bar_open_utc"] in okset:
                continue
        else:
            if (r["bar_open_utc"], r.get("status")) in keys:
                continue
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        if r.get("status") == "ok":
            okset.add(r["bar_open_utc"])
        keys.add((r["bar_open_utc"], r.get("status")))
        w += 1
    return w


def parse_args():
    p = argparse.ArgumentParser(description="Collect FXY 5Min bars (yahoo-chart) to data/FXY/YYYY-MM-DD.jsonl")
    p.add_argument("--loop", action="store_true", help="repeat every 5min during ET Mon-Fri 09:30-16:00")
    p.add_argument("--days", type=int, default=20, help="trading-day backfill window (default: 20)")
    return p.parse_args()


def main():
    a = parse_args()
    loop, days = a.loop, max(1, a.days)
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
            mx = None
            for p in OUT.glob("*.jsonl"):
                try:
                    for ln in p.read_text(encoding="utf-8").splitlines():
                        try:
                            o = json.loads(ln)
                        except Exception:
                            continue
                        if o.get("status") != "ok":
                            continue
                        t = o.get("bar_open_utc")
                        if t and (mx is None or t > mx):
                            mx = t
                except Exception:
                    pass
            recs, err = [], None
            if mx:
                st = datetime.strptime(mx, f).replace(tzinfo=timezone.utc) + BAR
                p1, p2 = int(st.timestamp()), int(now.timestamp())
                if p2 > p1:
                    res, err = yahoo_get({"period1": p1, "period2": p2, "interval": "5m", "includePrePost": "false"})
                    if res is not None:
                        recs = to_recs(res, cut, now.strftime(f), st_floor=st)
                        err = None
                    elif err in ("delayed", "rate_limited", "http_error"):
                        pass
                    if err or not recs:
                        res2, err2 = yahoo_get({"range": "5d", "interval": "5m", "includePrePost": "false"})
                        if res2 is not None:
                            r2 = to_recs(res2, cut, now.strftime(f), st_floor=st)
                            if r2:
                                recs, err = r2, None
                            elif not err:
                                err = err2
                        elif not err:
                            err = err2
                else:
                    err = "delayed"
            else:
                cur, n = now, 0
                while n < days:
                    cur -= timedelta(days=1)
                    if cur.weekday() < 5:
                        n += 1
                st_floor = cur.replace(hour=0, minute=0, second=0, microsecond=0)
                res, err = yahoo_get({"range": "1mo", "interval": "5m", "includePrePost": "false"})
                if res is not None:
                    recs = to_recs(res, cut, now.strftime(f), st_floor=st_floor)
                    err = None if recs else "delayed"
                if not recs and err != "rate_limited":
                    res2, err2 = yahoo_get({"range": "5d", "interval": "5m", "includePrePost": "false"})
                    if res2 is not None:
                        r2 = to_recs(res2, cut, now.strftime(f), st_floor=st_floor)
                        if r2:
                            recs, err = r2, None
                        elif err is None:
                            err = err2 if err2 else "delayed"
                    elif err is None:
                        err = err2
            if err:
                append([dict(base, status=err)])
                print(err)
            else:
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
