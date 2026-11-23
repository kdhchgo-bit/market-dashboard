"""BTCUSDT 5m collector (Binance spot klines + futures OI).

Usage:
  python 19_BTC.py                 # 20일 백필 1회 수집 (완성봉만)
  python 19_BTC.py --loop          # 백필 후 5분 간격 24시간 반복
  python 19_BTC.py --loop --interval 300 --duration 24 --days 20

Output:
  <this_dir>/data/BTC/YYYY-MM-DD.jsonl (bar_open_utc 날짜별)
Record:
  {"source":"binance-spot+futures","symbol":"BTCUSDT","bar_open_utc":..,
   "received_at_utc":..,"o":..,"h":..,"l":..,"c":..,"base_vol":..,
   "quote_vol":..,"trades":..,"open_interest":..|null,
   "status":"ok|http_error|oi_unavailable"}
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "BTCUSDT"
SOURCE = "binance-spot+futures"
SPOT = "https://api.binance.com/api/v3/klines"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"
OUT = Path(__file__).parent / "data" / "BTC"
BAR_MS = 5 * 60 * 1000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_seen() -> set:
    seen = set()
    if not OUT.exists():
        return seen
    for fp in OUT.glob("*.jsonl"):
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    try:
                        seen.add(json.loads(line).get("bar_open_utc"))
                    except Exception:
                        pass
        except Exception:
            pass
    return seen


def fetch_oi():
    try:
        r = requests.get(OI_URL, params={"symbol": SYMBOL, "period": "5m", "limit": 30}, timeout=15)
        r.raise_for_status()
        d = {}
        for e in r.json():
            try:
                d[int(e["timestamp"])] = float(e["sumOpenInterest"])
            except Exception:
                pass
        return d
    except Exception:
        return None


def fetch_pages(start_ms, end_ms):
    out, cur = [], start_ms
    while cur < end_ms:
        r = requests.get(SPOT, params={"symbol": SYMBOL, "interval": "5m", "limit": 1000,
                                       "startTime": cur, "endTime": end_ms}, timeout=15)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out += batch
        nxt = int(batch[-1][0]) + BAR_MS
        if nxt <= cur:
            break
        cur = nxt
        if len(batch) < 1000:
            break
        time.sleep(0.2)
    return out


def save(rec, seen) -> bool:
    if rec["bar_open_utc"] in seen:
        return False
    fp = OUT / (rec["bar_open_utc"][:10] + ".jsonl")
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    seen.add(rec["bar_open_utc"])
    return True


def collect(days, seen) -> int:
    now_ms = int(time.time() * 1000)
    klines = fetch_pages(now_ms - days * 86400 * 1000, now_ms)
    oi = fetch_oi()
    n, rx = 0, now_iso()
    for k in klines:
        ot, o, h, l, c, bv, ct, qv, tr = int(k[0]), k[1], k[2], k[3], k[4], k[5], k[6], k[7], k[8]
        if int(ct) > now_ms:  # 미완성봉 제외
            continue
        v = oi.get(ot) if oi else None
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": iso(ot), "received_at_utc": rx,
               "o": float(o), "h": float(h), "l": float(l), "c": float(c),
               "base_vol": float(bv), "quote_vol": float(qv), "trades": int(tr),
               "open_interest": v, "status": "ok" if v is not None else "oi_unavailable"}
        n += save(rec, seen)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--duration", type=float, default=24)
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    seen = load_seen()
    try:
        print("saved:", collect(a.days, seen), flush=True)
    except Exception as e:
        print("http_error:", e, flush=True)
    if not a.loop:
        return
    t0 = time.time()
    while time.time() - t0 < a.duration * 3600:
        time.sleep(a.interval)
        seen = seen if len(seen) < 200000 else load_seen()
        try:
            print("saved:", collect(1, seen), flush=True)
        except Exception as e:
            print("http_error:", e, flush=True)


if __name__ == "__main__":
    main()
