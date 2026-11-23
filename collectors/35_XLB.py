"""XLB (Materials Select Sector SPDR) 5-minute bar collector — Yahoo Finance public chart (keyless).

Source: https://query1.finance.yahoo.com/v8/finance/chart/XLB?range=5d&interval=5m
Query: range=1mo/5d/1d + interval=5m + includePrePost=false (regular session only, no key).
Default run: backfill last 20 trading days within provider range (5m ~1mo window).
`--loop`: ET regular session (09:30-16:00, Mon-Fri), every 5 min, completed bars only.
Yahoo provides no VWAP/trade-count -> vwap=null, n=null; notional approx ((h+l+c)/3*v) as `notional`.
Saves Path(__file__).parent/"data"/"XLB"/YYYY-MM-DD.jsonl (ET date).
Stdlib + requests only, no classes.
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = dt.timezone(dt.timedelta(hours=-5), "ET")
UTC = dt.timezone.utc

SYMBOL = "XLB"
SOURCE = "yahoo-chart"
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT_DIR = Path(__file__).parent / "data" / "XLB"


def now_utc():
    return dt.datetime.now(UTC)


def now_et():
    return now_utc().astimezone(ET)


def iso_z(d):
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def fetch_chart(rng):
    last = None
    for host in HOSTS:
        try:
            r = requests.get(f"{host}/v8/finance/chart/{SYMBOL}",
                             params={"range": rng, "interval": "5m",
                                     "includePrePost": "false"},
                             headers=UA, timeout=30)
            r.raise_for_status()
            res = (r.json().get("chart") or {}).get("result") or []
            if not res:
                last = ValueError("empty chart result")
                continue
            return res[0]
        except Exception as e:
            last = e
    raise RuntimeError(f"yahoo chart fetch failed range={rng}: {last}")


def fetch_bars(rng="1mo"):
    """Raw 5m bars (dicts t/o/h/l/c/v) in ascending time order."""
    res = fetch_chart(rng)
    ts = res.get("timestamp") or []
    quotes = (res.get("indicators") or {}).get("quote") or [{}]
    q = quotes[0] if quotes else {}
    oo, hh, ll, cc, vv = q.get("open") or [], q.get("high") or [], \
        q.get("low") or [], q.get("close") or [], q.get("volume") or []
    out = []
    for i, t in enumerate(ts):
        o = oo[i] if i < len(oo) else None
        h = hh[i] if i < len(hh) else None
        l = ll[i] if i < len(ll) else None
        c = cc[i] if i < len(cc) else None
        v = vv[i] if i < len(vv) else None
        if o is None and h is None and l is None and c is None:
            continue
        tdt = dt.datetime.fromtimestamp(int(t), tz=UTC)
        out.append({"t": tdt, "o": o, "h": h, "l": l, "c": c, "v": v})
    out.sort(key=lambda b: b["t"])
    return out


def to_record(b, received):
    t = b["t"]
    etd = t.astimezone(ET).date().isoformat()
    o, h, l, c, v = b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v")
    notion = None
    try:
        if h is not None and l is not None and c is not None and v is not None:
            notion = round(((float(h) + float(l) + float(c)) / 3.0) * float(v), 2)
    except Exception:
        notion = None
    return etd, {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": iso_z(t),
        "bar_close_utc": iso_z(t + dt.timedelta(minutes=5)),
        "received_at_utc": received, "o": o, "h": h,
        "l": l, "c": c, "v": v, "n": None,
        "vwap": None, "notional": notion, "status": "ok"}


def existing_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            k = rec.get("bar_open_utc")
            if not k:
                continue
            if rec.get("o") is None and rec.get("h") is None and \
                    rec.get("l") is None and rec.get("c") is None:
                continue
            s.add(k)
    return s


def save_grouped(records):
    groups = {}
    for day, rec in records:
        groups.setdefault(day, []).append(rec)
    for day, recs in groups.items():
        p = OUT_DIR / f"{day}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        seen = existing_keys(p)
        fresh = [r for r in recs if r["bar_open_utc"] not in seen]
        if not fresh:
            continue
        with p.open("a", encoding="utf-8") as f:
            for r in fresh:
                f.write(json.dumps(r) + "\n")
                seen.add(r["bar_open_utc"])
        print(f"saved {len(fresh)} -> {p}")


def backfill(days):
    end = now_utc()
    received = iso_z(end)
    rng = "5d" if int(days) <= 5 else "1mo"
    try:
        bars = fetch_bars(rng)
    except Exception as e:
        print(f"backfill fetch failed: {e}", file=sys.stderr)
        sys.exit(1)
    recs = [to_record(b, received) for b in bars
            if b["t"] + dt.timedelta(minutes=5) <= end]
    sessions = sorted({d for d, _ in recs})
    keep = set(sessions[-int(days):]) if len(sessions) > int(days) else set(sessions)
    recs = [(d, r) for d, r in recs if d in keep]
    save_grouped(recs)
    print(f"backfill: {len(recs)} bars over {len(keep)} sessions (range={rng})")


def in_session(t):
    return t.weekday() < 5 and (dt.time(9, 30) <= t.time() < dt.time(16, 0))


def secs_to_next_5m():
    n = now_utc()
    return 300 - (n.timestamp() % 300) + 30


def loop():
    print("loop: ET regular session, 5-min completed bars only. Ctrl+C to stop.")
    while True:
        try:
            et = now_et()
            if in_session(et):
                end = now_utc()
                received = iso_z(end)
                try:
                    bars = fetch_bars("1d")
                except Exception as e:
                    print(f"loop error: {e}", file=sys.stderr)
                    time.sleep(60)
                    continue
                recs = [to_record(b, received) for b in bars
                        if b["t"] + dt.timedelta(minutes=5) <= end]
                save_grouped(recs)
                time.sleep(secs_to_next_5m())
            else:
                time.sleep(60)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
            time.sleep(60)


def main():
    ap = argparse.ArgumentParser(description="XLB 5Min collector (Yahoo chart, keyless)")
    ap.add_argument("--loop", action="store_true", help="ET session 5-min loop")
    ap.add_argument("--days", type=int, default=20, help="trading days for one-shot")
    a = ap.parse_args()
    if a.loop:
        loop()
    else:
        backfill(a.days)


if __name__ == "__main__":
    main()
