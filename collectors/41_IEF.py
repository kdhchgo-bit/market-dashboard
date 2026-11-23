"""IEF (중기채, iShares 7-10 Year Treasury Bond ETF) 5분봉 수집기.

소스: Yahoo Finance 공개 차트 (키 없음).
  GET https://query1.finance.yahoo.com/v8/finance/chart/IEF?range={5d,1mo}&interval=5m
태그: IEF / 티커: IEF.
방식: 단발 실행은 range=1mo 1회 조회로 최근 20거래일 내외 백필(제공 범위 내),
  --loop는 ET 정규장 중에만 5분마다 range=5d 증분 수집.
범위: ET(America/New_York) 기준 정규장(월~금 09:30~16:00) 완성봉만 저장
  (bar_close_utc <= 수신 시각; 당일 미완성봉 제외).
저장: data/IEF/YYYY-MM-DD.jsonl (ET 일자별, 1줄=5분봉 1개, ponytail: append-only, 기존행 유지/재작성 없음).
필드: 기존 일봉 필드(symbol/date/open/high/low/close/volume/trades/vwap) 유지 +
  source/bar_open_utc/bar_close_utc/received_at_utc/notional_approx(거래대금 근삿값=close*volume)/status 추가.
  Yahoo 미제공인 vwap/trades는 null 보존. 과거 오류행(bar_open_utc null)은 중복판정에서 제외해
  뒤늦은 실측 저장을 막지 않는다.
의존성: 표준라이브러리 + requests.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "IEF"
TICKER = "IEF"
TAG = "IEF"
SRC = "yahoo-chart"
TIMEFRAME = "5m"
YAHOO_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/IEF",
    "https://query2.finance.yahoo.com/v8/finance/chart/IEF",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BAR = timedelta(minutes=5)
ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).resolve().parent / "data" / SYMBOL
FMT = "%Y-%m-%dT%H:%M:%SZ"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IEF 5min bars collector (Yahoo chart, keyless).")
    p.add_argument("--loop", action="store_true", help="repeat collection until interrupted")
    p.add_argument("--days", type=int, default=None, help="collect only last N trading days")
    return p.parse_args()


def et_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


def is_et_open(dt_et: datetime) -> bool:
    if dt_et.weekday() >= 5:
        return False
    return (dt_et.hour, dt_et.minute) >= (9, 30) and (dt_et.hour, dt_et.minute) < (16, 0)


def fetch_yahoo(range_: str) -> tuple[dict | None, str]:
    params = {"range": range_, "interval": "5m", "includePrePost": "false"}
    last_err = "http_error"
    for url in YAHOO_URLS:
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
        except Exception:
            last_err = "http_error"
            continue
        if r.status_code == 429:
            last_err = "rate_limited"
            time.sleep(5)
            continue
        if r.status_code != 200:
            last_err = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = "http_error"
            continue
        res = ((j.get("chart") or {}).get("result") or [None])[0]
        if not res or not res.get("timestamp"):
            last_err = "delayed"
            continue
        return res, "ok"
    return None, last_err


def parse_bars(res: dict, now_utc: datetime) -> list[dict]:
    recv = now_utc.strftime(FMT)
    ts = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    out: list[dict] = []
    for i, t in enumerate(ts):
        try:
            o_utc = datetime.fromtimestamp(t, tz=timezone.utc)
        except Exception:
            continue
        c_utc = o_utc + BAR
        if c_utc > now_utc:  # 완성봉만
            continue
        e = o_utc.astimezone(ET)
        if e.weekday() >= 5:  # 정규장만
            continue
        if (e.hour, e.minute) < (9, 30) or (e.hour, e.minute) >= (16, 0):
            continue
        def _at(a, k):
            try:
                return a[k]
            except Exception:
                return None
        o, h, lo, c, v = _at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i), _at(vols, i)
        if o is None and h is None and lo is None and c is None:
            continue  # null뿐인 행은 실패 취급해 저장 제외
        notional = (c * v) if (c is not None and v is not None) else None
        et_day = e.strftime("%Y-%m-%d")
        out.append({
            "source": SRC,
            "symbol": SYMBOL,
            "date": et_day,
            "bar_open_utc": o_utc.strftime(FMT),
            "bar_close_utc": c_utc.strftime(FMT),
            "received_at_utc": recv,
            "open": o, "high": h, "low": lo, "close": c, "volume": v,
            "trades": None,
            "vwap": None,
            "notional_approx": notional,
            "status": "ok",
        })
    return out


def load_seen() -> tuple[set[str], str | None]:
    """기존 ok행의 bar_open_utc 집합과 최대 시각. 오류행(None/비ok)은 제외."""
    seen: set[str] = set()
    mx: str | None = None
    if not DATA_DIR.exists():
        return seen, mx
    for fp in DATA_DIR.glob("*.jsonl"):
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            t = rec.get("bar_open_utc")
            if not t:
                continue  # 과거 오류행은 무시
            if rec.get("status") not in (None, "ok"):
                continue
            if rec.get("open") is None and rec.get("close") is None:
                continue
            seen.add(t)
            if mx is None or t > mx:
                mx = t
    return seen, mx


# ponytail: append-only, history rewrite 없음 ("a" 모드 + bar_open_utc 중복제외).
def append_bars(bars: list[dict], seen: set[str]) -> int:
    n = 0
    groups: dict[str, list[dict]] = {}
    for b in bars:
        if b["bar_open_utc"] in seen:
            continue
        groups.setdefault(b["date"], []).append(b)
    for day in sorted(groups):
        fp = DATA_DIR / f"{day}.jsonl"
        fp.parent.mkdir(parents=True, exist_ok=True)
        existing: set[str] = set()
        if fp.exists():
            for ln in fp.read_text(encoding="utf-8").splitlines():
                try:
                    t = json.loads(ln).get("bar_open_utc")
                except Exception:
                    continue
                if t:
                    existing.add(t)
        with fp.open("a", encoding="utf-8") as f:
            for b in sorted(groups[day], key=lambda x: x["bar_open_utc"]):
                if b["bar_open_utc"] in existing or b["bar_open_utc"] in seen:
                    continue
                f.write(json.dumps(b, separators=(",", ":")) + "\n")
                existing.add(b["bar_open_utc"])
                seen.add(b["bar_open_utc"])
                n += 1
    return n


def collect_once(range_: str, days: int | None) -> tuple[int, str, int]:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    res, status = fetch_yahoo(range_)
    if res is None:
        print(f"fetch {range_} -> {status}")
        return 0, status, 0
    bars = parse_bars(res, now_utc)
    fetched = len(bars)
    if days is not None and days > 0 and bars:
        dates = sorted(set(b["date"] for b in bars))
        keep = set(dates[-days:])
        bars = [b for b in bars if b["date"] in keep]
    seen, _mx = load_seen()
    n = append_bars(bars, seen)
    print(f"fetch {range_}: {fetched} completed bars -> +{n} new ({status})")
    return n, status, fetched


def main() -> None:
    args = parse_args()
    if args.days is not None and args.days <= 0:
        print("--days must be > 0")
        raise SystemExit(2)
    if not args.loop:
        n, status, fetched = collect_once("1mo", args.days)
        print(f"done: {n} new bars -> {DATA_DIR}/ (fetched={fetched} status={status})")
        return
    while True:
        now_et = et_now()
        if not is_et_open(now_et):
            print(f"closed {now_et:%Y-%m-%d %H:%M} ET skip")
            time.sleep(300)
            continue
        collect_once("5d", args.days)
        time.sleep(300)


if __name__ == "__main__":
    main()
