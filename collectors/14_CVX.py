"""CVX (에너지, 섹터 XLE) 5분봉+거래량 수집기.

원천: Yahoo Finance 공개 차트 (키 불필요)
  GET https://query1.finance.yahoo.com/v8/finance/chart/CVX?range=1mo&interval=5m&includePrePost=false
  (query1 실패 시 query2로 재시도)
동작: 1회 수집(최근 20거래일, 제공 범위 내) + --loop 시 ET 정규장(09:30-16:00) 5분 완성봉만 수집.
  원천 미제공 VWAP는 vwap=null 보존, 거래대금 근삿값은 notional 별도 필드.
저장: Path(__file__).parent/"data"/"CVX"/YYYY-MM-DD.jsonl (날짜=ET 기준 바 날짜, t 기준 중복금지).
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "CVX"
URLS = [
    f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
    f"https://query2.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")
OUT = Path(__file__).parent / "data" / SYMBOL


def fetch(range_="1mo"):
    last = None
    for url in URLS:
        try:
            r = requests.get(url, params={"range": range_, "interval": "5m",
                                          "includePrePost": "false"},
                             headers=UA, timeout=30)
            r.raise_for_status()
            res = (r.json().get("chart") or {}).get("result") or []
            if not res:
                last = "empty_chart"
                continue
            ts = res[0].get("timestamp") or []
            q = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
            bars = []
            for i, t in enumerate(ts):
                try:
                    o = (q.get("open") or [])[i]
                    h = (q.get("high") or [])[i]
                    l = (q.get("low") or [])[i]
                    c = (q.get("close") or [])[i]
                    v = (q.get("volume") or [])[i]
                except IndexError:
                    continue
                if o is None or h is None or l is None or c is None:
                    continue  # 빈 봉 제외
                dt = datetime.fromtimestamp(t, tz=timezone.utc)
                bars.append({"t": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "o": o, "h": h, "l": l, "c": c,
                             "v": int(v) if v is not None else 0})
            if bars:
                return bars
            last = "empty_bars"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"yahoo chart fetch failed ({last})")


def completed_only(bars, now_et=None):
    now_et = now_et or datetime.now(ET)
    out = []
    for b in bars:
        t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
        if not (t.hour > 9 or (t.hour == 9 and t.minute >= 30)) or (t.hour >= 16):
            continue  # ET 정규장(09:30~16:00 시작봉)만
        floor = t.replace(second=0, microsecond=0)
        m = (floor.minute // 5) * 5
        close = floor.replace(minute=m) + timedelta(minutes=5)
        if now_et < close:
            continue  # 미완성봉 제외
        b["_et"] = t
        out.append(b)
    return out


def with_vwap(bars):
    # Yahoo 원천은 VWAP 미제공 → vwap=null 보존, 거래대금 근삿값은 notional 별도 필드
    for b in bars:
        b["vwap"] = None
        tp = (b["h"] + b["l"] + b["c"]) / 3
        b["notional"] = round(tp * b["v"], 2)
    return bars


def load_keys(path):
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            t = json.loads(line).get("t")
        except Exception:
            continue  # 과거 오류 행은 무시하고 뒤늦은 실측 저장을 막지 않음
        if t:
            keys.add(t)
    return keys


def save(bars):
    bars = with_vwap(bars)
    groups = {}
    for b in bars:
        groups.setdefault(b["_et"].date().isoformat(), []).append(b)
    for d, rows in groups.items():
        OUT.mkdir(parents=True, exist_ok=True)
        fp = OUT / f"{d}.jsonl"
        seen = load_keys(fp)
        added = 0
        with open(fp, "a", encoding="utf-8") as f:
            for b in sorted(rows, key=lambda x: x["t"]):
                if b["t"] in seen:
                    continue
                rec = {"symbol": SYMBOL, "t": b["t"], "et": b["_et"].isoformat(),
                       "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
                       "v": b["v"], "vwap": b["vwap"], "n": None, "vw": None,
                       "notional": b["notional"]}
                f.write(json.dumps(rec) + "\n")
                seen.add(b["t"])
                added += 1
        print(f"{d}: +{added} bars -> {fp}")


def one_shot(days=20):
    bars = completed_only(fetch(range_="1mo"))
    # 최근 N거래일만 (제공 범위 내)
    day_list = sorted({b["_et"].date().isoformat() for b in bars})[-days:]
    save([b for b in bars if b["_et"].date().isoformat() in day_list])


def loop(interval=300):
    try:
        while True:
            now_et = datetime.now(ET)
            wd = now_et.weekday() < 5 and (now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 35))
            wd = wd and now_et.hour < 16
            if wd:
                try:
                    bars = completed_only(fetch(range_="5d"))
                    save(bars)
                except Exception as e:
                    print("loop error:", e, flush=True)
            else:
                print("ET 정규장 외 대기:", now_et.isoformat(timespec="minutes"), flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description="CVX 5분봉 수집기 (Yahoo 공개 차트, 키 불필요) -> data/CVX/YYYY-MM-DD.jsonl")
    ap.add_argument("--loop", action="store_true", help="ET 정규장(09:30-16:00) 5분 완성봉 반복 수집")
    ap.add_argument("--days", type=int, default=20, help="1회 수집 거래일 수 (default 20)")
    ap.add_argument("--interval", type=int, default=300, help="loop 간격 초 (default 300)")
    a = ap.parse_args()
    if a.loop:
        loop(a.interval)
    else:
        one_shot(a.days)


if __name__ == "__main__":
    main()
