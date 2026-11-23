"""XLK (Technology Select Sector SPDR) 5-minute bar collector — Yahoo Finance public chart.

Source: https://query1.finance.yahoo.com/v8/finance/chart/XLK?range=5d&interval=5m
Endpoint: GET https://query1.finance.yahoo.com/v8/finance/chart/XLK
  (fallback https://query2.finance.yahoo.com/v8/finance/chart/XLK)
  params: period1/period2 (unix) or range, interval=5m, includePrePost=false.
Auth: none (keyless public chart). Requires User-Agent header.
Default run: backfill last 20 trading days (within Yahoo 5m ~60d window).
`--loop`: ET regular session (09:30-16:00, Mon-Fri), every 5 min, completed bars only.
Saves Path(__file__).parent/"data"/"XLK"/YYYY-MM-DD.jsonl (ET date).
Yahoo provides no VWAP/trade-count: vwap=null, n=null, notional_proxy=c*v kept separate.
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

SYMBOL = "XLK"
SOURCE = "yahoo-chart"
TIMEFRAME = "5Min"
INTERVAL = "5m"
URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/XLK",
    "https://query2.finance.yahoo.com/v8/finance/chart/XLK",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT_DIR = Path(__file__).parent / "data" / "XLK"


def now_utc():
    return dt.datetime.now(UTC)


def now_et():
    return now_utc().astimezone(ET)


def iso_z(d):
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def fetch_bars(start, end):
    """Fetch regular-session 5m bars via Yahoo chart (period1/period2)."""
    p1, p2 = int(start.timestamp()), int(end.timestamp())
    params = {"period1": p1, "period2": p2, "interval": INTERVAL,
              "includePrePost": "false", "events": "div|split"}
    last_err = None
    for url in URLS:
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
        except Exception as e:
            last_err = f"http_error: {e}"
            continue
        if r.status_code != 200:
            last_err = f"http_{r.status_code}"
            continue
        try:
            j = r.json()
            res = (j.get("chart") or {}).get("result") or []
            res = res[0]
        except Exception as e:
            last_err = f"parse_error: {e}"
            continue
        ts = res.get("timestamp") or []
        quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        out = []
        for i, t in enumerate(ts):
            try:
                op = dt.datetime.fromtimestamp(t, UTC)
            except Exception:
                continue
            def at(k):
                try:
                    v = (quote.get(k) or [])[i]
                    return None if v is None else float(v)
                except Exception:
                    return None
            o, h, l, c = at("open"), at("high"), at("low"), at("close")
            try:
                v = (quote.get("volume") or [])[i]
                v = None if v is None else int(v)
            except Exception:
                v = None
            if o is None and h is None and l is None and c is None:
                continue
            out.append({"t": op, "o": o, "h": h, "l": l, "c": c, "v": v})
        out.sort(key=lambda b: b["t"])
        return out, "ok"
    return [], (last_err or "http_error")


def to_record(b, received):
    t = b["t"]
    etd = t.astimezone(ET).date().isoformat()
    c, v = b.get("c"), b.get("v")
    notional = round(c * v, 2) if c is not None and v is not None else None
    return etd, {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": iso_z(t),
        "bar_close_utc": iso_z(t + dt.timedelta(minutes=5)),
        "received_at_utc": received, "o": b.get("o"), "h": b.get("h"),
        "l": b.get("l"), "c": c, "v": v, "n": None,
        "vwap": None, "notional_proxy": notional, "status": "ok"}


def existing_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                j = json.loads(line)
            except Exception:
                continue
            # Only ok rows block: past error rows must not block late real bars.
            if j.get("status") == "ok" and j.get("bar_open_utc"):
                s.add(j.get("bar_open_utc"))
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
        # ponytail: append-only, history rewrite 없음 ("a" + bar_open_utc 중복제외)
        with p.open("a", encoding="utf-8") as f:
            for r in fresh:
                f.write(json.dumps(r) + "\n")
                seen.add(r["bar_open_utc"])
        print(f"saved {len(fresh)} -> {p}")


def keep_completed(bars, end):
    return [b for b in bars if b["t"] + dt.timedelta(minutes=5) <= end]


def keep_regular(bars):
    out = []
    for b in bars:
        et = b["t"].astimezone(ET)
        if et.weekday() < 5 and (dt.time(9, 30) <= et.time() < dt.time(16, 0)):
            out.append(b)
    return out


def backfill(days):
    end = now_utc()
    start = end - dt.timedelta(days=int(days * 1.5) + 10)
    received = iso_z(end)
    bars, st = fetch_bars(start, end)
    if st != "ok":
        print(f"backfill fetch failed: {st}", file=sys.stderr)
        return
    bars = keep_completed(bars, end)
    bars = keep_regular(bars)
    cutoff = now_et().date() - dt.timedelta(days=int(days * 1.5) + 10)
    recs = [to_record(b, received) for b in bars]
    sessions = sorted({d for d, _ in recs})
    keep_days = set(sessions[-days:]) if len(sessions) > days else set(sessions)
    recs = [(d, r) for d, r in recs if d in keep_days and d >= cutoff.isoformat()]
    save_grouped(recs)
    print(f"backfill: {len(recs)} bars over {len(keep_days)} sessions (source={SOURCE})")


def in_session(t):
    return t.weekday() < 5 and (dt.time(9, 30) <= t.time() < dt.time(16, 0))


def secs_to_next_5m():
    n = now_utc()
    return 300 - (n.timestamp() % 300) + 30


def loop():
    print("loop: ET regular session, 5-min completed bars only. Ctrl+C to stop.")
    while True:
        try:
            et = now_et()
            if in_session(et):
                end = now_utc()
                start = end - dt.timedelta(minutes=60)
                received = iso_z(end)
                bars, st = fetch_bars(start, end)
                if st == "ok":
                    bars = keep_regular(keep_completed(bars, end))
                    recs = [to_record(b, received) for b in bars]
                    save_grouped(recs)
                else:
                    print(f"loop fetch failed: {st}", file=sys.stderr)
                time.sleep(secs_to_next_5m())
            else:
                time.sleep(60)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
            time.sleep(60)


def main():
    ap = argparse.ArgumentParser(description="XLK 5Min collector (Yahoo public chart)")
    ap.add_argument("--loop", action="store_true", help="ET session 5-min loop")
    ap.add_argument("--days", type=int, default=20, help="trading days for one-shot")
    a = ap.parse_args()
    if a.loop:
        loop()
    else:
        backfill(a.days)


if __name__ == "__main__":
    main()
