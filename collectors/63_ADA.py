"""ADAUSDT Binance spot 5m + turnover + futures OI collector.

Spot 5m (completed only) + quote_volume=turnover + futures OI(hist).
Store: data/ADA/YYYY-MM-DD.jsonl (by open time, open_time dedup append).
Default: single collect. --loop: repeat every 5 min. stdlib only.
"""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SYMBOL = "ADAUSDT"
INTERVAL = "5m"
DATA_DIR = Path(__file__).resolve().parent / "data" / "ADA"
SPOT_URL = "https://api.binance.com/api/v3/klines"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"
DEFAULT_LIMIT = 100
SLEEP_SEC = 300

NET_ERR = (urllib.error.URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError)


def http_get(url, params, timeout=15):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_spot(limit):
    return http_get(SPOT_URL, {"symbol": SYMBOL, "interval": INTERVAL, "limit": limit})


def fetch_oi(limit):
    try:
        return http_get(OI_URL, {"symbol": SYMBOL, "period": INTERVAL, "limit": limit})
    except NET_ERR:
        return []


def completed(klines):
    now = int(time.time() * 1000)
    return [k for k in klines if int(k[6]) < now]


def load_keys(path):
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            keys.add(json.loads(line)["open_time"])
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError):
            continue
    return keys


def collect_once(limit=DEFAULT_LIMIT):
    klines = completed(fetch_spot(limit))
    try:
        oi_rows = fetch_oi(limit)
    except NET_ERR:
        oi_rows = []
    oi_map = {}
    for o in oi_rows:
        try:
            oi_map[int(o["timestamp"])] = o
        except (KeyError, ValueError, TypeError):
            continue
    now_iso = datetime.now(timezone.utc).isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for k in klines:
        ot = int(k[0])
        day = datetime.fromtimestamp(ot / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        path = DATA_DIR / f"{day}.jsonl"
        if ot in load_keys(path):
            continue
        o = oi_map.get(ot, {})
        rec = {"symbol": SYMBOL, "interval": INTERVAL, "open_time": ot,
               "close_time": int(k[6]), "open": k[1], "high": k[2],
               "low": k[3], "close": k[4], "volume": k[5],
               "turnover": k[7], "trades": k[8],
               "oi": o.get("sumOpenInterest"), "oi_value": o.get("sumOpenInterestValue"),
               "collected_at": now_iso}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n += 1
    print(f"[{now_iso}] appended {n}/{len(klines)}")
    return n


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="ADAUSDT spot 5m + OI collector")
    p.add_argument("--loop", action="store_true", help="repeat every 5 min")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="klines limit (default 100)")
    p.add_argument("--days", type=float, default=1.0, help="loop duration days (default 1.0)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        collect_once(limit=args.limit)
    except NET_ERR as e:
        print("collect error:", e)
        if not args.loop:
            return 1
    if not args.loop:
        return 0
    end = time.time() + max(args.days, 0) * 86400
    while time.time() < end:
        time.sleep(SLEEP_SEC)
        try:
            collect_once(limit=args.limit)
        except NET_ERR as e:
            print("collect error:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
