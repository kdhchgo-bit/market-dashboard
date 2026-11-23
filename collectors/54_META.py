"""META 5분봉 수집기 (커뮤니케이션 대표, 섹터 XLC).
원천: Yahoo Finance 공개 차트 (키 불필요) — https://query1.finance.yahoo.com/v8/finance/chart/META?range=5d&interval=5m
GET https://query1.finance.yahoo.com/v8/finance/chart/META?range=1mo&interval=5m&includePrePost=false (query2 폴백)
필드: 정규장 완성 5분봉 OHLC + 거래량(v). Yahoo 미제공 n(체결수)/VWAP는 null 보존, 거래대금 근삿값은 notional_est(c*v) 별도 기록.
용법: python 54_META.py (최근 20거래일 1회) / python 54_META.py --loop (ET 정규장 5분 폴링, 완성봉만).
저장: <this_dir>/data/META/YYYY-MM-DD.jsonl (bar_open_utc 날짜별, 중복금지).
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "META"
SECTOR = "XLC"
SOURCE = "yahoo-5m"
BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/META",
    "https://query2.finance.yahoo.com/v8/finance/chart/META",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TF_MIN = 5
OUT = Path(__file__).parent / "data" / "META"


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


def et_of(utc_dt):
    try:
        from zoneinfo import ZoneInfo
        return utc_dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        isdst = time.localtime().tm_isdst != 0
        return utc_dt - timedelta(hours=4 if isdst else 5)


def range_for(days):
    if days <= 5:
        return "5d"
    if days <= 45:
        return "1mo"
    return "3mo"


def fetch(range_="1mo"):
    """Yahoo chart 5m → 정규장 완성봉만 [{t,o,h,l,c,v}] (t: ISO Z)."""
    now = utcnow()
    params = {"range": range_, "interval": "5m", "includePrePost": "false",
              "events": "div,splits"}
    data = None
    for base in BASES:
        try:
            r = requests.get(base, params=params, headers=UA, timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("chart", {}).get("result"):
                break
            data = None
        except Exception:
            data = None
    if not data:
        return []
    res = (data.get("chart", {}).get("result") or [None])[0]
    if not res:
        return []
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c, v = (q.get("open") or [], q.get("high") or [], q.get("low") or [],
                     q.get("close") or [], q.get("volume") or [])
    out = []
    for i, epoch in enumerate(ts):
        try:
            if epoch % (TF_MIN * 60) != 0:
                continue  # 실시간 미완성 인용 pseudo-bar 제외
            bt = datetime.fromtimestamp(epoch, timezone.utc)
            if bt + timedelta(minutes=TF_MIN) > now:
                continue  # 완성봉만
            if not in_regular(et_of(bt)):
                continue  # 정규장만
            oo, hh, ll, cc = o[i], h[i], l[i], c[i]
            if oo is None or hh is None or ll is None or cc is None:
                continue
            vv = v[i] if i < len(v) else None
            out.append({"t": bt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "o": oo, "h": hh, "l": ll, "c": cc, "v": vv})
        except Exception:
            continue
    return out


def seen_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                k = json.loads(line).get("bar_open_utc")
                if k:  # 오류행(None) 무시 → 뒤늦은 실측 저장 차단 방지
                    s.add(k)
            except Exception:
                pass
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
        if bt + timedelta(minutes=TF_MIN) > utcnow():
            continue  # 완성봉만
        day = t[:10]
        path = OUT / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if t in seen_keys(path):
            continue
        cc, vv = b.get("c"), b.get("v")
        try:
            notional = round(float(cc) * float(vv), 2) if cc is not None and vv is not None else None
        except Exception:
            notional = None
        rec = {"source": SOURCE, "symbol": SYMBOL, "sector": SECTOR, "bar_open_utc": t,
               "received_at_utc": now, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
               "c": cc, "v": vv, "n": None, "vwap": None,
               "notional_est": notional, "status": status}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        n_new += 1
    return n_new


def once(days=20):
    bars = fetch(range_for(int(days)))
    n = save(bars)
    print(f"fetched={len(bars)} new={n}")


def loop():
    while True:
        try:
            et = et_now()
            if in_regular(et):
                end = floor5(utcnow())
                _ = end
                bars = fetch("1d")
                n = save(bars)
                print(f"{et.isoformat()} fetched={len(bars)} new={n}", flush=True)
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
