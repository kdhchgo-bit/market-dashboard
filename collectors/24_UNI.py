"""UNIUSDT (DeFi) 5분봉 수집기 — 현물 OHLCV+거래대금 + 선물 OI.

원천:
  - GET https://api.binance.com/api/v3/klines?symbol=UNIUSDT&interval=5m
  - GET https://fapi.binance.com/futures/data/openInterestHist?symbol=UNIUSDT&period=5m
동작: 1회 수집(최근 24h 완성봉) + --loop 시 5분 간격 24시간 반복. 완성봉만 저장.
저장: Path(__file__).parent/"data"/"UNI"/YYYY-MM-DD.jsonl (날짜=캔들 UTC 일자), 중복(open_time) 금지.
사용: python 24_UNI.py [--loop] [--limit 288]
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "UNIUSDT"
SECTOR = "DeFi"
INTERVAL = "5m"
SPOT_URL = "https://api.binance.com/api/v3/klines"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"
LIMIT_DEFAULT = 288  # 5m * 288 = 24h
HEADERS = {"User-Agent": "market-collector/1.0"}


def get(url, params, timeout=20):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_spot(limit):
    params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": limit}
    data = get(SPOT_URL, params)
    now_ms = int(time.time() * 1000)
    out = {}
    for k in data:
        ot, o, h, l, c, v, ct = k[0], k[1], k[2], k[3], k[4], k[5], k[6]
        if ct >= now_ms:  # 완성봉만 (진행중 제외)
            continue
        out[int(ot)] = {
            "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(v),
            "turnover": float(k[7]), "trades": int(k[8]),
            "close_time": int(ct),
        }
    return out


def fetch_oi(limit):
    params = {"symbol": SYMBOL, "period": INTERVAL, "limit": limit}
    try:
        data = get(OI_URL, params)
    except Exception:
        return {}
    out = {}
    for d in data if isinstance(data, list) else []:
        out[int(d["timestamp"])] = float(d.get("sumOpenInterest", 0) or 0)
    return out


def day_path(base, ot_ms):
    day = datetime.fromtimestamp(ot_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    p = base / "data" / "UNI" / f"{day}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_keys(path):
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            keys.add(int(json.loads(line)["open_time"]))
        except Exception:
            pass
    return keys


def collect(limit):
    base = Path(__file__).parent
    spot = fetch_spot(limit)
    oi = fetch_oi(limit)
    now_iso = datetime.now(timezone.utc).isoformat()
    groups = {}
    for ot, s in spot.items():
        rec = {"symbol": SYMBOL, "sector": SECTOR, "interval": INTERVAL,
               "open_time": ot,
               "open_time_iso": datetime.fromtimestamp(ot / 1000, tz=timezone.utc).isoformat(),
               **s, "open_interest": oi.get(ot), "collected_at": now_iso}
        groups.setdefault(day_path(base, ot), []).append(rec)
    total = 0
    for path, recs in groups.items():
        keys = load_keys(path)
        fresh = [r for r in recs if r["open_time"] not in keys]
        if not fresh:
            continue
        fresh.sort(key=lambda r: r["open_time"])
        with open(path, "a", encoding="utf-8") as f:
            for r in fresh:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        total += len(fresh)
    print(f"[{now_iso}] candles={len(spot)} saved={total}")
    return total


def main():
    ap = argparse.ArgumentParser(description="UNIUSDT 5m collector (spot+OI, finished candles)")
    ap.add_argument("--loop", action="store_true", help="5분 간격 24시간 반복")
    ap.add_argument("--limit", type=int, default=LIMIT_DEFAULT)
    args = ap.parse_args()
    if not args.loop:
        collect(args.limit)
        return
    end = time.time() + 24 * 3600
    while True:
        try:
            collect(args.limit)
        except Exception as e:
            print(f"collect error: {e}")
        wait = 300 - (time.time() % 300) + 5
        if time.time() + wait > end:
            break
        time.sleep(wait)


if __name__ == "__main__":
    main()
