"""SOLUSDT (L1) collector: spot 5m + quote volume + futures OI.

Sources (Binance public GET):
- https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=5m (paged, last 20 days)
- https://fapi.binance.com/futures/data/openInterestHist?symbol=SOLUSDT&period=5m (fail -> null)
Run: python 21_SOL.py [--loop]  # once, or every 5 min for 24h; completed bars only.
Store: Path(__file__).parent/"data"/"SOL"/YYYY-MM-DD.jsonl, dedup on bar_open_utc.
Record: source/symbol/bar_open_utc/received_at_utc/o/h/l/c/base_vol/quote_vol/trades/open_interest/status.
Stdlib + requests only.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "SOLUSDT"
SOURCE = "binance"
SPOT_URL = "https://api.binance.com/api/v3/klines"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"
OUT_ROOT = Path(__file__).parent / "data" / "SOL"
STEP_MS = 5 * 60 * 1000
RANGE_MS = 20 * 24 * 60 * 60 * 1000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_spot() -> list:
    now = int(time.time() * 1000)
    start, end, out = now - RANGE_MS, now, []
    s = requests.Session()
    while start < end:
        r = s.get(SPOT_URL, params={"symbol": SYMBOL, "interval": "5m",
                  "startTime": start, "endTime": end, "limit": 1000}, timeout=20)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out.extend(rows)
        start = int(rows[-1][0]) + STEP_MS
        if len(rows) < 1000:
            break
    return [x for x in out if int(x[6]) < now]


def fetch_oi() -> dict:
    try:
        now = int(time.time() * 1000)
        start, end, m = now - RANGE_MS, now, {}
        s = requests.Session()
        while start < end:
            r = s.get(OI_URL, params={"symbol": SYMBOL, "period": "5m",
                      "startTime": start, "endTime": end, "limit": 500}, timeout=20)
            r.raise_for_status()
            rows = r.json()
            if not rows:
                break
            for d in rows:
                m[int(d["timestamp"])] = float(d["sumOpenInterest"])
            start = int(rows[-1]["timestamp"]) + STEP_MS
            if len(rows) < 500:
                break
        return m
    except Exception:
        return {}


def seen_of(path: Path) -> set:
    if not path.exists():
        return set()
    s = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            s.add(json.loads(line).get("bar_open_utc"))
        except Exception:
            pass
    return s


def collect_once() -> int:
    klines = fetch_spot()
    oi = fetch_oi()
    oi_ok = bool(oi)
    recv = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    groups: dict[str, list] = {}
    for k in klines:
        b = iso(int(k[0]))
        v = oi.get(int(k[0]))
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": b,
               "received_at_utc": recv, "o": float(k[1]), "h": float(k[2]),
               "l": float(k[3]), "c": float(k[4]), "base_vol": float(k[5]),
               "quote_vol": float(k[7]), "trades": int(k[8]),
               "open_interest": v, "status": "ok" if v is not None and oi_ok else "ok_oi_null"}
        groups.setdefault(b[:10], []).append(rec)
    n = 0
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for day, recs in groups.items():
        p = OUT_ROOT / f"{day}.jsonl"
        seen = seen_of(p)
        with p.open("a", encoding="utf-8") as f:
            for rec in recs:
                if rec["bar_open_utc"] not in seen:
                    f.write(json.dumps(rec) + "\n")
                    seen.add(rec["bar_open_utc"])
                    n += 1
    print(f"[SOL] bars={len(klines)} appended={n} oi={'ok' if oi_ok else 'null'}")
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="every 5 min for 24h")
    a = ap.parse_args()
    collect_once()
    if a.loop:
        end = time.time() + 24 * 3600
        while time.time() < end:
            time.sleep(300)
            try:
                collect_once()
            except Exception as e:
                print(f"[SOL] loop error: {e}")


if __name__ == "__main__":
    main()
