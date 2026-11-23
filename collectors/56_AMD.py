"""AMD 5분봉 수집기 (기술업종, 섹터 XLK).
원천: Yahoo Finance 공개 차트 (키 불필요) — https://query1.finance.yahoo.com/v8/finance/chart/AMD?range=5d&interval=5m
정규장 완성 5분봉 OHLCV만 저장. VWAP 미제공 → null, 거래대금 근삿값은 turnover_approx(c*v) 별도 필드.
용법: python 56_AMD.py (최근 20거래일 1회) / python 56_AMD.py --loop (ET 정규장 5분 폴링, 완성봉만).
저장: <this_dir>/data/AMD/YYYY-MM-DD.jsonl (bar_open_utc 날짜별, 중복금지).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "AMD"
SECTOR = "XLK"
SOURCE = "yahoo-chart"
YHOSTS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/AMD",
    "https://query2.finance.yahoo.com/v8/finance/chart/AMD",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT = Path(__file__).parent / "data" / "AMD"


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


def _et_of(bt):
    try:
        from zoneinfo import ZoneInfo
        return bt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # EST(UTC-5)/EDT(UTC-4) 근사: 미국 동부 일광절약 판정은 호출 시점 기준
        return bt - timedelta(hours=4 if time.localtime().tm_isdst else 5)


def fetch(start, end):
    p1, p2 = int(start.timestamp()), int(end.timestamp())
    if p2 <= p1:
        return []
    last_err = None
    for base in YHOSTS:
        try:
            r = requests.get(base, headers=UA,
                             params={"period1": p1, "period2": p2,
                                     "interval": "5m", "includePrePost": "false"},
                             timeout=30)
            r.raise_for_status()
            j = r.json()
        except Exception as e:
            last_err = e
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                err = ((j.get("chart") or {}).get("error") or {})
                last_err = RuntimeError(str(err.get("description") or err.get("code") or "empty chart result"))
                continue
            ts = res[0].get("timestamp") or []
            q = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
            opens, highs = q.get("open") or [], q.get("high") or []
            lows, closes = q.get("low") or [], q.get("close") or []
            vols = q.get("volume") or []
            out = []
            for i, t in enumerate(ts):
                try:
                    o = opens[i] if i < len(opens) else None
                    h = highs[i] if i < len(highs) else None
                    lo = lows[i] if i < len(lows) else None
                    c = closes[i] if i < len(closes) else None
                    v = vols[i] if i < len(vols) else None
                except Exception:
                    continue
                if o is None or h is None or lo is None or c is None:
                    continue  # 미완성/형성중 봉(placeholder) 제외
                if int(t) % 300 != 0:
                    continue  # 5분 정렬이 아닌 형성중 스냅샷 제외
                if v is None or int(v) <= 0:
                    continue  # 윈도우 경계 부분봉/형성중 placeholder 제외
                bt = datetime.fromtimestamp(int(t), timezone.utc)
                et = _et_of(bt)
                if not in_regular(et):
                    continue  # 정규장만
                out.append({"t": bt.isoformat().replace("+00:00", "Z"),
                            "o": o, "h": h, "l": lo, "c": c, "v": int(v) if v is not None else 0})
            out.sort(key=lambda b: b["t"])
            return out
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"yahoo chart fetch failed: {last_err}")


def seen_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            # 과거 오류행(status!=ok, 무효 OHLC)이 뒤늦은 실측 저장을 막지 않게 정상행만 중복키로 인정
            if rec.get("status", "ok") != "ok":
                continue
            k = rec.get("bar_open_utc")
            if not isinstance(k, str) or not k:
                continue
            if rec.get("o") is None and rec.get("c") is None:
                continue
            s.add(k)
    return s


def save(bars, status="ok"):
    now = utcnow().isoformat().replace("+00:00", "Z")
    n_new = 0
    for b in bars:
        t = b.get("t", "")
        try:
            bt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            continue
        if bt + timedelta(minutes=5) > utcnow():
            continue  # 완성봉만
        day = t[:10]
        path = OUT / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if t in seen_keys(path):
            continue
        c, v = b.get("c"), b.get("v")
        try:
            turnover = float(c) * int(v) if c is not None and v is not None else None
        except Exception:
            turnover = None
        rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR, "bar_open_utc": t,
               "received_at_utc": now, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
               "c": c, "v": v, "n": None, "vwap": None, "turnover_approx": turnover, "status": status}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n_new += 1
    return n_new


def once(days=20):
    end = utcnow()
    start = end - timedelta(days=int(days * 1.5 + 5))  # 20거래일 커버
    bars = fetch(start, end)
    n = save(bars)
    print(f"fetched={len(bars)} new={n}")


def loop():
    while True:
        try:
            et = et_now()
            if in_regular(et):
                end = floor5(utcnow())
                bars = fetch(end - timedelta(minutes=15), end)
                n = save(bars)
                print(f"{et.isoformat()} fetched={len(bars)} new={n}", flush=True)
            else:
                print(f"{et.isoformat()} out-of-session sleep", flush=True)
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
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
