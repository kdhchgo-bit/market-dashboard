"""AMZN 5분봉 수집기 (임의소비재 대표, 섹터 XLY).
원천: Yahoo Finance 공개 차트 (키 없음) — https://query1.finance.yahoo.com/v8/finance/chart/AMZN?range=5d&interval=5m
정규장 완성 5분봉 OHLCV만 저장. 최근 20거래일 백필은 제공 범위 내에서 수행(1mo→5d 폴백).
VWAP는 원천 미제공이므로 null 보존, 거래대금 근삿값(notional=c*v)은 별도 필드.
용법: python 52_AMZN.py (최근 20거래일 1회) / python 52_AMZN.py --loop (ET 정규장 5분 폴링, 완성봉만).
저장: <this_dir>/data/AMZN/YYYY-MM-DD.jsonl (bar_open_utc 날짜별, 중복금지).
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "AMZN"
SECTOR = "XLY"
SOURCE = "yahoo-chart"
INTERVAL = "5m"
OUT = Path(__file__).parent / "data" / "AMZN"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
HOSTS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/AMZN",
    "https://query2.finance.yahoo.com/v8/finance/chart/AMZN",
]


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


def fetch_chart(rng):
    last_err = None
    for base in HOSTS:
        try:
            r = requests.get(base, params={"range": rng, "interval": INTERVAL,
                                           "includePrePost": "false"},
                             headers=UA, timeout=30)
            r.raise_for_status()
            j = r.json()
            res = (j.get("chart") or {}).get("result")
            if res:
                return res[0], None
            last_err = f"empty_result:{base}"
        except Exception as e:
            last_err = f"{type(e).__name__}:{e}"
    return None, last_err


def parse_bars(result):
    """Yahoo chart result → 완성된 정규장 5분봉 리스트. {t,o,h,l,c,v}."""
    if not result:
        return []
    ts = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    vols = quotes.get("volume") or []
    now_ts = utcnow().timestamp()
    out = []
    n = min(len(ts), len(opens), len(highs), len(lows), len(closes), len(vols))
    for i in range(n):
        try:
            t = int(ts[i])
        except Exception:
            continue
        if t + 300 > int(now_ts):
            continue  # 완성봉만
        o, h, lo, c = opens[i], highs[i], lows[i], closes[i]
        if o is None and h is None and lo is None and c is None:
            continue  # null뿐인 행 스킵
        # 정규장 필터 (ET 기준)
        try:
            from zoneinfo import ZoneInfo
            et = datetime.fromtimestamp(t, timezone.utc).astimezone(ZoneInfo("America/New_York"))
        except Exception:
            et = datetime.fromtimestamp(t, timezone.utc)
        if et.weekday() >= 5:
            continue
        if (et.hour, et.minute) < (9, 30) or (et.hour, et.minute) >= (16, 0):
            continue
        out.append({"t": t, "o": o, "h": h, "l": lo, "c": c, "v": vols[i]})
    return out


def seen_keys(path):
    """유효 실측 행의 bar_open_utc만 수집. 오류(null/None) 행은 차단하지 않음."""
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            t = r.get("bar_open_utc")
            if not t:
                continue
            # 가격 실측이 없는 오류 행은 중복키에서 제외
            if r.get("o") is None and r.get("h") is None and r.get("l") is None and r.get("c") is None:
                continue
            s.add(t)
    return s


def save(bars, status="ok"):
    now = utcnow().isoformat().replace("+00:00", "Z")
    groups = {}
    for b in bars:
        t = datetime.fromtimestamp(b["t"], timezone.utc).isoformat().replace("+00:00", "Z")
        groups.setdefault(t[:10], []).append((t, b))
    n_new = 0
    for day in sorted(groups):
        path = OUT / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        seen = seen_keys(path)
        rows = sorted(groups[day], key=lambda x: x[0])
        with open(path, "a", encoding="utf-8") as f:
            for t, b in rows:
                if t in seen:
                    continue
                c, v = b.get("c"), b.get("v")
                try:
                    notional = round(float(c) * float(v), 2) if c is not None and v is not None else None
                except Exception:
                    notional = None
                rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR, "bar_open_utc": t,
                       "received_at_utc": now, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
                       "c": c, "v": v, "n": None, "vwap": None, "notional": notional, "status": status}
                f.write(json.dumps(rec) + "\n")
                seen.add(t)
                n_new += 1
    return n_new


def once(days=20):
    rng = "1mo" if int(days) >= 15 else "5d"
    res, err = fetch_chart(rng)
    if res is None:
        print(f"fetched=0 new=0 status=fetch_error err={err}")
        return
    bars = parse_bars(res)
    n = save(bars)
    last = datetime.fromtimestamp(bars[-1]["t"], timezone.utc).isoformat().replace("+00:00", "Z") if bars else None
    delay = int(utcnow().timestamp() - bars[-1]["t"] - 300) if bars else None
    print(f"range={rng} fetched={len(bars)} new={n} last_open={last} delay_s={delay}")


def loop():
    while True:
        try:
            et = et_now()
            if in_regular(et):
                res, err = fetch_chart("1d")
                if res is None:
                    print(f"{et.isoformat()} fetch_error {err}", flush=True)
                else:
                    bars = parse_bars(res)
                    # 루프는 최근 완성봉 위주
                    end = floor5(utcnow())
                    bars = [b for b in bars if b["t"] >= int((end - timedelta(minutes=30)).timestamp())]
                    n = save(bars)
                    print(f"{et.isoformat()} fetched={len(bars)} new={n}", flush=True)
            else:
                print(f"{et.isoformat()} out-of-session sleep", flush=True)
        except Exception as e:
            print(f"loop error: {e}")
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
