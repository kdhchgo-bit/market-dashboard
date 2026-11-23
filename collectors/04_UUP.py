"""UUP (달러지수 불리시 ETF) 5분봉 수집기 — Yahoo Finance 공개 차트 (키 없음).

원천: https://query1.finance.yahoo.com/v8/finance/chart/UUP?range=1mo&interval=5m
  (폴백 query2, period1/period2+interval=5m; 20거래일 백필은 제공 범위 내에서 수행)

사용법:
  python 04_UUP.py                 # 20거래일 백필 1회 수집 (키 불필요)
  python 04_UUP.py --loop          # ET 정규장(평일 09:30-16:00)에 5분마다 수집
  python 04_UUP.py --loop --days 20 --interval 300

저장: Path(__file__).parent/"data"/"UUP"/YYYY-MM-DD.jsonl (날짜=ET 거래일)
완성봉만 저장(정규장, bar_close<=now, 5분 정렬), 중복(bar_open_utc) 스킵,
VWAP 미제공 → null 보존, 거래대금 근삿값은 turnover_est(c*v) 별도 필드, 가짜값 생성 안 함.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL, SOURCE, TIMEFRAME, BAR_MIN = "UUP", "yahoo-chart", "5Min", 5
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
CHART_PATH = "/v8/finance/chart/UUP"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (UUP-5m collector; keyless)"}
ET = ZoneInfo("America/New_York")
OUTDIR = Path(__file__).parent / "data" / "UUP"

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


def _parse_yahoo(j, now):
    try:
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return []
        r0 = res[0]
        ts = r0.get("timestamp") or []
        q = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        closes = q.get("close") or []
        vols = q.get("volume") or []
    except (AttributeError, TypeError):
        return []
    out = []
    recv = iso_z(now)
    n = min(len(ts), len(opens), len(highs), len(lows), len(closes), len(vols))
    for i in range(n):
        try:
            op = datetime.fromtimestamp(int(ts[i]), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            continue
        # 5분 정렬 봉만 (형성 중 비정렬 틱 제외)
        if op.second != 0 or op.minute % BAR_MIN != 0:
            continue
        cl = op + timedelta(minutes=BAR_MIN)
        if cl > now:
            continue  # 미완성봉 제외
        et = op.astimezone(ET)
        if et.weekday() >= 5:
            continue
        if (et.hour, et.minute) < (9, 30) or (et.hour, et.minute) >= (16, 0):
            continue  # 정규장만
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if o is None or h is None or l is None or c is None:
            continue  # null뿐인 행 스킵
        v = vols[i]
        try:
            v = int(v) if v is not None else 0
        except (ValueError, TypeError):
            v = 0
        try:
            rec = {"source": SOURCE, "symbol": SYMBOL,
                   "bar_open_utc": iso_z(op), "bar_close_utc": iso_z(cl),
                   "received_at_utc": recv,
                   "o": float(o), "h": float(h), "l": float(l),
                   "c": float(c), "v": v, "n": None,
                   "vwap": None,
                   "turnover_est": round(float(c) * v, 2) if v else 0.0,
                   "status": "ok"}
        except (ValueError, TypeError):
            continue  # 가짜값 대신 스킵
        out.append(rec)
    return out


def _get_json(host, params):
    r = requests.get(host + CHART_PATH, headers=UA, params=params, timeout=30)
    if r.status_code != 200:
        print(f"yahoo HTTP {r.status_code} via {host}: {r.text[:200]}", file=sys.stderr)
        return None
    try:
        return r.json()
    except ValueError:
        print(f"yahoo non-JSON via {host}: {r.text[:200]}", file=sys.stderr)
        return None


def fetch_bars(start_utc, end_utc):
    now = utcnow()
    p1 = int(start_utc.timestamp())
    p2 = int(end_utc.timestamp())
    attempts = [
        {"period1": p1, "period2": p2, "interval": "5m", "includePrePost": "false"},
        {"range": "1mo", "interval": "5m"},
        {"range": "5d", "interval": "5m"},
    ]
    last_error = True
    for params in attempts:
        for host in HOSTS:
            try:
                j = _get_json(host, params)
            except requests.RequestException as e:
                print(f"fetch error via {host}: {e}", file=sys.stderr)
                time.sleep(1)
                continue
            if j is None:
                time.sleep(1)
                continue
            recs = _parse_yahoo(j, now)
            # HTTP 200 + 파싱 성공이면 성공 취급 (0건은 휴장/미제공 가능)
            if recs:
                return recs, True
            # 빈 결과면 다음 후보(더 넓은/좁은 범위)로 계속
            last_error = False
            time.sleep(1)
    # 모든 시도에서 완성봉 0건
    return [], last_error


def load_opens(path):
    s = set()
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    v = json.loads(line).get("bar_open_utc")
                except Exception:
                    continue
                if v:  # 과거 오류행(None) 무시 → 뒤늦은 실측 저장 차단 방지
                    s.add(v)
    return s


def append_bars(recs):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for r in recs:
        if not r.get("bar_open_utc"):
            continue
        op = datetime.fromisoformat(r["bar_open_utc"].replace("Z", "+00:00"))
        by_day.setdefault(op.astimezone(ET).date().isoformat(), []).append(r)
    total = 0
    for day, rows in sorted(by_day.items()):
        p = OUTDIR / f"{day}.jsonl"
        seen = load_opens(p)
        rows = [x for x in rows if x["bar_open_utc"] not in seen]
        if not rows:
            continue
        with open(p, "a", encoding="utf-8") as f:
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
