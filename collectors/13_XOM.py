"""XOM (Energy / XLE) 5-minute bars + volume collector (keyless).

Source: Yahoo Finance public chart (no key):
  GET https://query1.finance.yahoo.com/v8/finance/chart/XOM?range=1mo&interval=5m
  (User-Agent header only; unofficial path, actual delay checked per run.)
Modes:
  one-shot (default): last ~20 trading days (range=1mo, provider limit), ET regular session, completed 5m bars.
  --loop: poll ET regular session, persist only completed 5m bars.
Store: Path(__file__).parent/"data"/"XOM"/YYYY-MM-DD.jsonl, dedup on bar time `t`.
Fields kept: symbol/sector/tf/feed/t/o/h/l/c/v/n/vwap (+ notional approx as separate field).
  vwap=null (source provides no VWAP); n=null (no trade count); notional~=c*v.
Deps: stdlib + requests only.
"""
import argparse
import json
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "XOM"
SECTOR = "XLE"
TF = "5Min"
FEED = "yahoo-5m"
BASE = f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")
OUT = Path(__file__).parent / "data" / SYMBOL
GRID = 300  # 5-minute grid seconds


def fetch(range_="1mo"):
    r = requests.get(BASE, headers=UA,
                     params={"range": range_, "interval": "5m"},
                     timeout=30)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return []
    res = res[0]
    ts = res.get("timestamp") or []
    quotes = (res.get("indicators") or {}).get("quote") or [{}]
    q = quotes[0]
    bars = []
    for i, t in enumerate(ts):
        try:
            o = q.get("open", [None])[i]
            c = q.get("close", [None])[i]
        except IndexError:
            continue
        if o is None and c is None:
            continue  # null-only row: not a real bar
        try:
            bars.append({
                "t": int(t) - (int(t) % GRID),  # floor to 5m grid (UTC epoch)
                "o": o,
                "h": (q.get("high", [None])[i] if i < len(q.get("high") or []) else None),
                "l": (q.get("low", [None])[i] if i < len(q.get("low") or []) else None),
                "c": c,
                "v": (q.get("volume", [None])[i] if i < len(q.get("volume") or []) else None),
            })
        except IndexError:
            continue
    return bars


def completed(b, now):
    bt = datetime.fromtimestamp(b["t"], tz=timezone.utc)
    if bt + timedelta(seconds=GRID) > now:
        return None  # still-forming bar
    e = bt.astimezone(ET)
    if e.weekday() >= 5 or not (9 * 60 + 30 <= e.hour * 60 + e.minute < 16 * 60):
        return None  # regular session only
    return bt


def seen_for(path):
    s = set()
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                t = json.loads(ln).get("t")
            except Exception:
                continue
            if t:  # error rows without `t` never block late real bars
                s.add(t)
    return s


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(bars, now):
    n = 0
    for b in bars:
        bt = completed(b, now)
        if not bt:
            continue
        t = iso_z(bt)
        p = OUT / f"{bt.astimezone(ET).date().isoformat()}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        if t in seen_for(p):
            continue
        c, v = b.get("c"), b.get("v")
        try:
            notion = round(float(c) * float(v), 2) if c is not None and v is not None else None
        except (TypeError, ValueError):
            notion = None
        rec = {"symbol": SYMBOL, "sector": SECTOR, "tf": TF, "feed": FEED,
               "t": t, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
               "c": c, "v": v, "n": None, "vwap": None, "notional": notion}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n += 1
    return n


def one_shot():
    now = datetime.now(timezone.utc)
    return save(fetch("1mo"), now)


def loop():
    print("loop XOM 5Min Yahoo ET regular-session, completed bars only", flush=True)
    while True:
        now = datetime.now(ET)
        wd = now.weekday() < 5 and 9 * 60 + 30 <= now.hour * 60 + now.minute < 16 * 60
        if wd:
            try:
                utc = datetime.now(timezone.utc)
                print(f"poll {now.isoformat()} +{save(fetch('5d'), utc)}", flush=True)
            except Exception as ex:
                print(f"warn {ex}", flush=True)
        wait = 300 - (now.minute * 60 + now.second) % 300 + 5
        _time.sleep(wait)


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--loop", action="store_true")
    o = a.parse_args()
    if o.loop:
        loop()
    else:
        print(f"saved {one_shot()} bars", flush=True)


if __name__ == "__main__":
    main()
