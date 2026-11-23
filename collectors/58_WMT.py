"""WMT 5분봉 수집기 (필수소비재 대표, 섹터 XLP).
원천: Yahoo Finance 공개 차트 (키 불필요) — https://query1.finance.yahoo.com/v8/finance/chart/WMT
요청: range 또는 period1/period2 + interval=5m + includePrePost=false (정규장만).
인증: 없음 (User-Agent 헤더만 사용).
용법: python 58_WMT.py (최근 20거래일 1회) / python 58_WMT.py --loop (ET 정규장 5분 폴링, 완성봉만).
저장: <this_dir>/data/WMT/YYYY-MM-DD.jsonl (bar_open_utc 날짜별, 중복금지).
필드: source/symbol/sector/bar_open_utc/received_at_utc/o/h/l/c/v/n/vwap/status 유지.
  Yahoo는 VWAP·거래건수(n)를 제공하지 않으므로 vwap=null, n=null으로 보존하고,
  거래대금 근삿값(notional_approx=c*v)은 별도 필드로 표시한다.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "WMT"
SECTOR = "XLP"
SOURCE = "yahoo-chart"
HOSTS = ("query1", "query2")
INTERVAL = "5m"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
OUT = Path(__file__).parent / "data" / "WMT"


def utcnow():
    return datetime.now(timezone.utc)


def floor5(dt):
    return dt.replace(minute=dt.minute // 5 * 5, second=0, microsecond=0)


def et_now():
    try:
        from zoneinfo import ZoneInfo
        return utcnow().astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return utcnow() - timedelta(hours=5 if time.localtime().tm_isdst == 0 else 4)


def in_regular(et):
    return et.weekday() < 5 and (et.hour, et.minute) >= (9, 30) and (et.hour, et.minute) < (16, 0)


def _parse_chart(j):
    chart = j.get("chart") or {}
    err = chart.get("error")
    if err:
        raise RuntimeError(f"yahoo chart error: {err}")
    res = (chart.get("result") or [None])[0]
    if not res:
        raise RuntimeError("yahoo empty result")
    ts = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    bars = []
    for i, t in enumerate(ts):
        o = (quote.get("open") or [None] * len(ts))[i]
        h = (quote.get("high") or [None] * len(ts))[i]
        l = (quote.get("low") or [None] * len(ts))[i]
        c = (quote.get("close") or [None] * len(ts))[i]
        v = (quote.get("volume") or [None] * len(ts))[i]
        if o is None and h is None and l is None and c is None:
            continue  # null뿐인 행은 저장하지 않음
        bt = datetime.fromtimestamp(t, timezone.utc)
        bars.append({"t": bt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "o": o, "h": h, "l": l, "c": c, "v": v})
    return bars


def fetch(start, end):
    p = {"period1": int(start.timestamp()), "period2": int(end.timestamp()),
         "interval": INTERVAL, "includePrePost": "false", "events": "div"}
    last_err = None
    for host in HOSTS:
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
                             headers=UA, params=p, timeout=30)
        except Exception as e:
            last_err = f"{host} http_error: {e}"
            continue
        if r.status_code != 200:
            last_err = f"{host} http_{r.status_code}"
            continue
        try:
            return _parse_chart(r.json())
        except Exception as e:
            last_err = f"{host} parse_error: {e}"
            continue
    raise RuntimeError(last_err or "yahoo fetch failed")


def seen_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                s.add(json.loads(line).get("bar_open_utc"))
            except Exception:
                pass
    return s


def save(bars, status="ok"):
    now = utcnow().isoformat().replace("+00:00", "Z")
    n_new = 0
    for b in bars:
        t = b.get("t", "")
        try:
            bt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            continue
        if bt + timedelta(minutes=5) > utcnow():
            continue  # 완성봉만
        day = t[:10]
        path = OUT / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if t in seen_keys(path):
            continue
        c, v = b.get("c"), b.get("v")
        try:
            notion = round(float(c) * float(v), 2) if c is not None and v is not None else None
        except Exception:
            notion = None
        rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR, "bar_open_utc": t,
               "received_at_utc": now, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
               "c": c, "v": v, "n": None, "vwap": None,
               "notional_approx": notion, "status": status}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n_new += 1
    return n_new


def once(days=20):
    end = utcnow()
    start = end - timedelta(days=int(days * 1.5 + 5))  # 20거래일 커버
    try:
        bars = fetch(start, end)
    except Exception as e:
        print(f"fetch_error: {e}", file=sys.stderr)
        return
    n = save(bars)
    done = [b for b in bars
            if datetime.fromisoformat(b["t"].replace("Z", "+00:00")) + timedelta(minutes=5) <= utcnow()]
    last = max((b["t"] for b in done), default=None)
    delay = (utcnow() - (datetime.fromisoformat(last.replace("Z", "+00:00")) + timedelta(minutes=5))
             ).total_seconds() / 60 if last else None
    print(f"fetched={len(bars)} new={n} last_open={last} "
          f"delay_min={round(delay, 1) if delay is not None else None}")


def loop():
    while True:
        try:
            et = et_now()
            if in_regular(et):
                end = floor5(utcnow())
                bars = fetch(end - timedelta(minutes=15), end)
                n = save(bars)
                print(f"{et.isoformat()} fetched={len(bars)} new={n}", flush=True)
            else:
                print(f"{et.isoformat()} out-of-session sleep", flush=True)
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
        time.sleep(300)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    if a.loop:
        loop()
    else:
        once(a.days)


if __name__ == "__main__":
    main()
