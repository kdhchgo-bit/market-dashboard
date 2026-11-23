"""AAVEUSDT (DeFi) 5m collector: spot OHLCV + quote-volume + futures OI.

Sources:
  - GET https://api.binance.com/api/v3/klines?symbol=AAVEUSDT&interval=5m
  - GET https://fapi.binance.com/futures/data/openInterestHist?symbol=AAVEUSDT&period=5m
Mode: one-shot by default; --loop runs every 5 min for 24h, completed candles only.
Store: Path(__file__).parent/"data"/"AAVE"/YYYY-MM-DD.jsonl (dedup by open_ms).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "AAVEUSDT"
INTERVAL = "5m"
SPOT_URL = "https://api.binance.com/api/v3/klines"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"
OUT_DIR = Path(__file__).parent / "data" / "AAVE"
LIMIT = 288
SLEEP_SEC = 300
RUN_HOURS = 24


def fetch_spot(limit=LIMIT):
    p = {"symbol": SYMBOL, "interval": INTERVAL, "limit": limit}
    r = requests.get(SPOT_URL, params=p, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_oi(limit=LIMIT):
    p = {"symbol": SYMBOL, "period": INTERVAL, "limit": min(limit, 500)}
    r = requests.get(OI_URL, params=p, timeout=20)
    r.raise_for_status()
    return r.json()


def oi_map(oi_rows):
    m = {}
    for d in oi_rows or []:
        try:
            m[int(d["timestamp"])] = float(d["sumOpenInterest"])
        except (KeyError, ValueError, TypeError):
            continue
    return m


def existing_keys(path):
    keys = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                keys.add(int(json.loads(line)["open_ms"]))
            except (ValueError, KeyError, TypeError):
                continue
    return keys


def to_record(k, oi):
    ot, o, h, lo, c, v, ct, qv, n = k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7], k[8]
    return {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "open_time": datetime.fromtimestamp(ot / 1000, timezone.utc).isoformat(),
        "open_ms": int(ot),
        "close_ms": int(ct),
        "open": float(o),
        "high": float(h),
        "low": float(lo),
        "close": float(c),
        "volume": float(v),
        "quote_volume": float(qv),
        "trades": int(n),
        "futures_oi": oi.get(int(ot)),
    }


def collect_once(limit=LIMIT):
    klines = fetch_spot(limit)
    try:
        ois = oi_map(fetch_oi(limit))
    except Exception as e:
        print(f"OI fetch failed: {e}", file=sys.stderr)
        ois = {}
    now = int(time.time() * 1000)
    new = 0
    for k in klines:
        if int(k[6]) >= now:  # completed candles only
            continue
        rec = to_record(k, ois)
        day = datetime.fromtimestamp(rec["open_ms"] / 1000, timezone.utc).strftime("%Y-%m-%d")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        fp = OUT_DIR / f"{day}.jsonl"
        if rec["open_ms"] in existing_keys(fp):
            continue
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        new += 1
    print(f"collected {new} new candles")
    return new


def main():
    ap = argparse.ArgumentParser(description="AAVEUSDT 5m spot+OI collector")
    ap.add_argument("--loop", action="store_true", help="repeat every 5 min for 24h")
    ap.add_argument("--limit", type=int, default=LIMIT)
    a = ap.parse_args()
    if not a.loop:
        collect_once(a.limit)
        return
    end = time.time() + RUN_HOURS * 3600
    while True:
        try:
            collect_once(a.limit)
        except Exception as e:
            print(f"collect error: {e}", file=sys.stderr)
        if time.time() >= end:
            break
        time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    main()
