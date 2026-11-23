"""JPM 5분봉 수집기 (금융/섹터XLF, 거래량+VWAP).

원천: Yahoo Finance 공개 차트 (키 불필요)
  GET https://query1.finance.yahoo.com/v8/finance/chart/JPM?range=1mo&interval=5m
  (실패 시 query2.finance.yahoo.com으로 재시도)
동작: 1회 수집(최근 20거래일, 제공 범위 내) + --loop 시 ET 정규장(평일 09:30-16:00) 5분 완성봉만 수집.
저장: Path(__file__).parent/"data"/"JPM"/YYYY-MM-DD(ET).jsonl, 타임스탬프 중복금지.
사용: python 15_JPM.py [--days 20] [--loop]
의존: 표준lib + requests.
참고: 원천이 VWAP/체결건수를 제공하지 않아 vw=null, n=null으로 보존.
  거래대금 근삿값(notional=c*v)은 별도 필드로 저장.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "JPM"
TIMEFRAME = "5Min"
ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent / "data" / SYMBOL
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


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
    rng = "5d" if (end - start).days <= 7 else "1mo"
    params = {"range": rng, "interval": "5m", "includePrepost": "false", "events": "div|split"}
    last_err = None
    for host in HOSTS:
        try:
            r = requests.get(f"https://{host}/v8/finance/chart/{SYMBOL}",
                             headers=UA, params=params, timeout=30)
        except Exception as e:
            last_err = e
            continue
        if r.status_code != 200:
            last_err = RuntimeError(f"http {r.status_code} from {host}")
            continue
        try:
            j = r.json()
        except Exception as e:
            last_err = e
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                last_err = RuntimeError(f"empty chart result from {host}: {str(j)[:120]}")
                continue
            ts = res[0].get("timestamp") or []
            q = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
            opens = q.get("open") or []
            highs = q.get("high") or []
            lows = q.get("low") or []
            closes = q.get("close") or []
            vols = q.get("volume") or []
            out = []
            for i, tsec in enumerate(ts):
                try:
                    o = opens[i] if i < len(opens) else None
                    h = highs[i] if i < len(highs) else None
                    lo = lows[i] if i < len(lows) else None
                    c = closes[i] if i < len(closes) else None
                    v = vols[i] if i < len(vols) else None
                    if c is None and o is None:
                        continue
                    t_utc = datetime.fromtimestamp(tsec, tz=timezone.utc)
                    if t_utc < start.astimezone(timezone.utc) - timedelta(minutes=1):
                        continue
                    if t_utc > end.astimezone(timezone.utc):
                        continue
                    iso = t_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                    out.append({"t": iso, "o": o, "h": h, "l": lo, "c": c, "v": v})
                except Exception:
                    continue
            return out
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"yahoo chart fetch failed: {last_err}")


def completed(bars, now):
    res = []
    for b in bars:
        try:
            t = datetime.strptime(b["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
        except Exception:
            continue
        if not in_session(t):
            continue
        if t + timedelta(minutes=5) > now:
            continue
        res.append((t, b))
    return res


def existing_ts(path):
    if not path.exists():
        return set()
    s = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            t = json.loads(line).get("t")
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
                c = b.get("c")
                v = b.get("v")
                try:
                    notional = round(float(c) * float(v), 2) if c is not None and v is not None else None
                except Exception:
                    notional = None
                rec = {"symbol": SYMBOL, "timeframe": TIMEFRAME, "t": b["t"],
                       "et": t.isoformat(), "o": b.get("o"), "h": b.get("h"),
                       "l": b.get("l"), "c": c, "v": v,
                       "vw": None, "n": None, "notional": notional}
                if rec["t"] in seen:
                    continue
                f.write(json.dumps(rec) + "\n")
                seen.add(rec["t"])
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
    ap = argparse.ArgumentParser(description="JPM 5분봉 수집기")
    ap.add_argument("--days", type=int, default=20, help="최근 거래일 수 (default: 20)")
    ap.add_argument("--loop", action="store_true", help="ET 정규장 루프")
    a = ap.parse_args()
    if a.loop:
        loop(a.days)
    else:
        once(a.days)


if __name__ == "__main__":
    main()
