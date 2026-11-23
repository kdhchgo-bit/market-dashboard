"""Usage: python 46_SLV.py [--loop] [--days 20]
Source: Yahoo Finance public chart (keyless)
GET https://query1.finance.yahoo.com/v8/finance/chart/SLV?interval=5m&period1=..&period2=..&includePrePost=false
Save: data/SLV/YYYY-MM-DD.jsonl (bar-start UTC date) e.g. {"source":"yahoo-5m","symbol":"SLV","bar_open_utc":"2026-11-11T14:30:00Z","status":"ok"}
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = 20-trading-day backfill then incremental; drops in-progress bar.
Note: Yahoo provides no VWAP/trade-count, so vwap=null n=null preserved; notional(=c*v) separate field.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYM = "SLV"
SRC = "yahoo-5m"
BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/SLV",
    "https://query2.finance.yahoo.com/v8/finance/chart/SLV",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")
OUT = Path(__file__).parent / "data" / "SLV"
BAR = timedelta(minutes=5)


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


def in_session_utc(t_utc):
    try:
        t = t_utc.astimezone(ET)
    except Exception:
        return True
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return 9 * 60 + 30 <= m < 16 * 60


def fetch(start, end):
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except Exception:
        return None, "http_error"
    if s.tzinfo is None:
        s = s.replace(tzinfo=timezone.utc)
    if e.tzinfo is None:
        e = e.replace(tzinfo=timezone.utc)
    q = {"interval": "5m", "period1": int(s.timestamp()), "period2": int(e.timestamp()), "includePrePost": "false"}
    err = None
    for base in BASES:
        try:
            r = requests.get(base, headers=UA, params=q, timeout=20)
        except Exception:
            err = "delayed"
            continue
        if r.status_code == 429:
            return None, "rate_limited"
        if r.status_code != 200:
            err = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            err = "http_error"
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                err = "http_error"
                continue
            node = res[0]
            ts = node.get("timestamp") or []
            qq = ((node.get("indicators") or {}).get("quote") or [{}])[0]
            n = len(ts)
            oo = qq.get("open") or [None] * n
            hh = qq.get("high") or [None] * n
            ll = qq.get("low") or [None] * n
            cc = qq.get("close") or [None] * n
            vv = qq.get("volume") or [None] * n
            bars = []
            for i, t in enumerate(ts):
                try:
                    c = cc[i] if i < len(cc) else None
                except Exception:
                    continue
                if c is None:
                    continue
                try:
                    dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
                except Exception:
                    continue
                bars.append({"t": dt, "o": oo[i] if i < len(oo) else None, "h": hh[i] if i < len(hh) else None, "l": ll[i] if i < len(ll) else None, "c": c, "v": vv[i] if i < len(vv) else None})
            return bars, None
        except Exception:
            err = "http_error"
            continue
    return None, (err or "http_error")


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
                        j = json.loads(ln)
                    except Exception:
                        continue
                    t = j.get("bar_open_utc")
                    if not t:
                        # legacy schema compat (s/t)
                        t = j.get("t")
                        if t and len(t) >= 10 and "T" not in t:
                            continue
                    if not t:
                        continue
                    anyset.add(t)
                    if j.get("status") == "ok":
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
        okset.add(r["bar_open_utc"])
        anyset.add(r["bar_open_utc"])
        w += 1
    return w


def main(argv=None):
    ap = argparse.ArgumentParser(description="SLV 5Min collector (yahoo-5m)")
    ap.add_argument("--loop", action="store_true", help="repeat every 5min")
    ap.add_argument("--days", type=int, default=20, help="backfill trading days (default: 20)")
    args = ap.parse_args(argv)
    loop, days = args.loop, args.days
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
                            j = json.loads(ln)
                        except Exception:
                            continue
                        if j.get("status") != "ok":
                            continue
                        t = j.get("bar_open_utc") or j.get("t")
                        if t and (mx is None or t > mx):
                            mx = t
                except Exception:
                    pass
            if mx:
                try:
                    st = (datetime.strptime(mx, f).replace(tzinfo=timezone.utc) + BAR).strftime(f)
                except Exception:
                    try:
                        st = (datetime.fromisoformat(str(mx).replace("Z", "+00:00")) + BAR).strftime(f)
                    except Exception:
                        st = mx
            else:
                cur, n = now, 0
                while n < days:
                    cur -= timedelta(days=1)
                    if cur.weekday() < 5:
                        n += 1
                st = cur.strftime("%Y-%m-%dT00:00:00Z")
            bars, err = fetch(st, now.strftime(f))
            if err:
                append([dict(base, status=err)])
                print(err)
            else:
                recs = []
                for b in bars:
                    try:
                        o = b.get("t")
                        if o.tzinfo is None:
                            o = o.replace(tzinfo=timezone.utc)
                        else:
                            o = o.astimezone(timezone.utc)
                    except Exception:
                        continue
                    if o >= cut:
                        continue
                    if not in_session_utc(o):
                        continue
                    try:
                        c = b.get("c")
                        v = b.get("v")
                        notional = (c * v) if isinstance(c, (int, float)) and isinstance(v, (int, float)) else None
                        recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": o.strftime(f), "bar_close_utc": (o + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": b.get("o"), "h": b.get("h"), "l": b.get("l"), "c": c, "v": v, "n": None, "vwap": None, "notional": notional, "status": "ok"})
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
