"""UNG (United States Natural Gas Fund) 5분봉 수집기 — 천연가스 방향 판단용.

원천: Yahoo Finance 공개 차트 (키 불필요, 비공식 경로).
  문서: https://query1.finance.yahoo.com/v8/finance/chart/UNG?range=5d&interval=5m
  엔드포인트: GET https://query1.finance.yahoo.com/v8/finance/chart/UNG
    ?range=1mo&interval=5m&includePrePost=false&events=div|split
    (query1 실패 시 https://query2.finance.yahoo.com/v8/finance/chart/UNG 대체)
    헤더: User-Agent: Mozilla/5.0 (Yahoo가 기본 UA 차단)
  범위: range=1mo는 5분봉 제공 한도(최근 60일 이내) 안에서 최근 20거래일 백필 커버.
  정규장 완성 5분봉 OHLCV만 저장. Yahoo 미제공 VWAP/n은 null 보존,
  거래대금 근삿값(c*v)은 별도 notional_approx 필드로 표시.

사용법:
  python 47_UNG.py            # 1회 수집 (최초 20거래일 백필, 이후 증분)
  python 47_UNG.py --loop     # 5분 간격 반복 수집

환경변수: 없음 (키 불필요).

저장:
  Path(__file__).parent/"data"/"UNG"/YYYY-MM-DD.jsonl (날짜=미동부 ET 기준 bar 날짜)
  예: {"source":"yahoo-chart","symbol":"UNG","bar_open_utc":"2026-11-11T14:30:00Z",
       "bar_close_utc":"2026-11-11T14:35:00Z","received_at_utc":"2026-11-11T14:36:01Z",
       "o":15.1,"h":15.3,"l":15.0,"c":15.2,"v":12000,"n":null,"vwap":null,
       "notional_approx":182400.0,"status":"ok"}

loop 규칙:
  --loop면 5분 간격. ET 월-금 09:30-16:00에만 수집, 나머지 시간은 저장 없이 skip 후 대기.
  완성봉만 저장 (bar_close_utc <= 현재시각). bar_open_utc 중복 저장 금지.
  에러시 임의가격 생성 금지, status(rate_limited|http_error|delayed|closed) 기록.
  과거 오류행(bar_open_utc=null)은 실측 dedup에 포함하지 않는다.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "UNG"
URLS = [
    f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
    f"https://query2.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
]
SOURCE = "yahoo-chart"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")


def et_now():
    return datetime.now(ET)


def fetch_bars():
    """Yahoo 5분봉 1회 조회. 반환: (raw봉 리스트, 상태).

    raw봉: {"t": epoch초, "o":..,"h":..,"l":..,"c":..,"v":..}
    상태: ok | rate_limited | http_error
    """
    params = {"range": "1mo", "interval": "5m",
              "includePrePost": "false", "events": "div|split"}
    last_status = "http_error"
    for url in URLS:
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
        except requests.RequestException:
            last_status = "http_error"
            continue
        if r.status_code == 429:
            return [], "rate_limited"
        if r.status_code != 200:
            last_status = "http_error"
            continue
        try:
            body = r.json()
        except ValueError:
            last_status = "http_error"
            continue
        chart = (body or {}).get("chart") or {}
        if chart.get("error"):
            last_status = "http_error"
            continue
        result = (chart.get("result") or [None])[0]
        if not result:
            last_status = "http_error"
            continue
        meta = result.get("meta") or {}
        # 요청 상품과 다른 값이면 실패 취급
        if (meta.get("symbol") or "").upper() != SYMBOL:
            last_status = "http_error"
            continue
        ts = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        out = []
        for i, t in enumerate(ts):
            try:
                tsec = int(t)
            except (TypeError, ValueError):
                continue
            def _at(a):
                return a[i] if i < len(a) else None
            out.append({"t": tsec, "o": _at(opens), "h": _at(highs),
                        "l": _at(lows), "c": _at(closes), "v": _at(vols)})
        if not out:
            last_status = "http_error"
            continue
        return out, "ok"
    return [], last_status


def existing_opens(base):
    """실측 ok행의 bar_open_utc 집합과 최신 시각. 오류행(null/비ok)은 제외."""
    opens = set()
    latest = None
    for fp in base.glob("*.jsonl"):
        try:
            for line in fp.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("status") != "ok":
                    continue
                t = d.get("bar_open_utc")
                if not t:
                    continue
                if d.get("o") is None and d.get("c") is None:
                    continue
                opens.add(t)
                if latest is None or t > latest:
                    latest = t
        except OSError:
            continue
    return opens, latest


def collect_once(base):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    opens, _latest = existing_opens(base)
    bars, status = fetch_bars()
    day = et_now().strftime("%Y-%m-%d")
    if status != "ok":
        fp = base / f"{day}.jsonl"
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": None,
               "bar_close_utc": None, "received_at_utc": now_iso,
               "o": None, "h": None, "l": None, "c": None, "v": None,
               "n": None, "vwap": None, "notional_approx": None, "status": status}
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"{status} recorded")
        return 0
    grouped = {}
    n = 0
    for b in bars:
        # Yahoo 실시간 형성봉은 5분 경계 미정렬 시각일 수 있어 경계로 내림
        odt = datetime.fromtimestamp(b["t"] // 300 * 300, tz=timezone.utc)
        cdt = odt + timedelta(minutes=5)
        if cdt > now:  # 미완성봉 제외
            continue
        et_t = odt.astimezone(ET)
        if et_t.weekday() >= 5:  # 정규장만 (주말 제외)
            continue
        if not ((9, 30) <= (et_t.hour, et_t.minute) < (16, 0)):
            continue
        o, h, l, c, v = b["o"], b["h"], b["l"], b["c"], b["v"]
        if o is None and h is None and l is None and c is None:
            continue
        t = odt.strftime("%Y-%m-%dT%H:%M:%SZ")
        if t in opens:
            continue
        notional = None
        try:
            if c is not None and v is not None:
                notional = round(float(c) * float(v), 2)
        except (TypeError, ValueError):
            notional = None
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": t,
               "bar_close_utc": cdt.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "received_at_utc": now_iso, "o": o, "h": h,
               "l": l, "c": c, "v": v,
               "n": None, "vwap": None, "notional_approx": notional,
               "status": "ok"}
        day_k = et_t.strftime("%Y-%m-%d")
        grouped.setdefault(day_k, []).append(rec)
        opens.add(t)
        n += 1
    for day_k, recs in grouped.items():
        with open(base / f"{day_k}.jsonl", "a", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec) + "\n")
    if n == 0:
        cur = et_now()
        is_open = cur.weekday() < 5 and (9, 30) <= (cur.hour, cur.minute) < (16, 0)
        if not is_open:
            fp = base / f"{day}.jsonl"
            rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": None,
                   "bar_close_utc": None, "received_at_utc": now_iso,
                   "o": None, "h": None, "l": None, "c": None, "v": None,
                   "n": None, "vwap": None, "notional_approx": None, "status": "closed"}
            with open(fp, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            print("closed recorded")
            return 0
        print("no new bars (already up to date)")
        return 0
    print(f"saved {n} bars")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    base = Path(__file__).parent / "data" / SYMBOL
    base.mkdir(parents=True, exist_ok=True)
    if not a.loop:
        collect_once(base)
        return
    while True:
        cur = et_now()
        if cur.weekday() >= 5 or not ((9, 30) <= (cur.hour, cur.minute) < (16, 0)):
            print(f"skip closed {cur.strftime('%Y-%m-%d %H:%M %Z')}")
            time.sleep(300)
            continue
        collect_once(base)
        time.sleep(300)


if __name__ == "__main__":
    main()
