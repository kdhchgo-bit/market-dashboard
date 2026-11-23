"""GOOGL 5분봉 수집기 (커뮤니케이션 대표, 섹터 XLC).
원천: Yahoo Finance 공개 차트 (키 불필요) — https://query1.finance.yahoo.com/v8/finance/chart/GOOGL?range=5d&interval=5m
정규장 완성 5분봉 OHLCV만 저장. 원천 미제공 VWAP는 null 보존, 거래대금 근삿값은 notional_est 별도 필드.
용법: python 53_GOOGL.py (최근 20거래일 1회) / python 53_GOOGL.py --loop (ET 정규장 5분 폴링, 완성봉만).
저장: <this_dir>/data/GOOGL/YYYY-MM-DD.jsonl (bar_open_utc 날짜별, 유효중복금지).
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "GOOGL"
SECTOR = "XLC"
SOURCE = "yahoo-chart"
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
INTERVAL = "5m"
UA = {"User-Agent": "Mozilla/5.0"}
OUT = Path(__file__).parent / "data" / "GOOGL"


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


def _et_of(bt):
    try:
        from zoneinfo import ZoneInfo
        return bt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return bt - timedelta(hours=4)


def fetch(start, end):
    """Yahoo chart period1/period2 5분봉을 Alpaca-b bar 호환 dict 목록으로 반환."""
    params = {"period1": int(start.timestamp()), "period2": int(end.timestamp()),
              "interval": INTERVAL, "includePrePost": "false", "events": "div|split"}
    last_exc = None
    for host in HOSTS:
        try:
            r = requests.get(f"{host}/v8/finance/chart/{SYMBOL}", params=params,
                             headers=UA, timeout=30)
            r.raise_for_status()
            j = r.json()
            res = (j.get("chart", {}).get("result") or [None])[0]
            if not res:
                last_exc = RuntimeError(f"yahoo empty result: {j.get('chart', {}).get('error')}")
                continue
            ts = res.get("timestamp") or []
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            opens, highs, lows = q.get("open") or [], q.get("high") or [], q.get("low") or []
            closes, vols = q.get("close") or [], q.get("volume") or []
            out = []
            for i, tsec in enumerate(ts):
                try:
                    o = opens[i] if i < len(opens) else None
                    h = highs[i] if i < len(highs) else None
                    lo = lows[i] if i < len(lows) else None
                    c = closes[i] if i < len(closes) else None
                    v = vols[i] if i < len(vols) else None
                except Exception:
                    continue
                bt = datetime.fromtimestamp(tsec, tz=timezone.utc)
                iso = bt.isoformat().replace("+00:00", "Z")
                out.append({"t": iso, "o": o, "h": h, "l": lo, "c": c, "v": v})
            return out
        except Exception as e:
            last_exc = e
            continue
    raise RuntimeError(f"yahoo fetch failed: {last_exc}")


def seen_keys(path):
    """유효 실측(bar_open_utc + OHLC 유효)만 중복 기준. 과거 오류/널 행은 뒤늦은 실측 저장을 막지 않는다."""
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = r.get("bar_open_utc")
            if t and r.get("o") is not None and r.get("h") is not None \
                    and r.get("l") is not None and r.get("c") is not None:
                s.add(t)
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
            continue  # 완성봉만 (형성 중 봉 제외)
        if not in_regular(_et_of(bt)):
            continue  # 정규장만
        o, h, lo, c, v = b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v")
        if o is None or h is None or lo is None or c is None:
            continue  # null뿐인 행 저장 안 함
        day = t[:10]
        path = OUT / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if t in seen_keys(path):
            continue
        try:
            notional = (float(c) * float(v)) if (c is not None and v is not None) else None
        except Exception:
            notional = None
        rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR, "bar_open_utc": t,
               "received_at_utc": now, "o": o, "h": h, "l": lo,
               "c": c, "v": v, "n": None, "vwap": None, "notional_est": notional,
               "status": status}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n_new += 1
    return n_new


def once(days=20):
    end = utcnow()
    start = end - timedelta(days=int(days * 1.5 + 5))  # 20거래일 커버
    bars = fetch(start, end)
    n = save(bars)
    print(f"fetched={len(bars)} new={n}")


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
            print(f"loop error: {e}", file=__import__("sys").stderr)
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
