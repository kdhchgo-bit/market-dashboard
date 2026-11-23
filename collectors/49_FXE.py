"""FXE (유로 ETF) 5분봉 수집기 — Yahoo Finance 공개 차트 (키 불필요).

원천: https://query1.finance.yahoo.com/v8/finance/chart/FXE?range=5d&interval=5m
  GET https://query1.finance.yahoo.com/v8/finance/chart/FXE
      ?period1=<unix>&period2=<unix>&interval=5m&includePrePost=false
  (실패 시 query2.finance.yahoo.com으로 폴백, User-Agent 필수)

사용법:
  python 49_FXE.py                 # 20거래일 백필 1회 수집 (키 불필요)
  python 49_FXE.py --loop          # ET 정규장(평일 09:30-16:00)에 5분마다 수집
  python 49_FXE.py --loop --days 20 --interval 300

저장: Path(__file__).parent/"data"/"FXE"/YYYY-MM-DD.jsonl (날짜=ET 거래일)
완성봉만 저장(정규장 ET 09:30~16:00 개시봉, bar_close_utc<=수신시각, 비정렬 라이브 스냅샷 제외),
중복(bar_open_utc, status=ok 기준) 스킵, 가짜값 생성 안 함.
Yahoo 미제공 vwap/n은 null 보존, 거래대금 근삿값은 notional_approx(=c*v) 별도 필드.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL, SOURCE, TIMEFRAME, BAR_MIN = "FXE", "yahoo-chart", "5m", 5
BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/FXE",
    "https://query2.finance.yahoo.com/v8/finance/chart/FXE",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")
OUTDIR = Path(__file__).parent / "data" / "FXE"


def utcnow(): return datetime.now(timezone.utc)


def iso_z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def et_start_of_20d(days=20):
    now_et = datetime.now(ET)
    d = now_et.date()
    cnt = 0
    cur = d
    while True:
        if cur.weekday() < 5:
            cnt += 1
            if cnt >= days:
                break
        cur -= timedelta(days=1)
    return datetime(cur.year, cur.month, cur.day, 0, 0, tzinfo=ET).astimezone(timezone.utc)


def in_regular_session(now_et=None):
    now_et = now_et or datetime.now(ET)
    if now_et.weekday() >= 5:
        return False
    t = now_et.time()
    return (t.hour, t.minute) >= (9, 30) and (t.hour, t.minute) < (16, 0)


def fetch_bars(start_utc, end_utc):
    params = {"period1": int(start_utc.timestamp()), "period2": int(end_utc.timestamp()),
              "interval": "5m", "includePrePost": "false"}
    payload = None
    for base in BASES:
        try:
            r = requests.get(base, params=params, headers=UA, timeout=30)
        except Exception as e:
            print(f"yahoo fetch error ({base}): {e}", file=sys.stderr)
            continue
        if r.status_code != 200:
            print(f"yahoo HTTP {r.status_code} ({base}): {r.text[:200]}", file=sys.stderr)
            continue
        try:
            payload = r.json()
        except Exception as e:
            print(f"yahoo json error ({base}): {e}", file=sys.stderr)
            continue
        break
    if payload is None:
        return [], False
    try:
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            err = (payload.get("chart") or {}).get("error")
            print(f"yahoo empty result: {err}", file=sys.stderr)
            return [], False
        res = result[0]
        ts_list = res.get("timestamp") or []
        quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
    except Exception as e:
        print(f"yahoo parse error: {e}", file=sys.stderr)
        return [], False
    out = []
    now = utcnow()
    n = len(ts_list)
    for i in range(n):
        try:
            ts = ts_list[i]
            if ts is None:
                continue
            ts = int(ts)
            if ts % 300 != 0:
                continue  # 라이브 스냅샷 등 비정규 시각 제외
            op = datetime.fromtimestamp(ts, tz=timezone.utc)
            cl = op + timedelta(minutes=BAR_MIN)
            if cl > now:
                continue  # 미완성봉 제외
            op_et = op.astimezone(ET)
            if op_et.weekday() >= 5:
                continue  # 주말 제외
            t = op_et.time()
            if not ((t.hour, t.minute) >= (9, 30) and (t.hour, t.minute) < (16, 0)):
                continue  # 정규장 개시봉만
            o = opens[i] if i < len(opens) else None
            h = highs[i] if i < len(highs) else None
            lo = lows[i] if i < len(lows) else None
            c = closes[i] if i < len(closes) else None
            if o is None and h is None and lo is None and c is None:
                continue  # null뿐인 행 스킵
            if o is None or h is None or lo is None or c is None:
                continue  # 가짜값 대신 스킵
            v_raw = vols[i] if i < len(vols) else None
            v = int(v_raw) if v_raw is not None else 0
            rec = {"source": SOURCE, "symbol": SYMBOL,
                   "bar_open_utc": iso_z(op), "bar_close_utc": iso_z(cl),
                   "received_at_utc": iso_z(now),
                   "o": float(o), "h": float(h), "l": float(lo),
                   "c": float(c), "v": v, "n": None,
                   "vwap": None,
                   "notional_approx": float(float(c) * v),
                   "status": "ok"}
        except Exception:
            continue  # 가짜값 대신 스킵
        out.append(rec)
    out.sort(key=lambda x: x["bar_open_utc"])
    return out, True


def load_opens(path):
    s = set()
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                    if o.get("status", "ok") != "ok":
                        continue  # 과거 오류 행이 뒤늦은 실측 저장을 막지 않게
                    v = o.get("bar_open_utc")
                    if v:
                        s.add(v)
                except Exception:
                    pass
    return s


def append_bars(recs):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for r in recs:
        op = datetime.fromisoformat(r["bar_open_utc"].replace("Z", "+00:00"))
        by_day.setdefault(op.astimezone(ET).date().isoformat(), []).append(r)
    total = 0
    for day, rows in sorted(by_day.items()):
        p = OUTDIR / f"{day}.jsonl"
        seen = load_opens(p)
        rows = [x for x in rows if x["bar_open_utc"] not in seen]
        if not rows:
            continue
        with open(p, "a", encoding="utf-8") as f:  # ponytail: append only, never rewrite history
            for x in rows:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
        total += len(rows)
        print(f"wrote {len(rows)} -> {p}")
    return total


def secs_to_next_bucket():
    now_et = datetime.now(ET)
    minute = (now_et.minute // BAR_MIN + 1) * BAR_MIN
    nxt = now_et.replace(second=0, microsecond=0) + timedelta(minutes=minute - now_et.minute)
    return max(10.0, (nxt - now_et).total_seconds() + 45)


def once(days):
    end = utcnow()
    start = et_start_of_20d(days)
    recs, ok = fetch_bars(start, end)
    n = append_bars(recs) if recs else 0
    print(f"done ok={ok} fetched={len(recs)} new={n}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--interval", type=int, default=300)
    a = ap.parse_args()
    if not a.loop:
        once(a.days)
        return
    print("loop mode: ET regular session only, 5min", flush=True)
    while True:
        try:
            if not in_regular_session():
                time.sleep(60)
                continue
            end = utcnow()
            start = end - timedelta(days=2)
            recs, _ = fetch_bars(start, end)
            if recs:
                append_bars(recs)
            time.sleep(min(max(a.interval, 60), secs_to_next_bucket()))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
            time.sleep(60)


if __name__ == "__main__":
    main()
