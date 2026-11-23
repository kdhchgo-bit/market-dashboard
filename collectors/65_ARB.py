"""ARB (ARBUSDT, L2) 5분봉 수집기.

Binance 현물 5분봉(OHLCV+거래대금+트레이드수) + 선물 OI 히스토리를
결합해 24시간 완성봉만 data/ARB/YYYY-MM-DD.jsonl 에 저장한다.
1회 수집 후 5분 간격으로 반복한다. 표준lib + requests만 사용.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "ARBUSDT"
TF = "5m"
SPOT_URL = "https://api.binance.com/api/v3/klines"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"
DATA_DIR = Path(__file__).resolve().parent / "data" / "ARB"
LIMIT = 288  # 5m x 288 = 24h
SLEEP = 300


def get(url, params, timeout=15):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_spot():
    now = int(time.time() * 1000)
    data = get(SPOT_URL, {"symbol": SYMBOL, "interval": TF, "limit": LIMIT})
    out = []
    for k in data:
        ot, o, h, l, c, v, ct, qv, n = k[0], k[1], k[2], k[3], k[4], k[5], k[6], k[7], k[8]
        if ct > now:  # 미완성봉 제외
            continue
        out.append({"open_ts": ot, "close_ts": ct, "o": float(o), "h": float(h),
                    "l": float(l), "c": float(c), "v": float(v),
                    "quote_v": float(qv), "trades": int(n)})
    return out


def fetch_oi():
    try:
        data = get(OI_URL, {"symbol": SYMBOL, "period": TF, "limit": LIMIT})
    except Exception:
        return {}
    m = {}
    for d in data:
        ts = int(d.get("timestamp", 0))
        m[ts] = (float(d.get("sumOpenInterest", 0) or 0),
                 float(d.get("sumOpenInterestValue", 0) or 0))
    return m


def save(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for r in rows:
        day = datetime.fromtimestamp(r["open_ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(r)
    for day, rs in by_day.items():
        fp = DATA_DIR / f"{day}.jsonl"
        seen = set()
        if fp.exists():
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    try:
                        seen.add(json.loads(line).get("open_ts"))
                    except Exception:
                        pass
        with open(fp, "a", encoding="utf-8") as f:
            for r in sorted(rs, key=lambda x: x["open_ts"]):
                if r["open_ts"] not in seen:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    seen.add(r["open_ts"])
        print(f"[ARB] {day} +{len([r for r in rs if True])} -> {fp}", flush=True)


def collect_once():
    klines = fetch_spot()
    oi = fetch_oi()
    rows = []
    for k in klines:
        o, v = oi.get(k["open_ts"], (None, None))
        rows.append({"symbol": SYMBOL, "tf": TF, **k, "oi": o, "oi_value": v,
                     "collected_at": datetime.now(timezone.utc).isoformat()})
    # 24시간 컷 (288봉)
    rows = sorted(rows, key=lambda x: x["open_ts"])[-LIMIT:]
    if rows:
        save(rows)
    else:
        print("[ARB] no completed candles", flush=True)
    return len(rows)


def wait_next():
    nxt = (int(time.time()) // SLEEP + 1) * SLEEP + 10
    time.sleep(max(1, nxt - time.time()))


def main(loop=True):
    collect_once()
    while loop:
        wait_next()
        try:
            collect_once()
        except Exception as e:
            print(f"[ARB] err: {e}", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="1회만 수집")
    a = p.parse_args()
    main(loop=not a.once)
