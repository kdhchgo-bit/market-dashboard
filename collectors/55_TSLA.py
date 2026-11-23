"""TSLA 5분봉 수집기 (임의소비재, 섹터 XLY).
원천: Yahoo Finance 공개 차트 — https://query1.finance.yahoo.com/v8/finance/chart/TSLA?range=5d&interval=5m
정규장 완성 5분봉 OHLCV만 저장. 키 불필요(무료 공개 경로).
Yahoo가 제공하지 않는 VWAP는 null로 보존하고 거래대금 근삿값(notional_approx=종가×거래량)은 별도 필드로 표시.
용법: python 55_TSLA.py (최근 20거래일 1회) / python 55_TSLA.py --loop (ET 정규장 5분 폴링, 완성봉만).
저장: <this_dir>/data/TSLA/YYYY-MM-DD.jsonl (bar_open_utc 날짜별, 중복금지).
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "TSLA"
SECTOR = "XLY"
SOURCE = "yahoo-chart"
BASE = "https://query1.finance.yahoo.com/v8/finance/chart/TSLA"
BASE_FB = "https://query2.finance.yahoo.com/v8/finance/chart/TSLA"
UA = {"User-Agent": "Mozilla/5.0"}
OUT = Path(__file__).parent / "data" / "TSLA"


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


def fetch_yahoo(range_="1mo"):
    last_err = "unknown"
    for base in (BASE, BASE_FB):
        try:
            r = requests.get(base, params={"range": range_, "interval": "5m",
                                           "includePrePost": "false"},
                             headers=UA, timeout=30)
        except Exception as e:
            last_err = f"http_error: {e}"
            continue
        if r.status_code != 200:
            last_err = f"http_{r.status_code}"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = "http_error: bad json"
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                last_err = "empty_chart"
                continue
            ts = res[0].get("timestamp") or []
            q = (res[0].get("indicators") or {}).get("quote") or [{}]
            q = q[0]
            opens, highs = q.get("open") or [], q.get("high") or []
            lows, closes = q.get("low") or [], q.get("close") or []
            vols = q.get("volume") or []
            bars = []
            for i, t in enumerate(ts):
                try:
                    c = closes[i] if i < len(closes) else None
                    if c is None:
                        continue  # 빈 봉(휴장 구간 등)은 제외
                    bt = datetime.fromtimestamp(t, tz=timezone.utc)
                    bars.append({
                        "t": bt.isoformat().replace("+00:00", "Z"),
                        "o": opens[i] if i < len(opens) else None,
                        "h": highs[i] if i < len(highs) else None,
                        "l": lows[i] if i < len(lows) else None,
                        "c": c,
                        "v": vols[i] if i < len(vols) else None,
                    })
                except Exception:
                    continue
            if not bars:
                last_err = "empty_chart"
                continue
            return bars, "ok"
        except Exception as e:
            last_err = f"parse_error: {e}"
            continue
    return [], last_err


def seen_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                k = json.loads(line).get("bar_open_utc")
                if k:  # 오류 행(None) 무시 → 뒤늦은 실측 저장 차단 방지
                    s.add(k)
            except Exception:
                pass
    return s


def save(bars, status="ok"):
    now = utcnow().isoformat().replace("+00:00", "Z")
    now_dt = utcnow()
    n_new = 0
    for b in bars:
        t = b.get("t", "")
        try:
            bt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            continue
        if bt + timedelta(minutes=5) > now_dt:
            continue  # 완성봉만
        try:
            from zoneinfo import ZoneInfo
            bet = bt.astimezone(ZoneInfo("America/New_York"))
        except Exception:
            bet = bt - timedelta(hours=5 if time.localtime().tm_isdst == 0 else 4)
        if not in_regular(bet):
            continue  # 정규장만(원천이 정규장만 주지만 방어 필터)
        day = t[:10]
        path = OUT / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if t in seen_keys(path):
            continue
        c, v = b.get("c"), b.get("v")
        na = (c * v) if isinstance(c, (int, float)) and isinstance(v, (int, float)) else None
        rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR, "bar_open_utc": t,
               "received_at_utc": now, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
               "c": c, "v": v, "n": None, "vwap": None, "notional_approx": na,
               "status": status}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n_new += 1
    return n_new


def once(days=20):
    range_ = "1mo" if days > 5 else "5d"  # 20거래일 백필은 제공 범위 내(1mo≈22거래일)
    bars, st = fetch_yahoo(range_)
    if st != "ok":
        print(f"fetched=0 new=0 status={st}")
        return
    n = save(bars, "ok")
    comp = [b["t"] for b in bars
            if datetime.fromisoformat(b["t"].replace("Z", "+00:00")) + timedelta(minutes=5) <= utcnow()]
    last = comp[-1] if comp else None
    delay = ""
    if last:
        try:
            lc = datetime.fromisoformat(last.replace("Z", "+00:00")) + timedelta(minutes=5)
            delay = f" delay_min={(utcnow() - lc).total_seconds() / 60:.1f}"
        except Exception:
            pass
    print(f"fetched={len(bars)} new={n} last={last}{delay}")


def loop():
    while True:
        try:
            et = et_now()
            if in_regular(et):
                bars, st = fetch_yahoo("1d")
                n = save(bars, "ok" if st == "ok" else st)
                print(f"{et.isoformat()} fetched={len(bars)} new={n} status={st}", flush=True)
            else:
                print(f"{et.isoformat()} out-of-session sleep", flush=True)
        except Exception as e:
            print(f"loop error: {e}", flush=True)
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
