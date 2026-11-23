"""Usage: python 45_EFA.py [--loop] [--days 20] | Env: none (keyless)
Source: https://query1.finance.yahoo.com/v8/finance/chart/EFA?range=5d&interval=5m
GET https://query1.finance.yahoo.com/v8/finance/chart/EFA?period1=..&period2=..&interval=5m (fallback https://query2.finance.yahoo.com)
Symbol: EFA (iShares MSCI EAFE ETF, 선진국 ex-US).
Save: data/EFA/YYYY-MM-DD.jsonl (bar-start UTC date) e.g. {"source":"yahoo","symbol":"EFA","bar_open_utc":"2026-11-11T14:30:00Z","status":"ok"}
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = 20-trading-day backfill then incremental; drops in-progress bar.
Note: Yahoo provides no VWAP/trade-count -> vwap/n stay null; notional (c*v approx) is a separate field.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "EFA"
SRC = "yahoo"
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
UA = {"User-Agent": "Mozilla/5.0 (collector; efa-5m)"}
OUT = Path(__file__).parent / "data" / "EFA"
BAR = timedelta(minutes=5)
FMT = "%Y-%m-%dT%H:%M:%SZ"


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
    dst_y = o.year
    m1 = datetime(dst_y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(dst_y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(dst_y, 3, s2, 7, tzinfo=timezone.utc) <= o < datetime(dst_y, 11, s1, 6, tzinfo=timezone.utc)
    e = o + timedelta(hours=-4 if dst else -5)
    if e.weekday() >= 5:
        return False
    return (e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)


def fetch(start_ts, end_ts):
    for host in HOSTS:
        try:
            r = requests.get(f"{host}/v8/finance/chart/{SYM}",
                             params={"period1": start_ts, "period2": end_ts, "interval": "5m"},
                             headers=UA, timeout=25)
        except Exception:
            continue
        if r.status_code == 429:
            return None, "rate_limited"
        if r.status_code != 200:
            continue
        try:
            res = (r.json().get("chart") or {}).get("result") or []
            ts = res[0].get("timestamp") or []
            q = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
        except Exception:
            continue
        return list(zip(ts, q.get("open") or [], q.get("high") or [],
                        q.get("low") or [], q.get("close") or [], q.get("volume") or [])), None
    return None, "http_error"


def append(recs):
    # ponytail: append-only, history kept (never rewrite); skip dup bar_open_utc
    w, seen = 0, {}
    for r in recs:
        d = r["bar_open_utc"][:10]
        if d not in seen:
            p = OUT / (d + ".jsonl")
            s = set()
            if p.exists():
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        v = json.loads(ln)
                    except Exception:
                        continue
                    if v.get("bar_open_utc"):
                        s.add(v.get("bar_open_utc"))
            seen[d] = (p, s)
        p, s = seen[d]
        if r["bar_open_utc"] in s:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        s.add(r["bar_open_utc"])
        w += 1
    return w


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Collect EFA 5Min bars (yahoo, keyless) into data/EFA/*.jsonl")
    p.add_argument("--loop", action="store_true", help="repeat every 5min during ET Mon-Fri 09:30-16:00")
    p.add_argument("--days", type=int, default=20, help="trading-day backfill on first run (default: 20)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    loop, days = args.loop, max(1, args.days)
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        f = FMT
        cut = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
        base = {"source": SRC, "symbol": SYM, "bar_open_utc": cut.strftime(f), "bar_close_utc": (cut + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": None, "h": None, "l": None, "c": None, "v": None, "n": None, "vwap": None, "notional": None}
        if loop and not et_open(now):
            print(f"skip closed {now.strftime(f)}")
        else:
            mx = None
            for p in OUT.glob("*.jsonl"):
                try:
                    for ln in p.read_text(encoding="utf-8").splitlines():
                        try:
                            v = json.loads(ln)
                        except Exception:
                            continue
                        if v.get("status") != "ok":
                            continue
                        t = v.get("bar_open_utc")
                        if t and (mx is None or t > mx):
                            mx = t
                except Exception:
                    pass
            if mx:
                st = int((datetime.strptime(mx, f).replace(tzinfo=timezone.utc) + BAR).timestamp())
            else:
                cur, n = now, 0
                while n < days:
                    cur -= timedelta(days=1)
                    if cur.weekday() < 5:
                        n += 1
                st = int(cur.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            rows, err = fetch(st, int(now.timestamp()))
            if err:
                append([dict(base, status=err)])
                print(err)
            else:
                recs = []
                for t, o, h, lo, c, v in rows:
                    try:
                        bar = datetime.fromtimestamp(int(t), tz=timezone.utc)
                    except Exception:
                        continue
                    if bar.minute % 5 != 0 or bar.second != 0:
                        continue
                    if bar >= cut:
                        continue
                    if o is None and h is None and lo is None and c is None:
                        continue
                    if not et_regular(bar):
                        continue
                    try:
                        notional = round(float(c) * float(v), 2) if c is not None and v is not None else None
                    except Exception:
                        notional = None
                    try:
                        recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": bar.strftime(f), "bar_close_utc": (bar + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": o, "h": h, "l": lo, "c": c, "v": v, "n": None, "vwap": None, "notional": notional, "status": "ok"})
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
