"""BAC 5분봉+거래량 수집기 (금융/섹터:XLF).

원천: Yahoo Finance 공개 차트 (키 불필요)
  GET https://query1.finance.yahoo.com/v8/finance/chart/BAC?interval=5m&period1=..&period2=..
동작: 1회 수집(최근 20거래일, 제공 범위 내) + --loop 시 ET 정규장(09:30-16:00) 5분 완성봉만 수집.
저장: Path(__file__).parent/"data"/"BAC"/YYYY-MM-DD.jsonl (ET 일자별, t 기준 중복금지).
의존: 표준lib + requests.
참고: 원천이 VWAP/체결건수를 제공하지 않아 vwap=null, n=null 보존.
  거래대금 근삿값은 별도 필드 notional(=c*v)로 표시.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:
    sys.exit("requests 필요: pip install requests")

SYMBOL = "BAC"
TIMEFRAME = "5Min"
BASES = [
    f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
    f"https://query2.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent / "data" / SYMBOL
BAR_MIN = 5


def fetch(start_iso, end_iso):
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    params = {
        "interval": "5m",
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "includePrePost": "false",
    }
    err = None
    for base in BASES:
        try:
            r = requests.get(base, headers=UA, params=params, timeout=20)
            if r.status_code != 200:
                err = f"Yahoo {r.status_code}: {r.text[:200]}"
                continue
            j = r.json()
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                err = f"Yahoo empty: {str(j)[:200]}"
                continue
            node = res[0]
            ts = node.get("timestamp") or []
            q = ((node.get("indicators") or {}).get("quote") or [{}])[0]
            bars = []
            for i, t in enumerate(ts):
                try:
                    c = (q.get("close") or [None] * len(ts))[i]
                except IndexError:
                    continue
                if c is None:
                    continue
                dt = datetime.fromtimestamp(t, tz=timezone.utc)
                bars.append({
                    "t": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "o": (q.get("open") or [None] * len(ts))[i],
                    "h": (q.get("high") or [None] * len(ts))[i],
                    "l": (q.get("low") or [None] * len(ts))[i],
                    "c": c,
                    "v": (q.get("volume") or [None] * len(ts))[i],
                })
            return bars
        except Exception as e:
            err = f"Yahoo err: {e}"
            continue
    sys.exit(err or "Yahoo fetch 실패")


def et_now():
    return datetime.now(ET)


def regular_open(now=None):
    n = now or et_now()
    if n.weekday() >= 5:
        return False
    m = n.hour * 60 + n.minute
    return 9 * 60 + 30 <= m < 16 * 60


def in_session_utc(t_utc):
    t = t_utc.astimezone(ET)
    if t.weekday() >= 5:
        return False
    m = t.hour * 60 + t.minute
    return 9 * 60 + 30 <= m < 16 * 60


def completed(bars, now_utc):
    res = []
    for b in bars:
        try:
            t = datetime.strptime(b["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if not in_session_utc(t):
            continue
        if t + timedelta(minutes=BAR_MIN) <= now_utc:
            res.append(b)
    return res


def day_key(bar_t):
    t = datetime.strptime(bar_t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
    if t.hour == 0 and t.minute == 0:
        t -= timedelta(minutes=1)
    return t.strftime("%Y-%m-%d")


def load_keys(path):
    s = set()
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                s.add(json.loads(ln)["t"])
            except Exception:
                pass
    return s


def save(bars):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    groups = {}
    for b in bars:
        c, v = b.get("c"), b.get("v")
        notional = (c * v) if isinstance(c, (int, float)) and isinstance(v, (int, float)) else None
        rec = {"s": SYMBOL, "t": b["t"], "o": b.get("o"), "h": b.get("h"),
               "l": b.get("l"), "c": c, "v": v,
               "vwap": None, "n": None, "notional": notional}
        groups.setdefault(day_key(b["t"]), []).append(rec)
    n_new = 0
    for day, recs in sorted(groups.items()):
        fp = DATA_DIR / f"{day}.jsonl"
        seen = load_keys(fp)
        fresh = [r for r in recs if r["t"] not in seen]
        if fresh:
            with open(fp, "a", encoding="utf-8") as f:
                for r in fresh:
                    f.write(json.dumps(r) + "\n")
            n_new += len(fresh)
        print(f"{day}: +{len(fresh)} ({fp})")
    return n_new


def once():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=32)  # 여유분 포함, 20거래일 커버
    bars = completed(fetch(start.isoformat(), end.isoformat()), end)
    print(f"fetch {len(bars)} bars (20거래일 목표)")
    n = save(bars)
    if bars:
        last = max(b["t"] for b in bars)
        delay = (end - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 60
        print(f"last={last} delay_min={delay:.1f} new={n}")


def loop():
    print("loop: ET 정규장 5분 완성봉만 (Ctrl+C 종료)")
    while True:
        try:
            n_et = et_now()
            if regular_open(n_et):
                end = datetime.now(timezone.utc)
                start = end - timedelta(days=2)
                bars = completed(fetch(start.isoformat(), end.isoformat()), end)
                # 당일 ET 일자분만 저장해 중복·야간 데이터 방지
                today = n_et.strftime("%Y-%m-%d")
                bars = [b for b in bars if day_key(b["t"]) == today]
                save(bars)
                # 다음 5분 경계까지 대기
                m = (BAR_MIN - (n_et.minute % BAR_MIN)) * 60 - n_et.second + 5
                time.sleep(max(m, 30))
            else:
                time.sleep(60)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"loop err: {e}")
            time.sleep(60)


def main():
    ap = argparse.ArgumentParser(description="BAC 5분봉 수집기")
    ap.add_argument("--loop", action="store_true", help="ET 정규장 루프")
    a = ap.parse_args()
    once()
    if a.loop:
        loop()


if __name__ == "__main__":
    main()
