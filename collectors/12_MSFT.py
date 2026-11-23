"""MSFT 5분봉+거래량 수집기 (Yahoo Finance 공개 차트, 키 불필요).

원천: https://query1.finance.yahoo.com/v8/finance/chart/MSFT?range=5d&interval=5m
  GET /v8/finance/chart/MSFT?range={1mo|5d}&interval=5m&includePrePost=false
  인증 없음 (공개 차트, UA 헤더 사용, query1->query2 폴백).
심볼: MSFT (기술, 섹터ETF XLK).
동작: 1회 수집(최근 20거래일 ~= range=1mo, 제공 범위 내) + --loop 시 ET 정규장(평일 09:30-16:00) 5분 완성봉만 수집.
저장: Path(__file__).parent/"data"/"MSFT"/YYYY-MM-DD.jsonl (ET 거래일자별, 중복금지).
레코드: source/symbol/sector/bar_open_utc/received_at_utc/o/h/l/c/v/n/vwap/status + notional(거래대금 근삿값 c*v).
  - Yahoo가 제공하지 않는 n(체결수)/vwap는 null 보존.
사용: python 12_MSFT.py [--loop]
의존: 표준lib + requests.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "MSFT"
SECTOR = "XLK"
SOURCE = "yahoo-chart"
INTERVAL = "5m"
ET = ZoneInfo("America/New_York")
BASE_DIR = Path(__file__).parent / "data" / SYMBOL
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")


def _fetch_chart(range_str):
    params = {"range": range_str, "interval": INTERVAL, "includePrePost": "false"}
    last_ex = None
    for host in HOSTS:
        try:
            r = requests.get(f"{host}/v8/finance/chart/{SYMBOL}",
                             params=params, headers=UA, timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as ex:
            last_ex = ex
            continue
        try:
            err = (j.get("chart") or {}).get("error")
            if err:
                last_ex = RuntimeError(str(err))
                continue
            res = ((j.get("chart") or {}).get("result") or [])
            if not res:
                last_ex = RuntimeError("empty yahoo result")
                continue
            return res[0]
        except Exception as ex:
            last_ex = ex
            continue
    raise RuntimeError(f"yahoo chart fetch failed: {last_ex}")


def _parse_result(res):
    ts = res.get("timestamp") or []
    quotes = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    vols = quotes.get("volume") or []
    out = []
    n = min(len(ts), len(opens), len(highs), len(lows), len(closes), len(vols))
    for i in range(n):
        try:
            bt = datetime.fromtimestamp(ts[i], tz=timezone.utc)
        except Exception:
            continue
        out.append({"t": bt, "o": opens[i], "h": highs[i],
                    "l": lows[i], "c": closes[i], "v": vols[i]})
    return out


def fetch_range_backfill():
    """최근 20거래일 백필 (제공 범위 내, range=1mo)."""
    return _parse_result(_fetch_chart("1mo"))


def fetch_recent():
    """루프용 최근 구간 (range=5d)."""
    return _parse_result(_fetch_chart("5d"))


def _in_regular(bt):
    e = bt.astimezone(ET)
    if e.weekday() >= 5:
        return False
    return (9, 30) <= (e.hour, e.minute) < (16, 0)


def _completed(bt, now):
    return bt + timedelta(minutes=5) <= now


def _seen(path):
    # 과거 오류행(bar_open_utc None/비완성/null 종가)이 같은 시각의
    # 뒤늦은 실측 저장을 막지 않도록 완성 실측행만 중복키로 삼는다.
    s = set()
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            k = rec.get("bar_open_utc")
            if not k or rec.get("status") != "completed" or rec.get("c") is None:
                continue
            s.add(k)
    return s


def save_bars(bars, now):
    now_utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for b in bars:
        bt = b.get("t")
        if bt is None:
            continue
        if not _in_regular(bt) or not _completed(bt, now):
            continue
        c, v = b.get("c"), b.get("v")
        if c is None:  # 빈 봉(휴장 혼입 등)은 저장하지 않음
            continue
        day = bt.astimezone(ET).strftime("%Y-%m-%d")
        path = BASE_DIR / f"{day}.jsonl"
        iso = bt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        if iso in _seen(path):
            continue
        try:
            notional = float(c) * int(v) if c is not None and v is not None else None
        except Exception:
            notional = None
        rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR,
               "bar_open_utc": iso, "received_at_utc": now_utc,
               "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
               "c": c, "v": v, "n": None,
               "vwap": None, "notional": notional, "status": "completed"}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n += 1
    return n


def collect_once():
    end = datetime.now(timezone.utc)
    bars = fetch_range_backfill()
    n = save_bars(bars, end)
    print(f"fetched={len(bars)} saved={n}")
    return n


def _sleep_next():
    now = datetime.now(timezone.utc)
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
    time.sleep(max(10, (nxt - now).total_seconds() + 45))


def loop():
    while True:
        e = datetime.now(ET)
        if e.weekday() < 5 and (9, 30) <= (e.hour, e.minute) < (16, 5):
            try:
                end = datetime.now(timezone.utc)
                bars = fetch_recent()
                print(f"[{e:%Y-%m-%d %H:%M %Z}] fetched={len(bars)} saved={save_bars(bars, end)}")
            except Exception as ex:
                print("loop error:", ex)
        else:
            print(f"[{e:%Y-%m-%d %H:%M %Z}] out of hours, wait")
            time.sleep(300)
            continue
        _sleep_next()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    collect_once()
    if a.loop:
        loop()


if __name__ == "__main__":
    main()
