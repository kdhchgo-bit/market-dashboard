"""JNJ (Healthcare, Sector ETF XLV) 5-min bars + volume collector.

Source: Yahoo Finance public chart (no key)
  GET https://query1.finance.yahoo.com/v8/finance/chart/JNJ
  params: period1, period2 (unix), interval=5m, includePrePost=false
  Fallback host: query2.finance.yahoo.com

Usage:
  python 18_JNJ.py            # one-shot: last 20 trading days, completed 5-min bars
  python 18_JNJ.py --loop     # ET regular session (09:30-16:00, Mon-Fri), completed bars only

Storage: Path(__file__).parent/"data"/"JNJ"/YYYY-MM-DD.jsonl (ET date split, dedup by bar time `t`).
Line schema: {"symbol","t","o","h","l","c","v","vw","n","amt"}
  t  = bar open UTC "%Y-%m-%dT%H:%M:%SZ"; vw = null (Yahoo has no VWAP);
  n  = null (Yahoo has no trade count); amt = round(c*v,2) turnover approx (null if missing).
Stdlib + requests only.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "JNJ"
YAHOO = "JNJ"
URLS = [
    f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO}",
    f"https://query2.finance.yahoo.com/v8/finance/chart/{YAHOO}",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")
BASE = Path(__file__).parent / "data" / SYMBOL


def _parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def fetch(start_iso, end_iso):
    try:
        s_dt = _parse(start_iso)
    except Exception:
        s_dt = datetime.now(timezone.utc) - timedelta(days=35)
    try:
        e_dt = _parse(end_iso)
    except Exception:
        e_dt = datetime.now(timezone.utc)
    if s_dt.tzinfo is None:
        s_dt = s_dt.replace(tzinfo=timezone.utc)
    if e_dt.tzinfo is None:
        e_dt = e_dt.replace(tzinfo=timezone.utc)
    p = {"period1": int(s_dt.timestamp()), "period2": int(e_dt.timestamp()),
         "interval": "5m", "includePrePost": "false"}
    last = None
    for url in URLS:
        try:
            r = requests.get(url, headers=UA, params=p, timeout=30)
        except Exception as e:
            last = e
            continue
        if r.status_code != 200:
            last = RuntimeError(f"http {r.status_code}")
            continue
        try:
            j = r.json()
        except Exception as e:
            last = e
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
        except Exception:
            res = []
        if not res:
            last = RuntimeError(f"empty chart: {str(j)[:120]}")
            continue
        r0 = res[0]
        ts = r0.get("timestamp") or []
        q = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
        opens, highs, lows, closes, vols = (q.get("open") or [], q.get("high") or [],
                                            q.get("low") or [], q.get("close") or [],
                                            q.get("volume") or [])
        out = []
        for i, t in enumerate(ts):
            o = opens[i] if i < len(opens) else None
            h = highs[i] if i < len(highs) else None
            lo = lows[i] if i < len(lows) else None
            c = closes[i] if i < len(closes) else None
            v = vols[i] if i < len(vols) else None
            if o is None and h is None and lo is None and c is None:
                continue
            dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
            out.append({"t": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "o": o, "h": h, "l": lo, "c": c, "v": v})
        out.sort(key=lambda x: x["t"])
        return out
    raise RuntimeError(f"yahoo chart failed: {last}")


def completed(bars, now):
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_ts = now.timestamp()
    return [b for b in bars if _parse(b["t"]).timestamp() + 300 <= now_ts]


def reg_open(dt_et):
    return dt_et.weekday() < 5 and dtime(9, 30) <= dt_et.time() < dtime(16, 0)


def save(bars):
    BASE.mkdir(parents=True, exist_ok=True)
    groups = {}
    for b in bars:
        et = _parse(b["t"]).astimezone(ET)
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
                c, v = b.get("c"), b.get("v")
                try:
                    amt = round(float(c) * int(v), 2) if c is not None and v is not None else None
                except Exception:
                    amt = None
                f.write(json.dumps({"symbol": SYMBOL, "t": b["t"], "o": b.get("o"),
                                    "h": b.get("h"), "l": b.get("l"), "c": c,
                                    "v": v, "vw": None,
                                    "n": None, "amt": amt}) + "\n")
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
    n = save(completed(fetch(s.isoformat(), now.isoformat()), utc))
    print(f"[JNJ] one-shot +{n} bars ({a.days}d window from {s.date()}) -> {BASE}")
    if not a.loop:
        return
    while True:
        try:
            now = datetime.now(ET)
            if not reg_open(now):
                time.sleep(60)
                continue
            utc = datetime.now().astimezone()
            w0 = (utc - timedelta(days=2)).isoformat()
            n2 = save(completed(fetch(w0, utc.isoformat()), utc))
            print(f"[JNJ] loop +{n2} @ {now:%Y-%m-%d %H:%M} ET", flush=True)
            time.sleep(secs_to_next5(datetime.now().astimezone()))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[JNJ] warn: {e}", flush=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
