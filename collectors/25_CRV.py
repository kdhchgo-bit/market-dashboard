"""CRVUSDT (DeFi) 5m collector: spot klines + quote-volume + futures OI.

Sources:
  - GET https://api.binance.com/api/v3/klines?symbol=CRVUSDT&interval=5m
  - GET https://fapi.binance.com/futures/data/openInterestHist?symbol=CRVUSDT&period=5m
Usage:
  python 25_CRV.py            # single 24h backfill (completed candles only)
  python 25_CRV.py --loop     # repeat every 5m (aligned, +15s) until stopped
Output:
  <this_file_dir>/data/CRV/YYYY-MM-DD.jsonl (UTC date of candle open_time, dedup by open_time_ms)
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "CRVUSDT"
INTERVAL = "5m"
BASE_DIR = Path(__file__).parent / "data" / "CRV"
SPOT_URL = "https://api.binance.com/api/v3/klines"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"
S = requests.Session()
S.headers.update({"User-Agent": "market-collector/1.0"})


def get(url, params, timeout=20):
    r = S.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_oi_map(limit):
    try:
        rows = get(OI_URL, {"symbol": SYMBOL, "period": INTERVAL, "limit": limit})
    except Exception as e:
        print(f"[warn] OI fetch failed: {e}", file=sys.stderr)
        return {}
    m = {}
    for o in rows if isinstance(rows, list) else []:
        try:
            m[int(o["timestamp"])] = (float(o["sumOpenInterest"]), float(o["sumOpenInterestValue"]))
        except (KeyError, ValueError, TypeError):
            continue
    return m


def collect(limit=288):
    now = int(time.time() * 1000)
    klines = get(SPOT_URL, {"symbol": SYMBOL, "interval": INTERVAL, "limit": limit})
    oi_map = fetch_oi_map(limit)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for k in klines:
        ot, o, h, l, c, v, ct = int(k[0]), k[1], k[2], k[3], k[4], k[5], int(k[6])
        if ct > now:
            continue  # completed candles only
        oi = oi_map.get(ot)
        out.append({
            "symbol": SYMBOL, "interval": INTERVAL,
            "open_time": datetime.fromtimestamp(ot / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open_time_ms": ot, "close_time_ms": ct,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": float(v), "quote_volume": float(k[7]),
            "trades": int(k[8]), "taker_buy_base": float(k[9]), "taker_buy_quote": float(k[10]),
            "oi": oi[0] if oi else None, "oi_value": oi[1] if oi else None,
            "collected_at": ts,
        })
    return out


def load_keys(path):
    keys = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                keys.add(json.loads(line)["open_time_ms"])
            except (ValueError, KeyError):
                continue
    return keys


def save(rows):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for r in rows:
        day = r["open_time"][:10]
        by_day.setdefault(day, []).append(r)
    total = 0
    for day, items in sorted(by_day.items()):
        p = BASE_DIR / f"{day}.jsonl"
        seen = load_keys(p)
        fresh = [r for r in items if r["open_time_ms"] not in seen]
        if fresh:
            with p.open("a", encoding="utf-8") as f:
                for r in fresh:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    seen.add(r["open_time_ms"])
        total += len(fresh)
        print(f"{p.name}: +{len(fresh)}/{len(items)}")
    return total


def wait_next_boundary(delay=15, step=300):
    nxt = (int(time.time()) // step + 1) * step + delay
    time.sleep(max(1, nxt - time.time()))


def main():
    ap = argparse.ArgumentParser(description="CRVUSDT 5m collector (spot klines + futures OI, completed candles)")
    ap.add_argument("--loop", action="store_true", help="repeat every 5m (aligned, +15s) until stopped")
    ap.add_argument("--limit", type=int, default=288, help="klines limit (default 288 = 24h)")
    ap.add_argument("--sleep", type=int, default=300, help="loop sleep seconds (default 300)")
    a = ap.parse_args()
    n = save(collect(a.limit))
    print(f"done: +{n}")
    while a.loop:
        if a.sleep != 300:
            time.sleep(a.sleep)
        else:
            wait_next_boundary()
        try:
            print(f"loop: +{save(collect(a.limit))}")
        except Exception as e:
            print(f"[warn] loop iter failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
