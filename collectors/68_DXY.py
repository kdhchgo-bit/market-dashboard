"""DXY (dollar index) 5-minute collector — Yahoo chart DX-Y.NYB, no key.

Sources:
  primary:  https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=5d&interval=5m
  fallback: https://query2.finance.yahoo.com/v8/finance/chart/DX-Y.NYB (same params)

Usage:
  python 68_DXY.py                # 1-time collect (20-day backfill; intraday first)
  python 68_DXY.py --loop         # every 5 min for 24h (weekend -> status=closed)
  python 68_DXY.py --loop --interval 300 --duration 24 --days 20

Save: Path(__file__).parent/"data"/"DXY"/YYYY-MM-DD.jsonl (date = bar_open_utc UTC)
Record: {"source","symbol","bar_open_utc","o","h","l","c","v","received_at_utc",
 "status"} status=ok|closed|http_error. Dedupe on bar_open_utc. Completed 5-min bars only.
Deps: stdlib + requests only.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL, SOURCE, BAR_MIN = "DXY", "yahoo-chart", 5
YAHOO = "DX-Y.NYB"
BASES = [
    f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO}",
    f"https://query2.finance.yahoo.com/v8/finance/chart/{YAHOO}",
]
OUTDIR = Path(__file__).parent / "data" / "DXY"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def utcnow(): return datetime.now(timezone.utc)
def iso_z(dt): return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def floor5(dt): return dt.replace(minute=(dt.minute // BAR_MIN) * BAR_MIN, second=0, microsecond=0)


def range_for_days(days):
    try:
        days = int(days)
    except Exception:
        days = 20
    if days <= 5:
        return "5d"
    return "1mo"  # 5m bars available up to ~60d; 20 trading days covered


def fetch_yahoo(range_str):
    params = {"range": range_str, "interval": "5m",
              "includePrePost": "false", "events": "div|split"}
    last_err = None
    for base in BASES:
        try:
            r = requests.get(base, headers=UA, params=params, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as e:
            last_err = e
            continue
        err = (j.get("chart") or {}).get("error")
        if err:
            last_err = err
            continue
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            last_err = "empty result"
            continue
        return res[0]
    raise RuntimeError(f"yahoo fetch failed: {last_err}")


def to_recs(result, rx, now):
    ts = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = q.get("open") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    vols = q.get("volume") or []
    out = []
    for i, epoch in enumerate(ts):
        try:
            op = datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(second=0, microsecond=0)
        except Exception:
            continue
        if op + timedelta(minutes=BAR_MIN) > now:
            continue  # unfinished bucket: do not freeze partials
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        v = vols[i] if i < len(vols) else None
        if o is None and h is None and lo is None and c is None:
            continue  # null-only row is failure, not data
        if c is None:
            continue
        try:
            vv = int(v) if v is not None else 0
        except (ValueError, TypeError):
            vv = 0
        out.append({"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": iso_z(op),
                    "o": o, "h": h, "l": lo, "c": c,
                    "v": vv, "received_at_utc": rx, "status": "ok"})
    return out


def load_opens(path):
    s = set()
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("status") not in (None, "ok"):
                    continue  # error rows must not block late real bars
                if d.get("c") is None:
                    continue
                k = d.get("bar_open_utc")
                if k:
                    s.add(k)
    return s


def append_recs(recs):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for x in recs:
        by_day.setdefault(x["bar_open_utc"][:10], []).append(x)
    n = 0
    for day in sorted(by_day):
        p = OUTDIR / f"{day}.jsonl"
        seen = load_opens(p)
        rows = [x for x in by_day[day] if x["bar_open_utc"] not in seen]
        if not rows:
            continue
        with open(p, "a", encoding="utf-8") as f:
            for x in rows:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        seen.update(x["bar_open_utc"] for x in rows)
        n += len(rows)
        print(f"wrote {len(rows)} -> {p}")
    return n


def write_closed(rx):
    day = utcnow().strftime("%Y-%m-%d")
    rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": f"{day}T00:00:00Z",
           "o": None, "h": None, "l": None, "c": None, "v": 0,
           "received_at_utc": rx, "status": "closed"}
    print(f"weekend closed -> {day}")
    return append_recs([rec])


def once(days=20):
    if utcnow().weekday() >= 5:
        return write_closed(iso_z(utcnow())), "closed"
    rx = iso_z(utcnow())
    try:
        result = fetch_yahoo(range_for_days(days))
        now = utcnow()
        recs = [x for x in to_recs(result, rx, now)
                if datetime.fromisoformat(x["bar_open_utc"].replace("Z", "+00:00"))
                >= now - timedelta(days=days)]
    except Exception as e:
        print(f"http_error {e}")
        return 0, "http_error"
    if not recs:
        print("http_error empty after filter")
        return 0, "http_error"
    return append_recs(recs), "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--duration", type=float, default=24)
    a = ap.parse_args()
    n, st = once(a.days)
    print(f"done status={st} new={n}")
    if not a.loop:
        return
    t0 = time.time()
    while time.time() - t0 < a.duration * 3600:
        time.sleep(max(a.interval, 60))
        try:
            n, st = once(2)
            print(f"loop status={st} new={n}", flush=True)
        except Exception as e:
            print(f"loop error: {e}", flush=True)


if __name__ == "__main__":
    main()
