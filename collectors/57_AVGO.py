"""AVGO 5분봉 수집기 (기술업종, 섹터 XLK).
원천: Yahoo Finance 공개 차트 (키 불필요) — https://query1.finance.yahoo.com/v8/finance/chart/AVGO?range=5d&interval=5m
GET https://query1.finance.yahoo.com/v8/finance/chart/AVGO?period1={s}&period2={e}&interval=5m&includePrePost=false
정규장 완성 5분봉 OHLCV만 저장. 원천 미제공 VWAP는 null, 거래대금 근삿값은 notional_est 별도 필드.
용법: python 57_AVGO.py (최근 20거래일 1회) / python 57_AVGO.py --loop (ET 정규장 5분 폴링, 완성봉만).
저장: <this_dir>/data/AVGO/YYYY-MM-DD.jsonl (bar_open_utc 날짜별, 중복금지).
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "AVGO"
SECTOR = "XLK"
SOURCE = "yahoo-chart"
BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/AVGO",
    "https://query2.finance.yahoo.com/v8/finance/chart/AVGO",
]
INTERVAL = "5m"
OUT = Path(__file__).parent / "data" / "AVGO"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


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


def fetch(start, end):
    """Yahoo chart 5m봉 조회. 완성 여부와 무관하게 원천 봉 목록 반환."""
    params = {"period1": int(start.timestamp()), "period2": int(end.timestamp()),
              "interval": INTERVAL, "includePrePost": "false"}
    last_err = None
    for base in BASES:
        try:
            r = requests.get(base, params=params, headers=UA, timeout=30)
            r.raise_for_status()
            j = r.json()
            err = (j.get("chart") or {}).get("error")
            if err:
                last_err = err
                continue
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                last_err = "empty result"
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
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                lo = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = vols[i] if i < len(vols) else None
                if o is None and h is None and lo is None and c is None and v is None:
                    continue  # null뿐인 행 저장 안 함
                bt = datetime.fromtimestamp(t, tz=timezone.utc)
                out.append({"t": bt.isoformat().replace("+00:00", "Z"),
                            "o": o, "h": h, "l": lo, "c": c, "v": v})
            return out
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"yahoo fetch failed: {last_err}")


def seen_keys(path):
    """유효 실측 행의 bar_open_utc 집합. 과거 오류(null/비정상) 행은 뒤늦은 실측 저장을 막지 않게 제외."""
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("status") not in (None, "ok"):
                continue
            if d.get("c") is None:
                continue
            k = d.get("bar_open_utc")
            if k:
                s.add(k)
        # 구 Alpaca 오류행 등 비표준 키가 있어도 무시
    return s


def save(bars, status="ok"):
    now = utcnow().isoformat().replace("+00:00", "Z")
    cur = utcnow()
    n_new = 0
    for b in bars:
        t = b.get("t", "")
        try:
            bt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            continue
        if bt + timedelta(minutes=5) > cur:
            continue  # 완성봉만
        day = t[:10]
        path = OUT / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if t in seen_keys(path):
            continue
        c, v = b.get("c"), b.get("v")
        notion = round(c * v, 2) if isinstance(c, (int, float)) and isinstance(v, (int, float)) else None
        rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR, "bar_open_utc": t,
               "received_at_utc": now, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
               "c": c, "v": v, "n": None, "vwap": None, "notional_est": notion, "status": status}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n_new += 1
    return n_new


def once(days=20):
    end = utcnow()
    start = end - timedelta(days=int(days * 1.5 + 5))  # 20거래일 커버
    bars = fetch(start, end)
    n = save(bars)
    last = bars[-1]["t"] if bars else None
    print(f"source={SOURCE} fetched={len(bars)} new={n} last_bar_open_utc={last}")


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
            print(f"loop error: {e}")
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
