"""AVAXUSDT (L1) 5m collector: spot klines + quote-volume + futures OI.

Sources:
  - GET https://api.binance.com/api/v3/klines?symbol=AVAXUSDT&interval=5m
  - GET https://fapi.binance.com/futures/data/openInterestHist?symbol=AVAXUSDT&period=5m
Mode: one-shot default; `--loop` repeats every 5 min for 24h, closed candles only.
Store: Path(__file__).parent/"data"/"AVAX"/YYYY-MM-DD.jsonl (dedupe by open_time).
Deps: stdlib + requests only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import requests

SYMBOL = "AVAXUSDT"
INTERVAL = "5m"
BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "data" / "AVAX"
UA = {"User-Agent": "market-collector/1.0"}
KLINE_URL = "https://api.binance.com/api/v3/klines"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"


def get(url: str, params: dict, timeout: int = 20):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def closed_klines(limit: int = 288) -> list:
    data = get(KLINE_URL, {"symbol": SYMBOL, "interval": INTERVAL, "limit": limit})
    now_ms = int(time.time() * 1000)
    out = []
    for k in data:
        ot, o, h, l, c, v, ct = k[0], k[1], k[2], k[3], k[4], k[5], k[6]
        if int(ct) >= now_ms:  # forming candle -> skip
            continue
        out.append({
            "open_time": int(ot), "close_time": int(ct),
            "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": float(v),
            "quote_volume": float(k[7]), "trades": int(k[8]),
            "taker_buy_base": float(k[9]), "taker_buy_quote": float(k[10]),
        })
    return out


def oi_map(limit: int = 288) -> dict:
    try:
        data = get(OI_URL, {"symbol": SYMBOL, "period": INTERVAL, "limit": limit})
    except Exception:
        return {}
    m = {}
    for e in data:
        try:
            m[int(e["timestamp"])] = (float(e["sumOpenInterest"]),
                                      float(e.get("sumOpenInterestValue", 0.0)))
        except (KeyError, ValueError, TypeError):
            continue
    return m


def load_keys(path: Path) -> set:
    keys = set()
    if not path.exists():
        return keys
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                keys.add(int(json.loads(line)["open_time"]))
            except (KeyError, ValueError):
                continue
    return keys


def collect(limit: int = 288) -> int:
    klines = closed_klines(limit)
    ois = oi_map(limit)
    by_date: dict[str, list[dict]] = {}
    for k in klines:
        day = dt.datetime.fromtimestamp(k["open_time"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        oi, oiv = ois.get(k["open_time"], (None, None))
        rec = {
            "ts": dt.datetime.fromtimestamp(k["open_time"] / 1000, dt.timezone.utc).isoformat(),
            "symbol": SYMBOL, "interval": INTERVAL,
            "open_time": k["open_time"], "close_time": k["close_time"],
            "open": k["open"], "high": k["high"], "low": k["low"], "close": k["close"],
            "volume": k["volume"], "quote_volume": k["quote_volume"],
            "trades": k["trades"], "taker_buy_base": k["taker_buy_base"],
            "taker_buy_quote": k["taker_buy_quote"],
            "open_interest": oi, "open_interest_value": oiv,
        }
        by_date.setdefault(day, []).append(rec)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for day, recs in by_date.items():
        p = OUT_DIR / f"{day}.jsonl"
        keys = load_keys(p)
        with open(p, "a", encoding="utf-8") as f:
            for r in recs:
                if r["open_time"] in keys:
                    continue
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                keys.add(r["open_time"])
                n += 1
    print(f"[AVAX] klines={len(klines)} appended={n}")
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="AVAXUSDT 5m spot+OI collector")
    ap.add_argument("--loop", action="store_true", help="repeat every 5 min for 24h")
    ap.add_argument("--limit", type=int, default=288)
    a = ap.parse_args()
    if not a.loop:
        collect(a.limit)
        return
    end = time.time() + 24 * 3600
    while True:
        try:
            collect(a.limit)
        except Exception as e:
            print(f"[AVAX] error: {e}")
        if time.time() >= end:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
