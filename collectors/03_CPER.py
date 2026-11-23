"""CPER 5Min collector (United States Copper Index Fund, 구리 5분봉).
Usage: python 03_CPER.py (최근 20거래일 백필 1회 수집)
Usage: python 03_CPER.py --loop (ET 정규장만 5분 간격 반복)
Usage: python 03_CPER.py --days 20 (백필 거래일 수, 기본 20; Yahoo range=1mo 범위 내 적용)
Env: 키 불필요 (Yahoo Finance 공개 차트, 키 없이 동작)
Out: data/CPER/YYYY-MM-DD.jsonl (중복 제외, 완성봉만, 가짜가격 없음, ponytail=append-only, 이력 보존)
Src: Yahoo Finance chart CPER 5m (https://query1.finance.yahoo.com/v8/finance/chart/CPER?range=1mo&interval=5m)
Note: Yahoo 원천이 제공하지 않는 VWAP/n은 null로 보존, 거래대금 근삿값(notional=c*v)은 별도 필드로 표시.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYMBOL = "CPER"
SOURCE = "yahoo-chart-5m"
BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/CPER",
    "https://query2.finance.yahoo.com/v8/finance/chart/CPER",
]
OUT = Path(__file__).parent / "data" / "CPER"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) market-collector/1.0"}


def to_et(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        y = dt.year
        m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
        d1 = (6 - m1.weekday()) % 7
        dst_start = datetime(y, 3, 1 + d1 + 7, 7, tzinfo=timezone.utc)
        m2 = datetime(y, 11, 1, tzinfo=timezone.utc)
        d2 = (6 - m2.weekday()) % 7
        dst_end = datetime(y, 11, 1 + d2, 6, tzinfo=timezone.utc)
        off = 4 if dst_start <= dt < dst_end else 5
        return (dt - timedelta(hours=off)).replace(tzinfo=None)


def fetch_bars(range_="1mo"):
    """Yahoo chart 5m 수집. returns (bars, status). bar={t,o,h,l,c,v} t=ISO Z."""
    params = {"range": range_, "interval": "5m", "includePrePost": "false",
              "events": "div|splits"}
    last_err = "http_error"
    for base in BASES:
        try:
            r = requests.get(base, headers=UA, params=params, timeout=30)
        except Exception:
            last_err = "http_error"
            continue
        if r.status_code == 429:
            return [], "rate_limited"
        if r.status_code != 200:
            last_err = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = "http_error"
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                err = (j.get("chart") or {}).get("error")
                if err:
                    last_err = "http_error"
                    continue
                return [], "empty"
            res = res[0]
            ts = res.get("timestamp") or []
            q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            opens = q.get("open") or []
            highs = q.get("high") or []
            lows = q.get("low") or []
            closes = q.get("close") or []
            vols = q.get("volume") or []
        except Exception:
            last_err = "http_error"
            continue
        bars = []
        for i, t in enumerate(ts):
            try:
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                lo = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = vols[i] if i < len(vols) else None
            except Exception:
                continue
            if o is None and h is None and lo is None and c is None:
                continue
            try:
                bt = datetime.fromtimestamp(int(t), tz=timezone.utc)
            except Exception:
                continue
            bars.append({"t": bt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "o": o, "h": h, "l": lo, "c": c, "v": v})
        if not bars:
            return [], "empty"
        return bars, "ok"
    return [], last_err


def _valid_seen(p):
    seen = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                j = json.loads(line)
            except Exception:
                continue
            # 과거 오류행(o=None)이 같은 시각의 뒤늦은 실측 저장을 막지 않게
            # 유효가 있는 행만 중복 기준으로 삼는다. (ponytail: append-only, error rows never block)
            if j.get("o") is None:
                continue
            v = j.get("bar_open_utc")
            if v is not None:
                seen.add(v)
    return seen


def save_bars(bars, hint="ok"):
    now = datetime.now(timezone.utc)
    recv = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not bars and hint != "ok":
        et = to_et(now)
        day = et.strftime("%Y-%m-%d") if hasattr(et, "strftime") else recv[:10]
        p = OUT / (day + ".jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": recv,
               "bar_close_utc": recv, "received_at_utc": recv, "o": None,
               "h": None, "l": None, "c": None, "v": None, "n": None,
               "vwap": None, "notional": None, "status": hint}
        with open(p, "a", encoding="utf-8") as f:  # ponytail: append only, never rewrite history
            f.write(json.dumps(rec) + "\n")
        return 0
    cache = {}
    new = 0
    for b in bars:
        t = b.get("t", "")
        try:
            bt = datetime.strptime(str(t), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            try:
                bt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                if bt.tzinfo is None:
                    bt = bt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
        close = bt + timedelta(minutes=5)
        if close > now:
            continue  # 미완성봉 제외
        et = to_et(bt)
        day = et.strftime("%Y-%m-%d") if hasattr(et, "strftime") else str(t)[:10]
        p = OUT / (day + ".jsonl")
        if p not in cache:
            cache[p] = _valid_seen(p)
        if t in cache[p]:
            continue
        gap = (now - close).total_seconds()
        st = hint if hint != "ok" else ("delayed" if gap > 900 else "ok")
        c, v = b.get("c"), b.get("v")
        try:
            notional = (float(c) * int(v)) if c is not None and v is not None else None
        except Exception:
            notional = None
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": t,
               "bar_close_utc": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "received_at_utc": recv, "o": b.get("o"), "h": b.get("h"),
               "l": b.get("l"), "c": c, "v": v,
               "n": None, "vwap": None, "notional": notional, "status": st}
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:  # ponytail: append only, never rewrite history
            f.write(json.dumps(rec) + "\n")
        cache[p].add(t)
        new += 1
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--days", type=int, default=20)
    a = ap.parse_args()
    if not a.loop:
        bars, st = fetch_bars("1mo")
        if st != "ok":
            save_bars([], st)
            print(f"fetch {st}", flush=True)
            raise SystemExit(1)
        # --days: 최근 N거래일(근사 1.5배 달력일) 범위 내로 제한, 기본 20
        try:
            days = max(1, int(a.days))
        except Exception:
            days = 20
        if days < 60:
            cutoff = datetime.now(timezone.utc) - timedelta(days=int(days * 1.5 + 5))
            bars = [b for b in bars if b.get("t", "") >= cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")]
        n = save_bars(bars, "ok")
        print(f"fetched={len(bars)} new={n} status=ok", flush=True)
        return
    while True:
        now = datetime.now(timezone.utc)
        et = to_et(now)
        wd = et.weekday() if hasattr(et, "weekday") else 5
        hm = (et.hour, et.minute) if hasattr(et, "hour") else (0, 0)
        if wd < 5 and (9, 30) <= hm < (16, 0):
            bars, st = fetch_bars("5d")
            if st != "ok":
                save_bars([], st)
                print(f"fetch {st}", flush=True)
            else:
                # 루프는 최근 60분만 저장 (중복 제외로 누적)
                cutoff = now - timedelta(minutes=60)
                recent = [b for b in bars
                          if b.get("t", "") >= cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")]
                n = save_bars(recent if recent else bars, "ok")
                print(f"fetched={len(bars)} new={n} status=ok", flush=True)
        else:
            print("closed: outside ET 09:30-16:00 weekday", flush=True)
        time.sleep(300 - time.time() % 300)


if __name__ == "__main__":
    main()
