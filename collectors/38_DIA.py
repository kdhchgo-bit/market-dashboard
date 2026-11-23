"""Usage: python 38_DIA.py [--loop] [--days 20]
Source: https://query1.finance.yahoo.com/v8/finance/chart/DIA?range=5d&interval=5m (fallback query2; backfill range=1mo)
Save: data/DIA/YYYY-MM-DD.jsonl (bar-start UTC date) e.g. {"source":"yahoo-5m","symbol":"DIA","bar_open_utc":"2026-11-11T14:30:00Z","status":"ok"}
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = 20-trading-day backfill (within Yahoo range) then incremental; drops in-progress bar.
Fields: source,symbol,bar_open_utc,bar_close_utc,received_at_utc,o,h,l,c,v,n,vwap,status + notional_approx (c*v approx; Yahoo provides no VWAP/trade-count so vwap=null,n=null).
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "DIA"
SRC = "yahoo-5m"
URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/DIA",
    "https://query2.finance.yahoo.com/v8/finance/chart/DIA",
]
OUT = Path(__file__).parent / "data" / "DIA"
BAR = timedelta(minutes=5)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def et_open(now):
    y = now.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(y, 3, s2, 7, tzinfo=timezone.utc) <= now < datetime(y, 11, s1, 6, tzinfo=timezone.utc)
    e = now + timedelta(hours=-4 if dst else -5)
    if e.weekday() >= 5:
        return False
    return (e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)


def et_date(epoch):
    o = datetime.fromtimestamp(epoch, tz=timezone.utc)
    y = o.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(y, 3, s2, 7, tzinfo=timezone.utc) <= o < datetime(y, 11, s1, 6, tzinfo=timezone.utc)
    e = o + timedelta(hours=-4 if dst else -5)
    return e, dst


def is_regular(epoch):
    e, _ = et_date(epoch)
    if e.weekday() >= 5:
        return False
    return (e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)


def yahoo_raw(range_):
    last_err = "http_error"
    for base in URLS:
        try:
            r = requests.get(base, headers=UA, params={"range": range_, "interval": "5m"}, timeout=20)
        except Exception:
            last_err = "http_error"
            continue
        if r.status_code == 429:
            return None, "rate_limited"
        if r.status_code != 200:
            last_err = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = "http_error"
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                last_err = "empty"
                continue
            return res[0], None
        except Exception:
            last_err = "http_error"
            continue
    return None, last_err


def fetch_completed(range_, now_epoch, cut_epoch):
    """Return list of (epoch,o,h,l,c,v) for regular completed 5m bars only."""
    res, err = yahoo_raw(range_)
    if err:
        return None, err
    try:
        ts = res.get("timestamp") or []
        q = (res.get("indicators") or {}).get("quote") or [{}]
        q = q[0]
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        closes = q.get("close") or []
        vols = q.get("volume") or []
    except Exception:
        return None, "http_error"
    out = []
    for i, t in enumerate(ts):
        try:
            t = int(t)
        except Exception:
            continue
        if t % 300 != 0:
            continue  # live partial quote, not a 5m bar open
        if t + 300 > now_epoch:
            continue  # in-progress bar
        if t >= cut_epoch:
            continue
        if not is_regular(t):
            continue
        try:
            o = opens[i] if i < len(opens) else None
            h = highs[i] if i < len(highs) else None
            l = lows[i] if i < len(lows) else None
            c = closes[i] if i < len(closes) else None
            v = vols[i] if i < len(vols) else None
        except Exception:
            continue
        if o is None and h is None and l is None and c is None:
            continue
        out.append((t, o, h, l, c, v))
    out.sort(key=lambda x: x[0])
    return out, None


def ok_seen():
    """Map date -> (path, set of bar_open_utc with status ok). Error rows never block."""
    seen = {}
    if not OUT.exists():
        return seen
    for p in OUT.glob("*.jsonl"):
        try:
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                if o.get("status") != "ok":
                    continue
                t = o.get("bar_open_utc")
                if not t:
                    continue
                d = t[:10]
                if d not in seen:
                    seen[d] = set()
                seen[d].add(t)
        except Exception:
            pass
    return seen


def append(recs):
    seen = ok_seen()
    paths = {}
    w = 0
    for r in recs:
        t = r.get("bar_open_utc")
        if not t:
            continue
        d = t[:10]
        if d not in seen:
            seen[d] = set()
            paths[d] = None
        if t in seen[d]:
            continue
        p = OUT / (d + ".jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        seen[d].add(t)
        w += 1
    return w


def append_status(base):
    p = OUT / (base["bar_open_utc"][:10] + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(base, separators=(",", ":")) + "\n")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="DIA 5Min collector (Yahoo public chart)")
    p.add_argument("--loop", action="store_true", help="repeat every 5min during ET open")
    p.add_argument("--days", type=int, default=20, help="backfill trading days (default 20)")
    return p.parse_args(argv)


def collect_once(days, now):
    f = "%Y-%m-%dT%H:%M:%SZ"
    now_epoch = int(now.timestamp())
    cut = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
    cut_epoch = int(cut.timestamp())
    base = {"source": SRC, "symbol": SYM, "bar_open_utc": cut.strftime(f), "bar_close_utc": (cut + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": None, "h": None, "l": None, "c": None, "v": None, "n": None, "vwap": None, "notional_approx": None}
    # max ok bar for incremental
    mx = None
    for s in ok_seen().values():
        for t in s:
            if mx is None or t > mx:
                mx = t
    rng = "5d" if mx else "1mo"
    bars, err = fetch_completed(rng, now_epoch, cut_epoch)
    if err:
        return {"kind": "error", "status": err, "base": dict(base, status=err)}
    if not mx and days and len(bars) > 0:
        # keep last `days` regular ET dates
        dates = sorted({et_date(t)[0].strftime("%Y-%m-%d") for t, *_ in bars})
        keep = set(dates[-days:])
        bars = [b for b in bars if et_date(b[0])[0].strftime("%Y-%m-%d") in keep]
    recs = []
    for t, o, h, l, c, v in bars:
        o_utc = datetime.fromtimestamp(t, tz=timezone.utc)
        ts = o_utc.strftime(f)
        if mx and ts <= mx:
            continue
        try:
            notional = (float(c) * float(v)) if (c is not None and v is not None) else None
        except Exception:
            notional = None
        recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": ts, "bar_close_utc": (o_utc + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": o, "h": h, "l": l, "c": c, "v": v, "n": None, "vwap": None, "notional_approx": notional, "status": "ok"})
    if recs:
        return {"kind": "ok", "recs": recs}
    if not mx:
        return {"kind": "error", "status": "delayed", "base": dict(base, status="delayed")}
    return {"kind": "uptodate"}


def main(argv=None):
    args = parse_args(argv)
    loop, days = args.loop, max(1, args.days)
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        f = "%Y-%m-%dT%H:%M:%SZ"
        if loop and not et_open(now):
            print(f"skip closed {now.strftime(f)}")
            time.sleep(300)
            continue
        if not loop and not et_open(now):
            # single run outside hours still tries Yahoo: backfill works closed-hours
            pass
        res = collect_once(days, now)
        if res["kind"] == "ok":
            print(f"ok +{append(res['recs'])}")
        elif res["kind"] == "uptodate":
            print("up-to-date")
        else:
            # write error row only if this cut has no ok row yet
            seen = ok_seen()
            d = res["base"]["bar_open_utc"][:10]
            if res["base"]["bar_open_utc"] not in seen.get(d, set()):
                append_status(res["base"])
            print(res["status"])
        if not loop:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
