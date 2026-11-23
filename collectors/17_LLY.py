"""LLY 5분봉+거래량 수집기 (헬스케어/섹터ETF XLV).

원천: Yahoo Finance 공개 차트 (키 불필요)
  GET https://query1.finance.yahoo.com/v8/finance/chart/LLY?interval=5m&includePrePost=false
  (query1 실패 시 query2로 대체)
동작: 기본 1회 수집(약 20거래일, 제공 범위 내) 후 종료. --loop 지정 시 ET 정규장(09:30-16:00)
  5분 완성봉만 추가 수집. Yahoo가 제공하지 않는 VWAP는 null로 보존하고
  거래대금 근삿값(notional_est=c*volume)을 별도 필드로 저장.
저장: <본파일>/data/LLY/YYYY-MM-DD.jsonl (ET 일자별, t 기준 중복금지).
사용: python 17_LLY.py [--days 20] [--loop]
의존: 표준lib + requests.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "LLY"
TAG = "LLY"
HOSTS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/LLY",
    "https://query2.finance.yahoo.com/v8/finance/chart/LLY",
]
UA = {"User-Agent": "Mozilla/5.0"}
ET = ZoneInfo("America/New_York")
DATA = Path(__file__).parent / "data" / "LLY"
SPAN = timedelta(minutes=5)


def _fetch(start, end):
    p1 = int(start.timestamp())
    p2 = int(end.timestamp())
    params = {"period1": p1, "period2": p2, "interval": "5m",
              "includePrePost": "false"}
    last_err = None
    for base in HOSTS:
        try:
            r = requests.get(base, params=params, headers=UA, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as e:
            last_err = e
            continue
        err = (j.get("chart") or {}).get("error")
        if err:
            last_err = RuntimeError(str(err))
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
        except Exception:
            last_err = RuntimeError("bad chart json")
            continue
        if not res:
            last_err = RuntimeError("empty chart result")
            continue
        res = res[0]
        ts = res.get("timestamp") or []
        quotes = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quotes.get("open") or []
        highs = quotes.get("high") or []
        lows = quotes.get("low") or []
        closes = quotes.get("close") or []
        vols = quotes.get("volume") or []
        out = []
        for i, t in enumerate(ts):
            try:
                dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
            except Exception:
                continue
            # 완성봉 후보만: 5분 경계에 정렬된 봉만 (형성중 틱 제외)
            if dt.second != 0 or dt.minute % 5 != 0:
                continue
            def _at(a):
                return a[i] if i < len(a) else None
            o, h, l, c, v = _at(opens), _at(highs), _at(lows), _at(closes), _at(vols)
            if o is None or h is None or l is None or c is None:
                continue
            iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append({"t": iso, "o": o, "h": h, "l": l, "c": c,
                        "v": int(v) if v is not None else None})
        return out
    raise RuntimeError(f"yahoo fetch failed: {last_err}")


def _rth_only(bars, completed_by=None):
    f = []
    for b in bars:
        try:
            t = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
        except Exception:
            continue
        e = t.astimezone(ET)
        if e.weekday() > 4:
            continue
        if not (timedelta(hours=9, minutes=30) <= timedelta(hours=e.hour, minutes=e.minute) < timedelta(hours=16)):
            continue
        if completed_by and t + SPAN > completed_by:
            continue
        f.append(b)
    return f


def _save(bars):
    n = 0
    for b in bars:
        try:
            t = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
        except Exception:
            continue
        day = t.astimezone(ET).strftime("%Y-%m-%d")
        fp = DATA / f"{day}.jsonl"
        fp.parent.mkdir(parents=True, exist_ok=True)
        seen = set()
        if fp.exists():
            for ln in fp.read_text(encoding="utf-8").splitlines():
                try:
                    v = json.loads(ln).get("t")
                    if v:
                        seen.add(v)
                except Exception:
                    pass
        if b["t"] in seen:
            continue
        c, v = b.get("c"), b.get("v")
        try:
            notional = float(c) * int(v) if c is not None and v is not None else None
        except Exception:
            notional = None
        rec = {"symbol": SYMBOL, "t": b["t"], "o": b["o"], "h": b["h"],
               "l": b["l"], "c": b["c"], "volume": b.get("v"), "vwap": None,
               "n": None, "notional_est": notional, "sector_etf": "XLV"}
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n += 1
    return n


def collect_once(days=20):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(days * 1.4 + 5))
    bars = _rth_only(_fetch(start, end), completed_by=end)
    return _save(bars)


def loop(days=20):
    print(f"[{SYMBOL}] loop: ET RTH 5min completed bars (Yahoo) -> {DATA}", flush=True)
    collect_once(days)
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            now_et = now_utc.astimezone(ET)
            tod = timedelta(hours=now_et.hour, minutes=now_et.minute, seconds=now_et.second)
            rth = now_et.weekday() < 5 and timedelta(hours=9, minutes=30) <= tod < timedelta(hours=16, minutes=5)
            if rth:
                end = now_utc
                start = end - timedelta(days=2)
                n = _save(_rth_only(_fetch(start, end), completed_by=now_utc))
                print(f"{now_et:%Y-%m-%d %H:%M} RTH +{n}", flush=True)
                nxt = (now_utc.replace(second=0, microsecond=0) + timedelta(minutes=5 - now_utc.minute % 5)).replace(tzinfo=None)
                sl = max(30, (nxt - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() + 45)
            else:
                sl = 120
            time.sleep(sl)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"warn: {e}", flush=True)
            time.sleep(60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    if a.loop:
        loop(a.days)
    else:
        print(f"saved {collect_once(a.days)} bars")


if __name__ == "__main__":
    main()
