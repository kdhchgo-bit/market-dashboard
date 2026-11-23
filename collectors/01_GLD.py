"""Usage: python 01_GLD.py [--loop] [--days N]
Source (keyless): https://query1.finance.yahoo.com/v8/finance/chart/GLD?range=1mo&interval=5m (5m fixed; 1mo covers 20-trading-day backfill)
Save: data/GLD/YYYY-MM-DD.jsonl (bar-start UTC date) e.g. {"source":"yahoo-chart","symbol":"GLD","status":"ok"}
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = backfill then incremental; drops in-progress bar.
Yahoo gives no VWAP/trade-count: vwap=null, n=null kept; notional_proxy=(h+l+c)/3*v kept separately. Existing JSONL fields kept."""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "GLD"
SRC = "yahoo-chart"
HOSTS = ("https://query1.finance.yahoo.com/v8/finance/chart/GLD", "https://query2.finance.yahoo.com/v8/finance/chart/GLD")
OUT = Path(__file__).parent / "data" / "GLD"
BAR = timedelta(minutes=5)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
F = "%Y-%m-%dT%H:%M:%SZ"

def _et(us):
    y = us.year
    m = datetime(y, 3, 1, tzinfo=timezone.utc)
    n = datetime(y, 11, 1, tzinfo=timezone.utc)
    s = datetime(y, 3, 1 + (6 - m.weekday()) % 7 + 7, 7, tzinfo=timezone.utc)
    e = datetime(y, 11, 1 + (6 - n.weekday()) % 7, 6, tzinfo=timezone.utc)
    return us - timedelta(hours=4 if s <= us < e else 5)

def et_open(now):
    e = _et(now)
    return e.weekday() < 5 and (e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)

def get(url, params):
    for i in range(2):
        try:
            r = requests.get(url, headers=UA, params=params, timeout=20)
        except requests.RequestException:
            return None, "http_error"
        if r.status_code != 200:
            if r.status_code == 429 and i == 0:
                try:
                    w = int(r.headers.get("Retry-After", 5))
                except (ValueError, TypeError):
                    w = 5
                time.sleep(max(1, min(w, 15)))
                continue
            return None, ("rate_limited" if r.status_code == 429 else "http_error")
        try:
            return r.json(), None
        except ValueError:
            return None, "http_error"
    return None, "rate_limited"

def fetch(rng):
    pr = {"range": rng, "interval": "5m", "includePrePost": "false", "events": "div|split"}
    err = "http_error"
    for h in HOSTS:
        j, err = get(h, pr)
        if not isinstance(j, dict):
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
        except AttributeError:
            continue
        if res and isinstance(res[0], dict):
            return res[0], None
        err = "empty_result" if j else err
    return None, err

def parse(res, recv, end):
    try:
        ts = res.get("timestamp") or []
        quo = (res.get("indicators") or {}).get("quote") or [{}]
        q = quo[0] if isinstance(quo, list) else {}
        if not isinstance(q, dict):
            return []
    except AttributeError:
        return []
    recs = []
    for i, e in enumerate(ts):
        try:
            t = datetime.fromtimestamp(int(e), tz=timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        if t + BAR > end:
            continue
        et = _et(t)
        if et.weekday() >= 5 or (et.hour, et.minute) < (9, 30) or (et.hour, et.minute) >= (16, 0):
            continue
        v = {}
        for k in ("open", "high", "low", "close", "volume"):
            col = q.get(k)
            v[k] = col[i] if isinstance(col, list) and i < len(col) else None
        o, h, l, c, vol = v["open"], v["high"], v["low"], v["close"], v["volume"]
        if o is None and h is None and l is None and c is None:
            continue
        try:
            notional = (float(h) + float(l) + float(c)) / 3.0 * float(vol) if vol is not None else None
        except (TypeError, ValueError):
            notional = None
        recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": t.strftime(F), "bar_close_utc": (t + BAR).strftime(F),
                     "received_at_utc": recv, "o": o, "h": h, "l": l, "c": c, "v": vol, "n": None, "vwap": None,
                     "notional_proxy": notional, "status": "ok"})
    return recs

def append(recs):
    w, seen = 0, {}
    for r in recs:
        try:
            d = r["bar_open_utc"][:10]
            k = r["bar_open_utc"]
        except (KeyError, TypeError):
            continue
        if d not in seen:
            p = OUT / (d + ".jsonl")
            try:
                lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
            except (OSError, UnicodeDecodeError):
                lines = []
            s = set()
            for ln in lines:
                try:
                    dn = json.loads(ln)
                except ValueError:
                    continue
                if dn.get("status") != "ok":
                    continue
                kk = dn.get("bar_open_utc")
                if kk is not None:
                    s.add(kk)
            seen[d] = (p, s)
        p, s = seen[d]
        if k in s:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        s.add(k)
        w += 1
    return w

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Collect GLD 5Min bars (Yahoo public chart, no key) into data/GLD/")
    ap.add_argument("--loop", action="store_true", help="repeat every 5min during ET open")
    ap.add_argument("--days", type=int, default=20, help="trading-day backfill window (default 20)")
    return ap.parse_args(argv)

def run_once(days):
    days = max(1, days)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cut = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
    base = {"source": SRC, "symbol": SYM, "bar_open_utc": cut.strftime(F), "bar_close_utc": (cut + BAR).strftime(F),
            "received_at_utc": now.strftime(F), "o": None, "h": None, "l": None, "c": None, "v": None,
            "n": None, "vwap": None, "notional_proxy": None}
    if not et_open(now):
        append([dict(base, status="closed")])
        print("closed")
        return
    res, err = fetch("5d" if days <= 5 else "1mo")
    if res is None:
        append([dict(base, status=err)])
        print(err)
        return
    recs = parse(res, now.strftime(F), now)
    sess = sorted({r["bar_open_utc"][:10] for r in recs})
    keep = set(sess[-days:]) if len(sess) > days else set(sess)
    recs = [r for r in recs if r["bar_open_utc"][:10] in keep]
    if recs:
        print(f"ok +{append(recs)}")
    else:
        append([dict(base, status="delayed")])
        print("delayed")

def main(argv=None):
    args = parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.loop:
        run_once(args.days)
        return
    while True:
        now = datetime.now(timezone.utc)
        if et_open(now):
            run_once(args.days)
        else:
            print(f"skip closed {now.strftime(F)}")
        time.sleep(300)

if __name__ == "__main__":
    main()
