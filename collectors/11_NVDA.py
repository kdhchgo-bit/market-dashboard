"""NVDA 5분봉 수집기 (기술업종 대표, 섹터 XLK).
원천: Yahoo Finance 공개 차트 (키 없음) — https://query1.finance.yahoo.com/v8/finance/chart/NVDA?range=5d&interval=5m
GET https://query1.finance.yahoo.com/v8/finance/chart/NVDA?period1={epoch}&period2={epoch}&interval=5m&includePrePost=false (폴백 query2)
정규장 완성 5분봉 OHLCV만 저장. VWAP 미제공 → null 보존, 거래대금 근삿값은 notional_est 별도 필드.
용법: python 11_NVDA.py (최근 20거래일 1회) / python 11_NVDA.py --loop (ET 정규장 5분 폴링, 완성봉만).
저장: <this_dir>/data/NVDA/YYYY-MM-DD.jsonl (bar_open_utc 날짜별, 중복금지).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "NVDA"
SECTOR = "XLK"
SOURCE = "yahoo-chart"
BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/NVDA",
    "https://query2.finance.yahoo.com/v8/finance/chart/NVDA",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) market-collector"}
OUT = Path(__file__).parent / "data" / "NVDA"


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


def fetch(start, end):
    params = {
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "5m",
        "includePrePost": "false",
        "events": "div|split",
    }
    last_err = None
    for base in BASES:
        try:
            r = requests.get(base, headers=UA, params=params, timeout=30)
        except Exception as e:
            last_err = e
            continue
        if r.status_code != 200:
            last_err = RuntimeError(f"http {r.status_code}")
            continue
        try:
            j = r.json()
        except Exception as e:
            last_err = e
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                err = (j.get("chart") or {}).get("error")
                last_err = RuntimeError(f"empty result err={err}")
                continue
            res = res[0]
            ts = res.get("timestamp") or []
            ind = res.get("indicators") or {}
            q = (ind.get("quote") or [{}])[0]
            oo, hh, ll, cc, vv = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or [], q.get("volume") or []
            out = []
            for i, t in enumerate(ts):
                try:
                    o = oo[i] if i < len(oo) else None
                    h = hh[i] if i < len(hh) else None
                    l = ll[i] if i < len(ll) else None
                    c = cc[i] if i < len(cc) else None
                    v = vv[i] if i < len(vv) else None
                except Exception:
                    continue
                if c is None:
                    continue  # null뿐인 행 스킵
                bt = datetime.fromtimestamp(int(t), tz=timezone.utc)
                out.append({"t": bt.strftime("%Y-%m-%dT%H:%M:%SZ"), "o": o, "h": h, "l": l, "c": c, "v": v})
            out.sort(key=lambda b: b["t"])
            return out
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"yahoo fetch failed: {last_err}")


def seen_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                s.add(json.loads(line).get("bar_open_utc"))
            except Exception:
                pass
    return s


def save(bars, status="ok"):
    now_dt = utcnow()
    now = now_dt.isoformat().replace("+00:00", "Z")
    try:
        from zoneinfo import ZoneInfo
        nyc = ZoneInfo("America/New_York")
    except Exception:
        nyc = None
    is_dst_now = time.localtime().tm_isdst != 0
    # 일자별 seen 캐시 (봉마다 파일 재조회 방지, 판정은 동일: bar_open_utc 중복금지)
    seen_cache = {}
    staged = {}
    for b in bars:
        t = b.get("t", "")
        try:
            bt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            continue
        if bt + timedelta(minutes=5) > now_dt:
            continue  # 완성봉만
        # 정규장만 (Yahoo includePrePost=false + ET 재확인)
        if nyc is not None:
            et = bt.astimezone(nyc)
        else:
            et = bt - timedelta(hours=4 if is_dst_now else 5)
        if not in_regular(et):
            continue
        day = t[:10]
        seen = seen_cache.get(day)
        if seen is None:
            seen = seen_keys(OUT / f"{day}.jsonl")
            seen_cache[day] = seen
        if t in seen:
            continue
        staged.setdefault(day, []).append((t, b))
        seen.add(t)
    OUT.mkdir(parents=True, exist_ok=True)
    n_new = 0
    for day in sorted(staged):
        path = OUT / f"{day}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            for t, b in sorted(staged[day], key=lambda x: x[0]):
                o, h, l, c, v = b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v")
                try:
                    notional = round(((float(h) + float(l) + float(c)) / 3.0) * int(v), 2) if h is not None and l is not None and c is not None and v is not None else None
                except Exception:
                    notional = None
                rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR, "bar_open_utc": t,
                       "received_at_utc": now, "o": o, "h": h, "l": l,
                       "c": c, "v": v, "n": None, "vwap": None, "notional_est": notional, "status": status}
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
