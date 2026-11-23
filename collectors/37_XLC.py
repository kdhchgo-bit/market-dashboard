"""XLC (Communication Services Select Sector SPDR) 5-minute bar collector — Yahoo Finance public chart (keyless).

Source: https://query1.finance.yahoo.com/v8/finance/chart/XLC?range=5d&interval=5m
No key/session. Regular-session completed 5-min OHLCV only.
Yahoo provides no VWAP/trade-count -> vwap=null, n=null; turnover approx in notional_est.
Default run: backfill last 20 trading days within provider range. `--loop`: ET regular session
(09:30-16:00, Mon-Fri), every 5 min, completed bars only.
Saves Path(__file__).parent/"data"/"XLC"/YYYY-MM-DD.jsonl (ET date).
Stdlib + requests only, no classes.
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = dt.timezone(dt.timedelta(hours=-5), "ET")
UTC = dt.timezone.utc

SYMBOL = "XLC"
SOURCE = "yahoo-chart"
BASE_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/XLC",
    "https://query2.finance.yahoo.com/v8/finance/chart/XLC",
]
INTERVAL = "5m"
OUT_DIR = Path(__file__).parent / "data" / "XLC"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def now_utc():
    return dt.datetime.now(UTC)


def now_et():
    return now_utc().astimezone(ET)


def iso_z(d):
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def yahoo_get(params):
    last = None
    for base in BASE_URLS:
        try:
            r = requests.get(base, headers=UA, params=params, timeout=30)
        except Exception as e:
            last = e
            continue
        if r.status_code == 429:
            last = RuntimeError("http 429 rate_limited")
            time.sleep(2)
            continue
        if r.status_code != 200:
            last = RuntimeError(f"http {r.status_code}: {r.text[:120]}")
            continue
        try:
            return r.json()
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"yahoo fetch failed: {last}")


def parse_chart(j):
    try:
        res = (j.get("chart") or {}).get("result") or []
        r0 = res[0]
    except Exception:
        return []
    ts = r0.get("timestamp") or []
    try:
        q = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    except Exception:
        q = {}
    o = q.get("open") or []
    h = q.get("high") or []
    l = q.get("low") or []
    c = q.get("close") or []
    v = q.get("volume") or []
    bars = []
    for i, t in enumerate(ts):
        try:
            bt = dt.datetime.fromtimestamp(int(t), tz=UTC)
        except Exception:
            continue
        def g(seq):
            try:
                return seq[i]
            except Exception:
                return None
        bars.append({"t": bt, "o": g(o), "h": g(h), "l": g(l), "c": g(c), "v": g(v)})
    return bars


def fetch_bars(start, end):
    """Fetch 5m bars in [start, end); chunked to stay within Yahoo limits."""
    out = []
    chunk = dt.timedelta(days=7)
    s = start
    while s < end:
        e = min(s + chunk, end)
        params = {"period1": int(s.timestamp()), "period2": int(e.timestamp()),
                  "interval": INTERVAL, "includePrePost": "false"}
        j = yahoo_get(params)
        out.extend(parse_chart(j))
        s = e
    return out


def is_regular(bt):
    et = bt.astimezone(ET)
    if et.weekday() >= 5:
        return False
    return dt.time(9, 30) <= et.time() < dt.time(16, 0)


def completed_only(bars, end):
    return [b for b in bars if b["t"] + dt.timedelta(minutes=5) <= end and is_regular(b["t"])]


def turnover_est(o, h, l, c, v):
    try:
        if v is None:
            return None
        px = None
        vals = [x for x in (h, l, c) if x is not None]
        if len(vals) == 3:
            px = sum(vals) / 3.0
        elif c is not None:
            px = c
        elif o is not None:
            px = o
        if px is None:
            return None
        return round(float(px) * float(v), 2)
    except Exception:
        return None


def to_record(b, received):
    t = b["t"]
    etd = t.astimezone(ET).date().isoformat()
    return etd, {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": iso_z(t),
        "received_at_utc": received, "o": b.get("o"), "h": b.get("h"),
        "l": b.get("l"), "c": b.get("c"), "v": b.get("v"), "n": None,
        "vwap": None, "notional_est": turnover_est(b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v")),
        "status": "ok"}


def existing_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("bar_open_utc")
            if not k or r.get("status") != "ok" or r.get("o") is None:
                continue
            s.add(k)
    return s


def save_grouped(records):
    groups = {}
    for day, rec in records:
        groups.setdefault(day, []).append(rec)
    for day, recs in groups.items():
        p = OUT_DIR / f"{day}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        seen = existing_keys(p)
        fresh = [r for r in recs if r["bar_open_utc"] not in seen]
        if not fresh:
            continue
        with p.open("a", encoding="utf-8") as f:
            for r in fresh:
                f.write(json.dumps(r) + "\n")
                seen.add(r["bar_open_utc"])
        print(f"saved {len(fresh)} -> {p}")


def backfill(days, _headers=None):
    end = now_utc()
    start = end - dt.timedelta(days=int(days * 1.5) + 10)
    received = iso_z(end)
    bars = completed_only(fetch_bars(start, end), end)
    bars = [b for b in bars if b.get("o") is not None and b.get("c") is not None]
    recs = [to_record(b, received) for b in bars]
    sessions = sorted({d for d, _ in recs})
    keep_days = set(sessions[-days:]) if len(sessions) > days else set(sessions)
    cutoff = now_et().date() - dt.timedelta(days=int(days * 1.5) + 10)
    recs = [(d, r) for d, r in recs if d in keep_days and d >= cutoff.isoformat()]
    save_grouped(recs)
    print(f"backfill: {len(recs)} bars over {len(keep_days)} sessions")


def in_session(t):
    return t.weekday() < 5 and (dt.time(9, 30) <= t.time() < dt.time(16, 0))


def secs_to_next_5m():
    n = now_utc()
    return 300 - (n.timestamp() % 300) + 30


def loop(_headers=None):
    print("loop: ET regular session, 5-min completed bars only. Ctrl+C to stop.")
    while True:
        try:
            et = now_et()
            if in_session(et):
                end = now_utc()
                start = end - dt.timedelta(minutes=30)
                received = iso_z(end)
                bars = completed_only(fetch_bars(start, end), end)
                recs = [to_record(b, received) for b in bars
                        if b.get("o") is not None and b.get("c") is not None]
                save_grouped(recs)
                time.sleep(secs_to_next_5m())
            else:
                time.sleep(60)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
            time.sleep(60)


def main():
    ap = argparse.ArgumentParser(description="XLC 5Min collector (Yahoo chart, keyless)")
    ap.add_argument("--loop", action="store_true", help="ET session 5-min loop")
    ap.add_argument("--days", type=int, default=20, help="trading days for one-shot")
    a = ap.parse_args()
    if a.loop:
        loop()
    else:
        backfill(a.days)


if __name__ == "__main__":
    main()
