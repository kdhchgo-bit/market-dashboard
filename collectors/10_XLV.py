"""XLV (Health Care Select Sector SPDR) 5분봉 수집기.

원천: Yahoo Finance 공개 차트 (키 불필요)
Endpoint: GET https://query1.finance.yahoo.com/v8/finance/chart/XLV?range={5d,1mo}&interval=5m
  (query1 실패 시 query2.finance.yahoo.com 대체)
동작: 1회 수집(최근 20거래일, 제공 범위 내 백필) + --loop 시 ET 정규장(평일 09:30-16:00) 5분마다 완성봉만 추가.
저장: Path(__file__).parent/"data"/"XLV"/YYYY-MM-DD.jsonl (ET 일자별, 같은 스키마, t 기준 중복금지)
스키마: {"symbol":"XLV","t":ISO8601,"o":float,"h":float,"l":float,"c":float,"v":int,"n":null,"vw":null,"notional":float|null}
  - Yahoo가 제공하지 않는 거래건수(n)/VWAP(vw)는 null로 보존.
  - notional = c*v 거래대금 근삿값(별도 필드, 기존 필드 유지).
사용: python 10_XLV.py [--days 20] [--loop]
의존성: 표준lib + requests만.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "XLV"
TAG = "XLV"
INTERVAL = "5m"
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")
OUTDIR = Path(__file__).parent / "data" / SYMBOL
BAR_MIN = 5


def et_now():
    return datetime.now(ET)


def fetch_chart(range_):
    """Yahoo chart 5분봉 원시 반환: (timestamps, quote, regular_market_time)."""
    last_err = None
    for host in HOSTS:
        try:
            r = requests.get(
                f"https://{host}/v8/finance/chart/{SYMBOL}",
                params={"range": range_, "interval": INTERVAL},
                headers=UA, timeout=30)
            if r.status_code != 200:
                last_err = f"{host} http={r.status_code}"
                continue
            j = r.json()
            chart = j.get("chart") or {}
            if chart.get("error"):
                last_err = f"{host} err={chart['error']}"
                continue
            res = (chart.get("result") or [None])[0]
            if not res:
                last_err = f"{host} empty_result"
                continue
            ts = res.get("timestamp") or []
            quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            rmt = (res.get("meta") or {}).get("regularMarketTime")
            return ts, quote, rmt
        except Exception as e:
            last_err = f"{host} {type(e).__name__}"
    print(f"fetch warn: {last_err}", file=sys.stderr)
    return [], {}, None


def completed(range_):
    """정규장 완성 5분봉만 (t, o, h, l, c, v) 리스트로 반환."""
    now_utc = datetime.now(timezone.utc)
    ts, quote, rmt = fetch_chart(range_)
    if rmt:
        try:
            cutoff = datetime.fromtimestamp(int(rmt), tz=timezone.utc)
            if cutoff > now_utc:
                cutoff = now_utc
        except Exception:
            cutoff = now_utc
    else:
        cutoff = now_utc
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    res = []
    for i, t0 in enumerate(ts):
        try:
            o = opens[i] if i < len(opens) else None
            h = highs[i] if i < len(highs) else None
            l = lows[i] if i < len(lows) else None
            c = closes[i] if i < len(closes) else None
        except IndexError:
            continue
        if o is None or h is None or l is None or c is None:
            continue
        t_utc = datetime.fromtimestamp(int(t0), tz=timezone.utc)
        if t_utc + timedelta(minutes=BAR_MIN) > cutoff:
            continue
        t_et = t_utc.astimezone(ET)
        if t_et.weekday() >= 5:
            continue
        if not (timedelta(hours=9, minutes=30) <= timedelta(
                hours=t_et.hour, minutes=t_et.minute) < timedelta(hours=16)):
            continue
        v = vols[i] if i < len(vols) else None
        res.append((t_utc, float(o), float(h), float(l), float(c),
                    int(v) if v is not None else None))
    return res


def load_keys(path):
    if not path.exists():
        return set()
    ks = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            t = json.loads(line).get("t")
        except Exception:
            continue
        if t:  # 오류행(null t)이 뒤늦은 실측 저장을 막지 않게
            ks.add(t)
    return ks


def store(rows):
    by_day = {}
    for t_utc, o, h, l, c, v in rows:
        t_et = t_utc.astimezone(ET)
        by_day.setdefault(t_et.date().isoformat(), []).append((t_utc, o, h, l, c, v))
    for day, items in by_day.items():
        p = OUTDIR / f"{day}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        seen = load_keys(p)
        lines = []
        for t_utc, o, h, l, c, v in sorted(items, key=lambda x: x[0]):
            t = t_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            if t in seen:
                continue
            seen.add(t)
            rec = {"symbol": SYMBOL, "t": t, "o": o, "h": h,
                   "l": l, "c": c, "v": v,
                   "n": None, "vw": None,
                   "notional": (c * v) if (c is not None and v is not None) else None}
            lines.append(json.dumps(rec, separators=(",", ":")))
        if lines:
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        print(f"{day}: +{len(lines)} -> {p}")


def collect(days):
    # Yahoo 5m은 최근 60일 이내만 제공: 20거래일 백필은 1mo, 단기는 5d
    range_ = "1mo" if days >= 8 else "5d"
    store(completed(range_))


def in_session(now):
    return now.weekday() < 5 and timedelta(hours=9, minutes=30) <= timedelta(
        hours=now.hour, minutes=now.minute, seconds=now.second) < timedelta(hours=16)


def sleep_to_next():
    now = et_now()
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=BAR_MIN - now.minute % BAR_MIN)
    time.sleep(max(1, (nxt - now).total_seconds() + 5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    collect(a.days)
    if not a.loop:
        return
    print("loop: ET 정규장 5분마다 완성봉 수집 (Ctrl+C 종료)")
    while True:
        try:
            if in_session(et_now()):
                collect(2)
            sleep_to_next()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"loop warn: {e}", file=sys.stderr)
            time.sleep(60)


if __name__ == "__main__":
    main()
