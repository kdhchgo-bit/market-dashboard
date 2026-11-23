"""QQQ 5min collector. source: Yahoo Finance public chart (no key).

GET https://query1.finance.yahoo.com/v8/finance/chart/QQQ?period1=..&period2=..&interval=5m&includePrePost=false
fallback: https://query2.finance.yahoo.com/v8/finance/chart/QQQ (same params).
env: none (keyless). One-shot last 20 trading days + --loop every 5min (ET regular session only, completed bars).
Yahoo provides no VWAP/trade-count -> vwap=null, n=null; notional approx (c*v) stored separately.
Save: Path(__file__).parent/"data"/"QQQ"/YYYY-MM-DD.jsonl (ET date), no duplicates.
Docs: https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=5d&interval=5m
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "QQQ"
SOURCE = "yahoo-chart"
TIMEFRAME = "5Min"
BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/QQQ",
    "https://query2.finance.yahoo.com/v8/finance/chart/QQQ",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) qqq-collector/1.0"}
ET = ZoneInfo("America/New_York")
OUTDIR = Path(__file__).parent / "data" / "QQQ"
BAR = timedelta(minutes=5)


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def last20_start(end_utc):
    d = end_utc.astimezone(ET).date()
    got, cur = 0, d
    while got < 20:
        if cur.weekday() < 5:
            got += 1
        cur -= timedelta(days=1)
    start_et = datetime(cur.year, cur.month, cur.day, tzinfo=ET) + timedelta(days=1)
    return start_et.astimezone(timezone.utc)


def fetch(start_utc, end_utc):
    params = {
        "period1": int(start_utc.timestamp()),
        "period2": int(end_utc.timestamp()),
        "interval": "5m",
        "includePrePost": "false",
    }
    last_err = None
    for base in BASES:
        try:
            r = requests.get(base, headers=UA, params=params, timeout=30)
        except Exception as e:
            last_err = f"http_error: {e}"
            continue
        if r.status_code == 429:
            last_err = "rate_limited"
            continue
        if r.status_code != 200:
            last_err = f"http_error: {r.status_code}"
            continue
        try:
            j = r.json()
        except Exception as e:
            last_err = f"http_error: bad json {e}"
            continue
        err = (j.get("chart") or {}).get("error")
        if err:
            last_err = f"http_error: {err}"
            continue
        results = (j.get("chart") or {}).get("result") or []
        if not results:
            last_err = "delayed: empty result"
            continue
        res = results[0]
        ts = res.get("timestamp") or []
        quotes = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quotes.get("open") or []
        highs = quotes.get("high") or []
        lows = quotes.get("low") or []
        closes = quotes.get("close") or []
        vols = quotes.get("volume") or []
        out = []
        for i, t in enumerate(ts):
            try:
                op = datetime.fromtimestamp(int(t), tz=timezone.utc)
            except Exception:
                continue
            def at(arr):
                try:
                    v = arr[i]
                except IndexError:
                    return None
                return float(v) if v is not None else None
            o, h, l, c = at(opens), at(highs), at(lows), at(closes)
            try:
                v = vols[i]
                v = int(v) if v is not None else None
            except (IndexError, TypeError, ValueError):
                v = None
            if o is None and h is None and l is None and c is None:
                continue
            out.append({"t": op, "o": o, "h": h, "l": l, "c": c, "v": v})
        meta = res.get("meta") or {}
        rmt = meta.get("regularMarketTime")
        if rmt:
            try:
                print(f"yahoo meta: regularMarketTime={iso(datetime.fromtimestamp(int(rmt), tz=timezone.utc))} gmtoffset={meta.get('gmtoffset')} tz={meta.get('exchangeTimezoneName')}")
            except Exception:
                pass
        return out, "ok"
    return [], last_err or "http_error"


def in_session(now_utc):
    e = now_utc.astimezone(ET)
    if e.weekday() >= 5:
        return False
    return datetime(e.year, e.month, e.day, 9, 30, tzinfo=ET) <= e < datetime(e.year, e.month, e.day, 16, 0, tzinfo=ET)


def load_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                k = json.loads(line).get("bar_open_utc")
            except Exception:
                continue
            if k:  # past error rows (None) must not block late real bars
                s.add(k)
    return s


def save(bars, now_utc):
    groups = {}
    for b in bars:
        t = b["t"]
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t + BAR > now_utc:  # completed bars only
            continue
        e = t.astimezone(ET)
        if e.weekday() >= 5:
            continue
        hm = e.hour * 60 + e.minute
        if not (570 <= hm < 960):  # 9:30-16:00 ET open time
            continue
        c, v = b.get("c"), b.get("v")
        notion = (c * v) if (c is not None and v is not None) else None
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": iso(t),
               "bar_close_utc": iso(t + BAR),
               "received_at_utc": iso(now_utc), "o": b.get("o"), "h": b.get("h"),
               "l": b.get("l"), "c": c, "v": v, "n": None,
               "vwap": None, "notional_approx": notion, "status": "ok"}
        groups.setdefault(e.date().isoformat(), []).append(rec)
    total = 0
    for day, recs in sorted(groups.items()):
        p = OUTDIR / f"{day}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        seen = load_keys(p)
        new = [r for r in recs if r["bar_open_utc"] not in seen]
        if new:
            with open(p, "a", encoding="utf-8") as f:
                for r in new:
                    f.write(json.dumps(r) + "\n")
            print(f"{day}: +{len(new)}")
            total += len(new)
        else:
            print(f"{day}: no new")
    return total


def collect_once(days20=True):
    now = utcnow()
    end = now
    start = last20_start(end) if days20 else now - timedelta(days=2)
    bars, status = fetch(start, end)
    if status != "ok":
        print(f"fetch status={status}", flush=True)
        return 0, status
    n = save(bars, now)
    return n, status


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--loop", action="store_true")
    o = a.parse_args()
    n, st = collect_once(True)
    print(f"once status={st} saved={n}")
    if not o.loop:
        return
    while True:
        now = utcnow()
        try:
            if in_session(now):
                n, st = collect_once(False)
                print(f"loop {now.astimezone(ET):%H:%M} ET status={st} saved={n}", flush=True)
            else:
                print("out of session, sleep")
        except Exception as e:
            print(f"loop warn: {e}")
        time.sleep(300)


if __name__ == "__main__":
    main()
