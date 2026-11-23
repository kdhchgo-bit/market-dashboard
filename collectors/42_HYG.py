"""HYG(하이일드) 5분봉 수집 (Yahoo Finance 공개 차트, 키 불필요).
Usage: python 42_HYG.py [--days 20] [--loop]
  --days N: 최근 N거래일 커버 백필(기본 20, Yahoo 제공 범위 내) | --loop: ET정규장만 5분마다 완성봉 수집
  keys: 없음(Yahoo 공개 차트) | out: data/HYG/YYYY-MM-DD.jsonl
  docs: https://query1.finance.yahoo.com/v8/finance/chart/HYG?range=5d&interval=5m
"""
import argparse as A, json as J, time as T
from datetime import datetime as D, timedelta as TD, timezone as TZ
from pathlib import Path as P
import requests as R

HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
SRC, SYM, TF = "yahoo-chart", "HYG", "5Min"
OUT = P(__file__).parent / "data" / SYM
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}

def et_now():
    u = D.now(TZ.utc)
    y = u.year
    d0 = D(y, 3, 1, tzinfo=TZ.utc)
    dst_s = (d0 + TD(days=(6 - d0.weekday()) % 7 + 7)).replace(hour=7)
    d1 = D(y, 11, 1, tzinfo=TZ.utc)
    dst_e = (d1 + TD(days=(6 - d1.weekday()) % 7)).replace(hour=6)
    off = TD(hours=-4) if dst_s <= u < dst_e else TD(hours=-5)
    return u, u + off

def _et_off(op):
    d0 = D(op.year, 3, 1, tzinfo=TZ.utc)
    s3 = d0 + TD(days=(6 - d0.weekday()) % 7 + 7)
    d1 = D(op.year, 11, 1, tzinfo=TZ.utc)
    s11 = d1 + TD(days=(6 - d1.weekday()) % 7)
    dst = s3.replace(hour=7) <= op < s11.replace(hour=6)
    return -TD(hours=4) if dst else -TD(hours=5)

def _range_for_days(days):
    if days <= 5:
        return "5d"
    if days <= 30:
        return "1mo"
    return "3mo"

def _parse_result(res):
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    oo, hh, ll, cc, vv = q.get("open") or [], q.get("high") or [], q.get("low") or [], q.get("close") or [], q.get("volume") or []
    bars = []
    for i, t in enumerate(ts):
        try:
            o = oo[i] if i < len(oo) else None
            h = hh[i] if i < len(hh) else None
            l = ll[i] if i < len(ll) else None
            c = cc[i] if i < len(cc) else None
            v = vv[i] if i < len(vv) else None
        except Exception:
            continue
        if o is None or h is None or l is None or c is None:
            continue
        try:
            op = D.fromtimestamp(int(t), TZ.utc)
        except Exception:
            continue
        bars.append({"t": op.strftime("%Y-%m-%dT%H:%M:%SZ"), "o": o, "h": h, "l": l, "c": c, "v": v})
    return bars

def fetch_bars(days=20):
    want = _range_for_days(days)
    chain = [want] + [r for r in ("1mo", "5d") if r != want]
    last = "http_error"
    for rng in chain:
        for host in HOSTS:
            try:
                r = R.get(f"{host}/v8/finance/chart/{SYM}",
                          params={"range": rng, "interval": "5m", "includePrePost": "false", "events": "div|split"},
                          headers=UA, timeout=30)
            except Exception:
                last = "http_error"
                continue
            if r.status_code == 429:
                return [], "rate_limited"
            if r.status_code != 200:
                last = "http_error"
                continue
            try:
                j = r.json()
            except Exception:
                last = "http_error"
                continue
            ch = j.get("chart") or {}
            res = (ch.get("result") or [None])[0]
            if not res:
                last = ch.get("error", {}).get("code") if isinstance(ch.get("error"), dict) else "empty"
                last = "empty" if last in (None, "ok") else str(last)
                continue
            bars = _parse_result(res)
            if not bars:
                last = "empty"
                continue
            return bars, "ok"
    return [], last or "http_error"

def save_bars(bars, status="ok"):
    u, et = et_now()
    recv = u.strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    done = 0
    grp = {}
    for b in bars:
        try:
            op = D.fromisoformat(str(b["t"]).replace("Z", "+00:00"))
        except Exception:
            continue
        cl = op + TD(minutes=5)
        if cl > u:  # 완성봉만
            continue
        opex = op + _et_off(op)
        if opex.weekday() >= 5:  # 정규장(ET 월~금 09:30~16:00)만
            continue
        if not (TD(hours=9, minutes=30) <= TD(hours=opex.hour, minutes=opex.minute) < TD(hours=16)):
            continue
        grp.setdefault(opex.strftime("%Y-%m-%d"), []).append((op, cl, b))
    for day, rows in grp.items():
        fp = OUT / f"{day}.jsonl"
        seen = set()
        if fp.exists():
            for ln in fp.read_text(encoding="utf-8").splitlines():
                try:
                    seen.add(J.loads(ln).get("bar_open_utc"))
                except Exception:
                    pass
        with fp.open("a", encoding="utf-8") as f:
            for op, cl, b in sorted(rows, key=lambda x: x[0]):
                o = op.strftime("%Y-%m-%dT%H:%M:%SZ")
                if o in seen:  # 중복금지
                    continue
                v = b.get("v")
                try:
                    typ = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0
                    notional = round(typ * float(v), 2) if v is not None else None
                except Exception:
                    notional = None
                rec = {"source": SRC, "symbol": SYM, "bar_open_utc": o,
                       "bar_close_utc": cl.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "received_at_utc": recv, "o": b.get("o"), "h": b.get("h"),
                       "l": b.get("l"), "c": b.get("c"), "v": b.get("v"),
                       "n": None, "vwap": None, "notional_est": notional, "status": status}
                f.write(J.dumps(rec) + "\n")
                seen.add(o)
                done += 1
    if status != "ok" or not bars:
        st = status if status != "ok" else ("closed" if _is_closed(et) else "delayed")
        fp = OUT / f"{et.strftime('%Y-%m-%d')}.jsonl"
        rec = {"source": SRC, "symbol": SYM, "bar_open_utc": None, "bar_close_utc": None,
               "received_at_utc": recv, "o": None, "h": None, "l": None, "c": None,
               "v": None, "n": None, "vwap": None, "notional_est": None, "status": st}
        with fp.open("a", encoding="utf-8") as f:
            f.write(J.dumps(rec) + "\n")
    return done

def _is_closed(et):
    return et.weekday() >= 5 or not (TD(hours=9, minutes=30) <= TD(hours=et.hour, minutes=et.minute) < TD(hours=16))

def main():
    ap = A.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    if not a.loop:
        bars, st = fetch_bars(a.days)
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"once days~{a.days} status={st} saved={n}")
        return
    while True:
        u, et = et_now()
        if _is_closed(et):
            print(f"closed {et:%Y-%m-%d %H:%M} ET skip")
            T.sleep(300)
            continue
        bars, st = fetch_bars(5)
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"loop {et:%H:%M} ET status={st} saved={n}")
        T.sleep(300)

if __name__ == "__main__":
    main()
