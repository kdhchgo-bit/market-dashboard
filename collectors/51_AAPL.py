"""AAPL 5m collector (tech/XLK).
src: Yahoo chart, no key.
save: data/AAPL/YYYY-MM-DD.jsonl, dedup on bar_open_utc.
use: python 51_AAPL.py [--days N] | --loop.
"""
import argparse, json, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "AAPL"
SECTOR = "XLK"
SOURCE = "yahoo"
HOSTS = ["https://query1.finance.yahoo.com",
         "https://query2.finance.yahoo.com"]
CHART = f"/v8/finance/chart/{SYMBOL}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT = Path(__file__).parent / "data" / "AAPL"

def utcnow(): return datetime.now(timezone.utc)
def floor5(dt): return dt.replace(minute=dt.minute // 5 * 5, second=0, microsecond=0)

def et_now():
    try:
        from zoneinfo import ZoneInfo
        return utcnow().astimezone(ZoneInfo("America/New_York"))
    except Exception:
        dst = time.localtime().tm_isdst > 0
        return utcnow() - timedelta(hours=4 if dst else 5)

def in_regular(et):
    return et.weekday() < 5 and (9, 30) <= (et.hour, et.minute) < (16, 0)

def _get(params):
    last = None
    for h in HOSTS:
        try:
            r = requests.get(h + CHART, params=params,
                             headers=UA, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
    raise RuntimeError(f"yahoo fetch failed: {last}")

def _etz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception: return None

def _parse(j):
    ch = j.get("chart") or {}
    if ch.get("error"):
        raise RuntimeError(f"yahoo error: {ch['error']}")
    res = ch.get("result") or []
    if not res:
        return []
    r0 = res[0]
    ts = r0.get("timestamp") or []
    q = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    cols = [q.get(k) or [] for k in ("open", "high", "low",
                                     "close", "volume")]
    etz = _etz()
    now = utcnow()
    out = []
    for i, epoch in enumerate(ts):
        try:
            epoch = int(epoch)
        except Exception:
            continue
        if epoch % 300 != 0:
            continue
        v = [c[i] if i < len(c) else None for c in cols]
        o, h, lo, c, vol = v
        if o is None and h is None and lo is None and c is None:
            continue
        bt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        if etz is not None:
            et = bt.astimezone(etz)
            if et.weekday() >= 5:
                continue
            if not (9, 30) <= (et.hour, et.minute) < (16, 0):
                continue
        if bt + timedelta(minutes=5) > now:
            continue
        t = bt.isoformat().replace("+00:00", "Z")
        out.append({"t": t, "o": o, "h": h, "l": lo,
                    "c": c, "v": vol})
    return out

def fetch(start, end):
    p = {"period1": int(start.timestamp()),
         "period2": int(end.timestamp()), "interval": "5m",
         "includePrePost": "false"}
    try:
        bars = _parse(_get(p))
        if bars:
            return bars
    except Exception as e:
        print(f"period fetch fail: {e}", file=sys.stderr)
    span = (end - start).days
    rng = "5d" if span <= 7 else "1mo"
    bars = _parse(_get({"range": rng, "interval": "5m",
                        "includePrePost": "false"}))
    out = []
    for b in bars:
        try:
            bt = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
        except Exception:
            continue
        if start - timedelta(minutes=1) <= bt <= end:
            out.append(b)
    return out

def seen_keys(path):
    s = set()
    if not path.exists():
        return s
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("status") != "ok" or d.get("o") is None or d.get("c") is None:
            continue
        if d.get("bar_open_utc"):
            s.add(d["bar_open_utc"])
    return s

def save(bars, status="ok"):
    now = utcnow().isoformat().replace("+00:00", "Z")
    n_new = 0
    cur = utcnow()
    for b in bars:
        t = b.get("t", "")
        try:
            bt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            continue
        if bt + timedelta(minutes=5) > cur:
            continue
        path = OUT / f"{t[:10]}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if t in seen_keys(path):
            continue
        c, v = b.get("c"), b.get("v")
        try:
            turn = float(c) * float(v)
        except Exception:
            turn = None
        rec = {"source": SOURCE, "symbol": SYMBOL,
               "sector": SECTOR, "bar_open_utc": t,
               "received_at_utc": now, "o": b.get("o"),
               "h": b.get("h"), "l": b.get("l"), "c": c,
               "v": v, "n": None, "vwap": None,
               "turnover_approx": turn, "status": status}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n_new += 1
    return n_new

def once(days=20):
    end = utcnow()
    start = end - timedelta(days=int(days * 1.5 + 5))
    bars = fetch(start, end)
    print(f"fetched={len(bars)} new={save(bars)}")

def loop():
    while True:
        try:
            et = et_now()
            if in_regular(et):
                end = floor5(utcnow())
                bars = fetch(end - timedelta(minutes=15), end)
                n = save(bars)
                print(f"{et.isoformat()} fetched={len(bars)} "
                      f"new={n}", flush=True)
            else:
                print(f"{et.isoformat()} out-of-session sleep",
                      flush=True)
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
        time.sleep(300)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    loop() if a.loop else once(a.days)


if __name__ == "__main__":
    main()
