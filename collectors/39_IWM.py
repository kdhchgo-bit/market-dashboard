"""Usage: python 39_IWM.py [--loop] [--days 20] | No key required
Source: https://query1.finance.yahoo.com/v8/finance/chart/IWM?range=5d&interval=5m (Yahoo Finance public chart, no key)
Fallback: https://query2.finance.yahoo.com/v8/finance/chart/IWM
Save: data/IWM/YYYY-MM-DD.jsonl (bar-start UTC date) e.g. {"source":"yahoo-chart-5m","symbol":"IWM","bar_open_utc":"2026-11-11T14:30:00Z","status":"ok"}
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = 20-trading-day backfill then incremental; drops in-progress bar.
Symbol: IWM (iShares Russell 2000 ETF, US small-cap benchmark).
Note: Yahoo provides no VWAP/trade-count -> vwap=null, n=null preserved; notional approx kept in turnover_approx=c*v.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "IWM"
SRC = "yahoo-chart-5m"
URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/IWM",
    "https://query2.finance.yahoo.com/v8/finance/chart/IWM",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT = Path(__file__).parent / "data" / "IWM"
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


def utc_to_et(dt):
    y = dt.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(y, 3, s2, 7, tzinfo=timezone.utc) <= dt < datetime(y, 11, s1, 6, tzinfo=timezone.utc)
    return dt + timedelta(hours=-4 if dst else -5)


def fetch_yahoo(st_unix, end_unix, backfill=False):
    """Return (timestamps, quote_dict) or (None, err_status). Keyless."""
    params_period = {"period1": int(st_unix), "period2": int(end_unix), "interval": "5m",
                     "includePrePost": "false", "events": "div|split"}
    params_range = {"range": ("1mo" if backfill else "5d"), "interval": "5m",
                    "includePrePost": "false", "events": "div|split"}
    last_err = "http_error"
    for params in (params_period, params_range):
        for url in URLS:
            try:
                r = requests.get(url, params=params, headers=UA, timeout=25)
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
            except Exception:
                last_err = "http_error"
                continue
            try:
                chart = j.get("chart") or {}
                if chart.get("error"):
                    last_err = "http_error"
                    continue
                res = (chart.get("result") or [None])[0]
                if not res:
                    last_err = "http_error"
                    continue
                ts = res.get("timestamp") or []
                quotes = (res.get("indicators") or {}).get("quote") or [{}]
                q = quotes[0] if quotes else {}
                if not ts or not q:
                    last_err = "http_error"
                    continue
                return ts, q
            except Exception:
                last_err = "http_error"
                continue
        # if period attempt failed on both hosts with a hard range error, range fallback is tried next
    return None, last_err


def max_ok():
    mx = None
    f = "%Y-%m-%dT%H:%M:%SZ"
    for p in OUT.glob("*.jsonl"):
        try:
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    j = json.loads(ln)
                except Exception:
                    continue
                if j.get("status") != "ok":
                    continue
                t = j.get("bar_open_utc")
                if t and (mx is None or t > mx):
                    mx = t
        except Exception:
            pass
    return mx


def append_ok(recs):
    w, seen = 0, {}
    for r in recs:
        d = r["bar_open_utc"][:10]
        if d not in seen:
            p = OUT / (d + ".jsonl")
            s = set()
            if p.exists():
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        j = json.loads(ln)
                    except Exception:
                        continue
                    # Only ok rows block future real bars; past error rows must not.
                    if j.get("status") == "ok" and j.get("bar_open_utc"):
                        s.add(j.get("bar_open_utc"))
                    # Note: ok rows with null OHLC are not valid; do not treat as seen.
                    if j.get("bar_open_utc") in s and j.get("o") is None and j.get("status") == "ok":
                        s.discard(j.get("bar_open_utc"))
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


def append_status(rec):
    d = (rec.get("bar_open_utc") or rec.get("received_at_utc") or "")[:10]
    p = OUT / (d + ".jsonl") if d else OUT / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return 1


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Collect IWM 5Min bars via Yahoo chart into data/IWM/ (no key)")
    p.add_argument("--loop", action="store_true", help="repeat every 5min during ET open")
    p.add_argument("--days", type=int, default=20, help="trading-day backfill on first run (default: 20)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    loop, days = args.loop, max(1, args.days)
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        f = "%Y-%m-%dT%H:%M:%SZ"
        cut = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
        base = {"source": SRC, "symbol": SYM, "bar_open_utc": cut.strftime(f), "bar_close_utc": (cut + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": None, "h": None, "l": None, "c": None, "v": None, "n": None, "vwap": None, "turnover_approx": None}
        if not et_open(now):
            if loop:
                print(f"skip closed {now.strftime(f)}")
            else:
                append_status(dict(base, status="closed"))
                print("closed")
        else:
            mx = max_ok()
            if mx:
                st_dt = datetime.strptime(mx, f).replace(tzinfo=timezone.utc) + BAR
                backfill = False
            else:
                cur, n = now, 0
                while n < days:
                    cur -= timedelta(days=1)
                    if cur.weekday() < 5:
                        n += 1
                st_dt = cur.replace(hour=0, minute=0, second=0, microsecond=0)
                backfill = True
            ts, q = fetch_yahoo(st_dt.timestamp(), now.timestamp(), backfill=backfill)
            if ts is None:
                append_status(dict(base, status=q))
                print(q)
            else:
                recs = []
                n_ts = len(ts)
                opens = q.get("open") or []
                highs = q.get("high") or []
                lows = q.get("low") or []
                closes = q.get("close") or []
                vols = q.get("volume") or []
                for i in range(min(n_ts, len(opens), len(highs), len(lows), len(closes), len(vols))):
                    try:
                        o = datetime.fromtimestamp(ts[i], tz=timezone.utc)
                    except Exception:
                        continue
                    if o >= cut:
                        continue  # 완성봉만 (진행중 봉 드롭)
                    if o < st_dt:
                        continue
                    e = utc_to_et(o)
                    if e.weekday() >= 5:
                        continue
                    if not ((e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)):
                        continue  # 정규장만
                    oo, hh, ll, cc, vv = opens[i], highs[i], lows[i], closes[i], vols[i]
                    if oo is None and hh is None and ll is None and cc is None:
                        continue
                    to = None
                    try:
                        if cc is not None and vv is not None:
                            to = round(float(cc) * float(vv), 2)
                    except Exception:
                        to = None
                    recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": o.strftime(f), "bar_close_utc": (o + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": oo, "h": hh, "l": ll, "c": cc, "v": vv, "n": None, "vwap": None, "turnover_approx": to, "status": "ok"})
                if recs:
                    print(f"ok +{append_ok(recs)}")
                else:
                    append_status(dict(base, status="delayed"))
                    print("delayed")
        if not loop:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
