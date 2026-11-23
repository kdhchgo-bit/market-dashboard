"""UNH (Healthcare, Sector ETF XLV) 5-min bars + volume collector.

Source: Yahoo Finance public chart (no key)
  GET https://query1.finance.yahoo.com/v8/finance/chart/UNH?period1=..&period2=..&interval=5m&includePrePost=false
  (fallback host: query2.finance.yahoo.com)
  Regular-session completed 5-min OHLCV bars only. Yahoo provides no VWAP/
  trade-count, so vw/n are preserved as null and an approximate notional
  turnover field `amt` (typical-price x volume) is stored separately.

Usage:
  python 59_UNH.py            # one-shot: last 20 trading days, completed 5-min bars
  python 59_UNH.py --loop     # ET regular session (09:30-16:00, Mon-Fri), completed bars only

Storage: Path(__file__).parent/"data"/"UNH"/YYYY-MM-DD.jsonl (ET date split, dedup by bar time `t`).
Line schema: {"symbol","t","o","h","l","c","v","vw","n","amt"} (vw=null, n=null; amt=approx turnover).
Stdlib + requests only.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "UNH"
TAG = "UNH"
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TF_SECS = 300
ET = ZoneInfo("America/New_York")
BASE = Path(__file__).parent / "data" / SYMBOL


def _parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _yahoo_get(params):
    last = None
    for host in HOSTS:
        url = f"https://{host}/v8/finance/chart/{SYMBOL}"
        try:
            r = requests.get(url, headers=UA, params=params, timeout=30)
        except Exception as e:
            last = e
            continue
        if r.status_code != 200:
            last = RuntimeError(f"HTTP {r.status_code} from {host}")
            continue
        try:
            return r.json()
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"yahoo chart fetch failed: {last}")


def fetch(start_dt, end_dt):
    """Return raw Yahoo bars as [{t,o,h,l,c,v}] with UTC ISO `t` (bar open)."""
    p1 = int(start_dt.timestamp())
    p2 = int(end_dt.timestamp()) + 60
    try:
        j = _yahoo_get({"period1": p1, "period2": p2, "interval": "5m",
                        "includePrePost": "false", "events": "div|split"})
    except Exception:
        j = _yahoo_get({"range": "1mo", "interval": "5m", "includePrePost": "false"})
    try:
        res = (j.get("chart") or {}).get("result") or []
        root = res[0]
        ts = root.get("timestamp") or []
        q = ((root.get("indicators") or {}).get("quote") or [{}])[0]
        opens, highs = q.get("open") or [], q.get("high") or []
        lows, closes = q.get("low") or [], q.get("close") or []
        vols = q.get("volume") or []
    except Exception:
        return []
    out = []
    for i, t in enumerate(ts):
        try:
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            o = opens[i] if i < len(opens) else None
            h = highs[i] if i < len(highs) else None
            lo = lows[i] if i < len(lows) else None
            v = vols[i] if i < len(vols) else None
            dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
            out.append({"t": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "o": o, "h": h, "l": lo, "c": c, "v": v})
        except Exception:
            continue
    return out


def completed(bars, now):
    return [b for b in bars if _parse(b["t"]) + timedelta(minutes=5) <= now]


def reg_open(dt_et):
    return dt_et.weekday() < 5 and dtime(9, 30) <= dt_et.time() < dtime(16, 0)


def _amt(b):
    try:
        v = b.get("v")
        if v is None:
            return None
        o, h, lo, c = b.get("o"), b.get("h"), b.get("l"), b.get("c")
        px = None
        vals = [x for x in (h, lo, c) if isinstance(x, (int, float))]
        if len(vals) == 3:
            px = sum(vals) / 3.0
        elif isinstance(c, (int, float)):
            px = c
        elif isinstance(o, (int, float)):
            px = o
        if px is None:
            return None
        return round(px * v, 2)
    except Exception:
        return None


def save(bars):
    BASE.mkdir(parents=True, exist_ok=True)
    groups = {}
    for b in bars:
        try:
            et = _parse(b["t"]).astimezone(ET)
        except Exception:
            continue
        if et.weekday() >= 5 or not dtime(9, 30) <= et.time() < dtime(16, 0):
            continue
        groups.setdefault(et.date().isoformat(), []).append(b)
    n = 0
    for day in sorted(groups):
        fp = BASE / f"{day}.jsonl"
        seen = set()
        if fp.exists():
            for ln in fp.read_text(encoding="utf-8").splitlines():
                try:
                    t = json.loads(ln).get("t")
                    if t:
                        seen.add(t)
                except Exception:
                    pass
        with fp.open("a", encoding="utf-8") as f:
            for b in sorted(groups[day], key=lambda x: x["t"]):
                if b["t"] in seen:
                    continue
                f.write(json.dumps({"symbol": SYMBOL, "t": b["t"], "o": b["o"],
                                    "h": b["h"], "l": b["l"], "c": b["c"],
                                    "v": b["v"], "vw": None,
                                    "n": None, "amt": _amt(b)}) + "\n")
                seen.add(b["t"])
                n += 1
    return n


def start_for(days):
    d = datetime.now(ET)
    cnt, cur = 0, d
    while cnt < days:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            cnt += 1
    return cur.replace(hour=0, minute=0, second=0, microsecond=0)


def secs_to_next5(now):
    return 300 - (now.timestamp() % 300) + 30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    now = datetime.now(ET)
    s = start_for(a.days)
    utc = datetime.now().astimezone()
    n = save(completed(fetch(s, utc), utc))
    print(f"[UNH] one-shot +{n} bars ({a.days}d window from {s.date()}) -> {BASE}")
    if not a.loop:
        return
    while True:
        try:
            now = datetime.now(ET)
            if not reg_open(now):
                time.sleep(60)
                continue
            utc = datetime.now().astimezone()
            w0 = utc - timedelta(days=2)
            n2 = save(completed(fetch(w0, utc), utc))
            print(f"[UNH] loop +{n2} @ {now:%Y-%m-%d %H:%M} ET", flush=True)
            time.sleep(secs_to_next5(utc))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[UNH] warn: {e}", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
