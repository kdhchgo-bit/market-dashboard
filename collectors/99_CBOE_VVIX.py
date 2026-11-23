"""VVIX 스냅샷 수집(딜레이, 키 불필요).
Usage: python 99_CBOE_VVIX.py [--loop] [--interval-min 15] [--hours 24]
  1회: CBOE/Stooq/Yahoo 조회 후 스냅샷 저장 | --loop: 24시간 동안 장중만 interval 간격 반복
  원천: https://cdn.cboe.com/api/global/delayed_quotes/indices/VVIX.json (delayed)
        https://stooq.com/q/l/?s=^vvix&f=sd2t2ohlcv&h&e=csv
        https://query1.finance.yahoo.com/v8/finance/chart/^VVIX
  out: data/VVIX/YYYY-MM-DD.jsonl (일자=ET 기준)
  레코드: {source,symbol,value,obs_time,received_at_utc,is_delayed,status}
  docs: https://www.cboe.com/delayed_quotes/vvix, https://stooq.com/db/h/
"""
import argparse
import csv
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "VVIX"
OUT = Path(__file__).parent / "data" / SYM
UA = {"User-Agent": "Mozilla/5.0 (collector; vvix-snapshot)"}
CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/indices/VVIX.json"
STOOQ = "https://stooq.com/q/l/?s=^vvix&f=sd2t2ohlcv&h&e=csv"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVVIX"


def et_now():
    u = datetime.now(timezone.utc)
    y = u.year
    s = (datetime(y, 3, 1, tzinfo=timezone.utc) + timedelta(days=(6 - datetime(y, 3, 1, tzinfo=timezone.utc).weekday()) % 7 + 7)).replace(hour=7)
    e = (datetime(y, 11, 1, tzinfo=timezone.utc) + timedelta(days=(6 - datetime(y, 11, 1, tzinfo=timezone.utc).weekday()) % 7)).replace(hour=6)
    dst = s <= u < e
    return u, u - (timedelta(hours=4) if dst else timedelta(hours=5)), dst


def et_wall_to_utc(ds, ts):
    s = (ds or "").strip() + ((" " + ts.strip()) if ts and ts.strip() else "")
    dt = None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, f)
            break
        except ValueError:
            pass
    if dt is None:
        return None
    u, _, dst = et_now()
    off = timedelta(hours=-4) if dst else timedelta(hours=-5)
    return (dt.replace(tzinfo=timezone.utc) - off).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_stooq():
    r = requests.get(STOOQ, headers=UA, timeout=30)
    if r.status_code == 429:
        raise RuntimeError("rate_limited")
    r.raise_for_status()
    rows = list(csv.DictReader(r.text.splitlines()))
    if not rows:
        raise RuntimeError("empty")
    k = {kk.strip(): (vv or "").strip() for kk, vv in rows[0].items() if kk}
    v = k.get("Close") or k.get("close")
    if not v or v.upper() == "N/D":
        raise RuntimeError("delayed")
    return float(v), et_wall_to_utc(k.get("Date", ""), k.get("Time", ""))


def fetch_cboe():
    r = requests.get(CBOE, headers=UA, timeout=30)
    if r.status_code == 429:
        raise RuntimeError("rate_limited")
    r.raise_for_status()
    payload = r.json()
    d = payload.get("data", payload) if isinstance(payload, dict) else {}
    for kf in ("last_price", "price", "close", "current_price", "last"):
        if d.get(kf) not in (None, ""):
            v = float(str(d[kf]).replace(",", ""))
            break
    else:
        raise RuntimeError("empty")
    t = d.get("last_trade_time") or d.get("trade_time") or d.get("updated") or d.get("timestamp")
    obs = None
    if t:
        try:
            obs = fmt_utc(datetime.fromisoformat(str(t).replace("Z", "+00:00")))
        except ValueError:
            obs = None
    return v, obs


def fetch_yahoo():
    r = requests.get(YAHOO, headers=UA, timeout=30)
    if r.status_code == 429:
        raise RuntimeError("rate_limited")
    r.raise_for_status()
    m = r.json()["chart"]["result"][0]["meta"]
    v = m.get("regularMarketPrice")
    if v is None:
        raise RuntimeError("empty")
    ts = m.get("regularMarketTime")
    obs = fmt_utc(datetime.fromtimestamp(int(ts), tz=timezone.utc)) if ts else None
    return float(v), obs


def fetch_all():
    out = []
    for name, fn in (("cboe", fetch_cboe), ("stooq", fetch_stooq), ("yahoo", fetch_yahoo)):
        try:
            v, obs = fn()
            out.append((name, v, obs, "ok"))
        except RuntimeError as e:
            out.append((name, None, None, str(e)))
        except Exception:
            out.append((name, None, None, "http_error"))
    return out


def is_closed(et):
    return et.weekday() >= 5 or not (timedelta(hours=9, minutes=30) <= timedelta(hours=et.hour, minutes=et.minute) < timedelta(hours=16, minutes=15))


def save(rows):
    u, et, _ = et_now()
    recv = u.strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / f"{et.strftime('%Y-%m-%d')}.jsonl"
    seen = set()
    if fp.exists():
        for ln in fp.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                seen.add((d.get("source"), d.get("obs_time")))
            except Exception:
                pass
    n = 0
    with fp.open("a", encoding="utf-8") as f:
        for src, v, obs, st in rows:
            if (src, obs) in seen:
                continue
            f.write(json.dumps({"source": src, "symbol": SYM, "value": v, "obs_time": obs,
                             "received_at_utc": recv, "is_delayed": True, "status": st}) + "\n")
            seen.add((src, obs))
            n += 1 if st == "ok" else 0
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval-min", type=float, default=15)
    ap.add_argument("--hours", type=float, default=24)
    a = ap.parse_args()
    if not a.loop:
        rows = fetch_all()
        print(f"once saved={save(rows)} rows={rows}")
        return
    end = time.time() + a.hours * 3600
    while time.time() < end:
        _, et, _ = et_now()
        if is_closed(et):
            print(f"closed {et:%Y-%m-%d %H:%M} ET skip")
            time.sleep(300)
            continue
        rows = fetch_all()
        print(f"loop {et:%H:%M} ET saved={save(rows)} rows={rows}")
        time.sleep(max(60, a.interval_min * 60))


if __name__ == "__main__":
    main()
