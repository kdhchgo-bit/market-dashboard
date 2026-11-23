"""MA 5분봉 수집기 (금융, 거래량+VWAP).

원천: Yahoo Finance 공개 차트 (키 불필요)
  GET https://query1.finance.yahoo.com/v8/finance/chart/MA?period1=&period2=&interval=5m&includePrePost=false
  (query1 실패 시 query2로 대체)
동작: 1회 수집(최근 20거래일, 제공 범위 내) + --loop 시 ET 정규장(평일 09:30-16:00) 5분 완성봉만 수집.
  Yahoo가 VWAP를 제공하지 않으므로 vw=null 보존, 거래대금 근삿값(c*v)은 별도 notional 필드로 표시.
저장: Path(__file__).parent/"data"/"MA"/YYYY-MM-DD(ET).jsonl, 타임스탬프(t) 중복금지.
  과거 오류 행(null OHLC / status error)은 같은 시각의 뒤늦은 실측 저장을 막지 않는다.
사용: python 60_MA.py [--days 20] [--loop]
의존: 표준lib + requests.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "MA"
TIMEFRAME = "5Min"
INTERVAL = "5m"
ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent / "data" / SYMBOL
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")


def et_now():
    return datetime.now(ET)


def in_session(dt):
    return dt.weekday() < 5 and (9, 30) <= (dt.hour, dt.minute) <= (16, 0)


def day_start_20d(now, days=20):
    d = now.date()
    n = 0
    cur = d
    while True:
        if cur.weekday() < 5:
            n += 1
            if n >= days:
                break
        cur -= timedelta(days=1)
    return datetime(cur.year, cur.month, cur.day, tzinfo=ET)


def fetch(start, end):
    """Yahoo chart 5m bars between start/end. Returns list of (et_open, o,h,l,c,v)."""
    p1 = int(start.timestamp())
    p2 = int(end.timestamp())
    last_err = None
    for host in HOSTS:
        try:
            r = requests.get(
                f"{host}/v8/finance/chart/{SYMBOL}",
                headers=UA,
                params={"period1": p1, "period2": p2, "interval": INTERVAL,
                        "includePrePost": "false"},
                timeout=30,
            )
            r.raise_for_status()
            j = r.json()
            res = (j.get("chart", {}).get("result") or [None])[0]
            if res is None:
                last_err = j.get("chart", {}).get("error")
                continue
            ts = res.get("timestamp") or []
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            vols = q.get("volume") or []
            opens = q.get("open") or []
            highs = q.get("high") or []
            lows = q.get("low") or []
            closes = q.get("close") or []
            out = []
            for i, t in enumerate(ts):
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                lo = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = vols[i] if i < len(vols) else None
                if o is None and h is None and lo is None and c is None:
                    continue
                t_et = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(ET)
                out.append({"t_et": t_et, "o": o, "h": h, "l": lo, "c": c,
                            "v": int(v) if v is not None else 0})
            return out
        except Exception as e:
            last_err = e
            time.sleep(1)
            continue
    raise RuntimeError(f"yahoo chart fetch failed: {last_err}")


def completed(bars, now):
    res = []
    for b in bars:
        t = b["t_et"]
        if not in_session(t):
            continue
        if t + timedelta(minutes=5) > now:
            continue
        res.append((t, b))
    return res


def existing_ts(path):
    """유효(종가 비null) 레코드의 t 집합만 반환 → 과거 오류 행이 실측 저장을 막지 않음."""
    if not path.exists():
        return set()
    s = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("c") is None and r.get("o") is None:
                continue
            if r.get("status") not in (None, "ok"):
                continue
            t = r.get("t")
            if t:
                s.add(t)
        except Exception:
            pass
    return s


def save(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for t, b in rows:
        by_day.setdefault(t.date().isoformat(), []).append((t, b))
    n = 0
    for day, items in sorted(by_day.items()):
        fp = DATA_DIR / f"{day}.jsonl"
        seen = existing_ts(fp)
        with fp.open("a", encoding="utf-8") as f:
            for t, b in sorted(items):
                t_utc = t.astimezone(timezone.utc)
                t_str = t_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                if t_str in seen:
                    continue
                c = b["c"]
                v = b["v"]
                rec = {"symbol": SYMBOL, "timeframe": TIMEFRAME, "t": t_str,
                       "et": t.isoformat(), "o": b["o"], "h": b["h"],
                       "l": b["l"], "c": c, "v": v,
                       "vw": None, "n": None,
                       "notional": round(c * v, 2) if c is not None and v is not None else None,
                       "source": "yahoo-chart"}
                f.write(json.dumps(rec) + "\n")
                seen.add(t_str)
                n += 1
    return n


def once(days):
    now = et_now()
    start = day_start_20d(now, days)
    rows = completed(fetch(start, now), now)
    print(f"fetched={len(rows)} saved={save(rows)} days={days}")


def loop(days):
    while True:
        now = et_now()
        if in_session(now) or True:
            try:
                s = day_start_20d(now, 2)
                print(f"saved={save(completed(fetch(s, now), now))} at {now:%H:%M %Z}")
            except Exception as e:
                print(f"loop err: {e}")
        nxt = now.replace(second=5, microsecond=0) + timedelta(minutes=5 - now.minute % 5)
        time.sleep(max(10, (nxt - et_now()).total_seconds()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    (loop if a.loop else once)(a.days)


if __name__ == "__main__":
    main()
