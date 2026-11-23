"""XLY (Consumer Discretionary Select Sector SPDR) 5-minute bar collector — Yahoo Finance public chart (no key).

Source: https://query1.finance.yahoo.com/v8/finance/chart/XLY?range=5d&interval=5m
Endpoint: GET https://query1.finance.yahoo.com/v8/finance/chart/XLY?range={range}&interval=5m&includePrePost=false&events=div%7Csplit
Auth: none (keyless). Fallback host query2.finance.yahoo.com.
Default run: backfill last 20 trading days within provider range (5m up to ~60d). `--loop`: ET regular session (09:30-16:00, Mon-Fri),
every 5 min, completed bars only. Saves Path(__file__).parent/"data"/"XLY"/YYYY-MM-DD.jsonl (ET date).
Yahoo provides no VWAP/trade-count: vwap=null, n=null preserved; notional (typical price x volume) stored separately as notional_proxy.
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

SYMBOL = "XLY"
TAG = "XLY"
SOURCE = "yahoo-chart"
INTERVAL = "5m"
OUT_DIR = Path(__file__).parent / "data" / "XLY"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def now_utc():
    return dt.datetime.now(UTC)


def now_et():
    return now_utc().astimezone(ET)


def iso_z(d):
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def range_for_days(days):
    try:
        days = int(days)
    except Exception:
        days = 20
    if days <= 5:
        return "5d"
    if days <= 22:
        return "1mo"
    return "3mo"


def fetch_chart(range_str):
    params = {"range": range_str, "interval": INTERVAL,
              "includePrePost": "false", "events": "div|split"}
    last_err = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{SYMBOL}"
        try:
            r = requests.get(url, headers=UA, params=params, timeout=30)
        except Exception as e:
            last_err = f"http_error: {e}"
            continue
        if r.status_code != 200:
            last_err = f"http_{r.status_code}"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = "http_error: bad json"
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                err = (j.get("chart") or {}).get("error") or {}
                last_err = f"empty_result: {err.get('description') or err.get('code') or 'no result'}"
                continue
            return res[0], None
        except Exception as e:
            last_err = f"parse_error: {e}"
            continue
    return None, (last_err or "fetch_failed")


def to_records(result, received, end):
    out = []
    ts = result.get("timestamp") or []
    ind = result.get("indicators") or {}
    quotes = (ind.get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    vols = quotes.get("volume") or []
    for i, epoch in enumerate(ts):
        try:
            t = dt.datetime.fromtimestamp(int(epoch), tz=UTC)
        except Exception:
            continue
        if t + dt.timedelta(minutes=5) > end:  # completed bars only
            continue
        et = t.astimezone(ET)
        if et.weekday() >= 5:  # regular session filter
            continue
        if not (dt.time(9, 30) <= et.time() < dt.time(16, 0)):
            continue
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        l = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        v = vols[i] if i < len(vols) else None
        if o is None and h is None and l is None and c is None:
            continue
        etd = et.date().isoformat()
        try:
            px = (float(h) + float(l) + float(c)) / 3.0
            notional = px * float(v) if v is not None else None
        except Exception:
            notional = None
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": iso_z(t),
               "received_at_utc": received, "o": o, "h": h,
               "l": l, "c": c, "v": v, "n": None,
               "vwap": None, "notional_proxy": notional, "status": "ok"}
        out.append((etd, rec))
    return out


def existing_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("status") != "ok":  # error rows must not block late real bars
                continue
            k = d.get("bar_open_utc")
            if k:
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


def backfill(days):
    end = now_utc()
    received = iso_z(end)
    result, err = fetch_chart(range_for_days(days))
    if result is None:
        print(f"backfill failed: {err}", file=sys.stderr)
        return 0
    recs = to_records(result, received, end)
    sessions = sorted({d for d, _ in recs})
    keep_days = set(sessions[-days:]) if len(sessions) > days else set(sessions)
    recs = [(d, r) for d, r in recs if d in keep_days]
    save_grouped(recs)
    print(f"backfill: {len(recs)} bars over {len(keep_days)} sessions (source={SOURCE})")
    return len(recs)


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
                received = iso_z(end)
                result, err = fetch_chart("5d")
                if result is None:
                    print(f"loop error: {err}", file=sys.stderr)
                else:
                    recs = to_records(result, received, end)
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
    ap = argparse.ArgumentParser(description="XLY 5Min collector (Yahoo chart, no key)")
    ap.add_argument("--loop", action="store_true", help="ET session 5-min loop")
    ap.add_argument("--days", type=int, default=20, help="trading days for one-shot")
    a = ap.parse_args()
    if a.loop:
        loop()
    else:
        backfill(a.days)


if __name__ == "__main__":
    main()
