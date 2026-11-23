"""Fill missing Binance spot 5-minute history for the 20 prior UTC days."""
import json
import math
import os
from pathlib import Path
import runpy
import sys
from tempfile import NamedTemporaryFile
from datetime import datetime, timedelta, timezone

import requests


HERE = Path(__file__).resolve().parent
STEP = 300_000
DAY = STEP * 288
PRICE_STATUS = {"", "ok", "ok_oi_null", "oi_unavailable"}


def millis(value):
    if isinstance(value, str) and not value.isdigit():
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    n = int(value)
    return n if n > 10**12 else n * 1000


def saved_slots(folder, date):
    slots = set()
    for path in folder.glob(date + "*.jsonl"):
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if (row.get("status") or "") not in PRICE_STATUS:
                        continue
                    close = float(row.get("c", row.get("close")))
                    quote = next(float(row[k]) for k in ("quote_vol", "quote_volume", "turnover", "quote_v") if row.get(k) is not None)
                    if close > 0 and math.isfinite(close) and quote >= 0 and math.isfinite(quote):
                        slots.add(millis(next(row[k] for k in ("bar_open_utc", "open_time", "open_ts", "open_ms", "t") if row.get(k) is not None)))
                except (ValueError, TypeError, StopIteration, KeyError, OverflowError):
                    continue
    return slots


def fill(tag):
    folder = HERE / "data" / tag
    folder.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    added = 0
    with requests.Session() as session:
        for offset in range(1, 21):
            day = today - timedelta(days=offset)
            start = int(day.timestamp() * 1000)
            date = day.strftime("%Y-%m-%d")
            missing = set(range(start, start + DAY, STEP)) - saved_slots(folder, date)
            if not missing:
                continue
            params = {"symbol": tag + "USDT", "interval": "5m", "startTime": min(missing),
                      "endTime": max(missing) + STEP - 1, "limit": 288}
            resp = session.get("https://data-api.binance.vision/api/v3/klines", params=params, timeout=12)
            resp.raise_for_status()
            lines = []
            for k in resp.json():
                t = int(k[0])
                if t not in missing:
                    continue
                o, h, low, c, base, quote = (float(k[i]) for i in (1, 2, 3, 4, 5, 7))
                if not all(map(math.isfinite, (o, h, low, c, base, quote))) or min(o, h, low, c) <= 0 or min(base, quote) < 0:
                    continue
                record = {"source": "binance-spot", "symbol": params["symbol"], "bar_open_utc": datetime.fromtimestamp(t / 1000, timezone.utc).isoformat(),
                          "o": o, "h": h, "l": low, "c": c, "base_vol": base, "quote_vol": quote, "status": "oi_unavailable"}
                lines.append(json.dumps(record, separators=(",", ":")) + "\n")
                missing.remove(t)
            if lines:
                path = folder / (date + ".history.jsonl")
                scratch = HERE.parent / "work" / "삭제"
                scratch.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=scratch, delete=False) as stream:
                    if path.exists():
                        stream.write(path.read_text(encoding="utf-8"))
                    stream.writelines(lines)
                os.replace(stream.name, path)
                added += len(lines)
    print(f"{tag}: added {added} completed historical spot bars", flush=True)


if __name__ == "__main__":
    symbol = sys.argv[1]
    # With a collector argument, always collect the current quote before repairing history.
    if len(sys.argv) > 2:
        sys.argv = sys.argv[2:]
        try:
            runpy.run_path(sys.argv[0], run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise
    fill(symbol)
