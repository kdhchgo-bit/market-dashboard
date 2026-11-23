"""Usage: python 74_VIX_TERM.py [--loop] [--interval SEC]
Source: Yahoo chart v8 (primary, keyless) fallback Stooq CSV (keyless)
  GET https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d (also %5EVIX9D %5EVIX3M %5EVIX6M)
  GET https://stooq.com/q/l/?s=%5Evix&f=sd2t2ohlcv&h&e=csv
Save: data/VIX_TERM/YYYY-MM-DD.jsonl e.g. {"source":"yahoo","symbol":"VIX","value":15.2,"obs_time":"2026-11-10T21:00:00Z","received_at_utc":"...","is_delayed":true,"status":"ok"}
Loop: --loop repeats every --interval sec (default 900) during ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = one snapshot + term summary.
Note: free snapshot is delayed; is_delayed=true always.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMS = ("VIX", "VIX9D", "VIX3M", "VIX6M")
YSYM = {"VIX": "%5EVIX", "VIX9D": "%5EVIX9D", "VIX3M": "%5EVIX3M", "VIX6M": "%5EVIX6M"}
SSYM = {"VIX": "%5Evix", "VIX9D": "%5Evix9d", "VIX3M": "%5Evix3m", "VIX6M": "%5Evix6m"}
OUT = Path(__file__).parent / "data" / "VIX_TERM"
UA = {"User-Agent": "Mozilla/5.0 (VIX-TERM snapshot; delayed)"}
FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


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


def fetch_yahoo(sym):
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{YSYM[sym]}?interval=1d&range=5d", headers=UA, timeout=20)
    except requests.RequestException:
        return None, None
    if r.status_code != 200:
        return None, None
    try:
        res = (r.json().get("chart") or {}).get("result") or []
        ts = res[0].get("timestamp") or []
        q = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
        cl = q.get("close") or []
    except (ValueError, AttributeError, IndexError, TypeError):
        return None, None
    for t, c in reversed(list(zip(ts, cl))):
        if c is None:
            continue
        try:
            o = datetime.fromtimestamp(int(t), tz=timezone.utc).strftime(FMT)
            return float(c), o
        except (ValueError, TypeError, OSError):
            continue
    return None, None


def fetch_stooq(sym):
    try:
        r = requests.get(f"https://stooq.com/q/l/?s={SSYM[sym]}&f=sd2t2ohlcv&h&e=csv", headers=UA, timeout=20)
    except requests.RequestException:
        return None, None
    if r.status_code != 200:
        return None, None
    try:
        lines = r.text.strip().splitlines()
        if len(lines) < 2:
            return None, None
        parts = [p.strip() for p in lines[1].split(",")]
        date, t, close = parts[1], parts[2], parts[6]
        o = datetime.strptime(f"{date}T{t}Z" if t and ":" in t else f"{date}T00:00:00Z", FMT).strftime(FMT)
        return float(close), o
    except (ValueError, IndexError):
        return None, None


def append(recs, day):
    p = OUT / (day + ".jsonl")
    s = set()
    if p.exists():
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for ln in lines:
            try:
                v = json.loads(ln)
                s.add((v.get("symbol"), v.get("obs_time")))
            except ValueError:
                continue
    w = 0
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for r in recs:
            if (r["symbol"], r["obs_time"]) in s:
                continue
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            s.add((r["symbol"], r["obs_time"]))
            w += 1
    return w


def snapshot(now):
    recs = []
    for sym in SYMS:
        val, obs, src = None, None, "yahoo"
        val, obs = fetch_yahoo(sym)
        if val is None:
            val, obs = fetch_stooq(sym)
            src = "stooq"
        if val is None:
            recs.append({"source": src, "symbol": sym, "value": None, "obs_time": None, "received_at_utc": now.strftime(FMT), "is_delayed": True, "status": "http_error"})
        else:
            recs.append({"source": src, "symbol": sym, "value": round(val, 4), "obs_time": obs, "received_at_utc": now.strftime(FMT), "is_delayed": True, "status": "ok"})
    return recs


def summary(recs):
    v = {r["symbol"]: r["value"] for r in recs if r["status"] == "ok"}
    if len(v) < 4:
        return "partial"
    o = [v["VIX9D"], v["VIX"], v["VIX3M"], v["VIX6M"]]
    if all(a < b for a, b in zip(o, o[1:])):
        return "contango"
    if all(a > b for a, b in zip(o, o[1:])):
        return "backwardation"
    return "mixed"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="VIX term snapshot VIX/VIX9D/VIX3M/VIX6M (delayed) into data/VIX_TERM/")
    ap.add_argument("--loop", action="store_true", help="repeat every --interval during ET open")
    ap.add_argument("--interval", type=int, default=900, help="loop interval sec (default 900)")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = now_utc()
        if args.loop and not et_open(now):
            print(f"skip closed {now.strftime(FMT)}")
        else:
            recs = snapshot(now)
            print(f"ok +{append(recs, now.strftime('%Y-%m-%d'))} {summary(recs)} " + " ".join(f"{r['symbol']}={r['value']}" for r in recs))
        if not args.loop:
            break
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    main()
