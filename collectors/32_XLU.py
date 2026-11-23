"""XLU 5분봉 수집 (Yahoo Finance 공개 차트, 키 불필요).
Usage: python 32_XLU.py [--days 20] [--loop]
  --days N: 최근 N거래일 커버 백필(기본 20, 제공 범위 내) | --loop: ET정규장만 5분마다 완성봉 수집
  out: data/XLU/YYYY-MM-DD.jsonl (ET일별)
  src: https://query1.finance.yahoo.com/v8/finance/chart/XLU?range=5d&interval=5m (fallback query2)
  완성봉만 저장, VWAP 미제공=null, 거래대금 근삿값=notional_approx(c*v) 별도 필드
"""
import argparse as A, json as J, time as T
from datetime import datetime as D, timedelta as TD, timezone as TZ
from pathlib import Path as P
import requests as R

BASES = [
    "https://query1.finance.yahoo.com/v8/finance/chart/XLU",
    "https://query2.finance.yahoo.com/v8/finance/chart/XLU",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SRC, SYM, TF = "yahoo", "XLU", "5Min"
OUT = P(__file__).parent / "data" / SYM

def et_now():
    u = D.now(TZ.utc)
    y = u.year
    d0 = D(y, 3, 1, tzinfo=TZ.utc)
    dst_s = (d0 + TD(days=(6 - d0.weekday()) % 7 + 7)).replace(hour=7)
    d1 = D(y, 11, 1, tzinfo=TZ.utc)
    dst_e = (d1 + TD(days=(6 - d1.weekday()) % 7)).replace(hour=6)
    off = TD(hours=-4) if dst_s <= u < dst_e else TD(hours=-5)
    return u, u + off

def _to_epoch(s):
    try:
        return int(D.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None

def fetch_bars(start, end):
    u, _ = et_now()
    p1, p2 = _to_epoch(start), _to_epoch(end)
    now_ep = int(u.timestamp())
    if p1 is None:
        p1 = now_ep - 30 * 86400
    if p2 is None:
        p2 = now_ep
    # Yahoo 5m은 period 범위로 요청, 완성봉·정규장 필터는 아래에서 수행
    last_err = "http_error"
    for base in BASES:
        q = {"period1": p1, "period2": p2, "interval": "5m",
             "includePrePost": "false", "events": "div|split"}
        try:
            r = R.get(base, headers=UA, params=q, timeout=30)
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
            ch = j.get("chart") or {}
            if ch.get("error"):
                last_err = "http_error"
                continue
            res = (ch.get("result") or [None])[0]
            if not res:
                last_err = "empty"
                continue
            ts = res.get("timestamp") or []
            ind = res.get("indicators") or {}
            qq = (ind.get("quote") or [{}])[0]
            oo, hh, ll, cc, vv = (qq.get("open") or []), (qq.get("high") or []), \
                (qq.get("low") or []), (qq.get("close") or []), (qq.get("volume") or [])
            bars = []
            for i, t in enumerate(ts):
                try:
                    op = D.fromtimestamp(int(t), tz=TZ.utc)
                except Exception:
                    continue
                cl = op + TD(minutes=5)
                if cl > u:  # 완성봉만 (형성중/라이브 pseudo-bar 제외)
                    continue
                # ET 정규장(월~금 09:30~16:00 open 기준)만
                d0 = D(op.year, 3, 1, tzinfo=TZ.utc)
                s3 = d0 + TD(days=(6 - d0.weekday()) % 7 + 7)
                d1 = D(op.year, 11, 1, tzinfo=TZ.utc)
                s11 = d1 + TD(days=(6 - d1.weekday()) % 7)
                dst = s3.replace(hour=7) <= op < s11.replace(hour=6)
                opex = op + (-TD(hours=4) if dst else -TD(hours=5))
                if opex.weekday() >= 5:
                    continue
                emin = opex.hour * 60 + opex.minute
                if not (9 * 60 + 30 <= emin < 16 * 60):
                    continue
                o = oo[i] if i < len(oo) else None
                h = hh[i] if i < len(hh) else None
                l = ll[i] if i < len(ll) else None
                c = cc[i] if i < len(cc) else None
                v = vv[i] if i < len(vv) else None
                if o is None and h is None and l is None and c is None:
                    continue  # null뿐인 행 스킵
                bars.append({"t": op.strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "o": o, "h": h, "l": l, "c": c, "v": v,
                             "n": None, "vw": None})
            if bars:
                return bars, "ok"
            last_err = "empty"
        except Exception:
            last_err = "http_error"
            continue
    return [], last_err

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
        d0 = D(op.year, 3, 1, tzinfo=TZ.utc)
        s3 = d0 + TD(days=(6 - d0.weekday()) % 7 + 7)
        d1 = D(op.year, 11, 1, tzinfo=TZ.utc)
        s11 = d1 + TD(days=(6 - d1.weekday()) % 7)
        dst = s3.replace(hour=7) <= op < s11.replace(hour=6)
        opex = op + (-TD(hours=4) if dst else -TD(hours=5))
        grp.setdefault(opex.strftime("%Y-%m-%d"), []).append((op, cl, b))
    for day, rows in grp.items():
        fp = OUT / f"{day}.jsonl"
        seen = set()
        if fp.exists():
            for ln in fp.read_text(encoding="utf-8").splitlines():
                try:
                    k = J.loads(ln).get("bar_open_utc")
                    if k:  # 오류행(None) 무시 -> 뒤늦은 실측 차단 방지
                        seen.add(k)
                except Exception:
                    pass
        with fp.open("a", encoding="utf-8") as f:
            for op, cl, b in sorted(rows, key=lambda x: x[0]):
                o = op.strftime("%Y-%m-%dT%H:%M:%SZ")
                if o in seen:  # 중복금지
                    continue
                c, v = b.get("c"), b.get("v")
                try:
                    notion = float(c) * int(v) if c is not None and v is not None else None
                except Exception:
                    notion = None
                rec = {"source": SRC, "symbol": SYM, "bar_open_utc": o,
                       "bar_close_utc": cl.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "received_at_utc": recv, "o": b.get("o"), "h": b.get("h"),
                       "l": b.get("l"), "c": c, "v": v,
                       "n": None, "vwap": None, "notional_approx": notion,
                       "status": status}
                f.write(J.dumps(rec) + "\n")
                seen.add(o)
                done += 1
    if status != "ok" or not bars:
        st = status if status != "ok" else ("closed" if _is_closed(et) else "delayed")
        fp = OUT / f"{et.strftime('%Y-%m-%d')}.jsonl"
        rec = {"source": SRC, "symbol": SYM, "bar_open_utc": None, "bar_close_utc": None,
               "received_at_utc": recv, "o": None, "h": None, "l": None, "c": None,
               "v": None, "n": None, "vwap": None, "notional_approx": None, "status": st}
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
        u, et = et_now()
        back = max(a.days * 7 // 5 + 2, 30) if a.days >= 20 else a.days + 4
        s = (u - TD(days=back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = u.strftime("%Y-%m-%dT%H:%M:%SZ")
        bars, st = fetch_bars(s, e)
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"once days~{a.days} status={st} saved={n}")
        return
    while True:
        u, et = et_now()
        if _is_closed(et):
            print(f"closed {et:%Y-%m-%d %H:%M} ET skip")
            T.sleep(300)
            continue
        s = (u - TD(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = u.strftime("%Y-%m-%dT%H:%M:%SZ")
        bars, st = fetch_bars(s, e)
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"loop {et:%H:%M} ET status={st} saved={n}")
        T.sleep(300)

if __name__ == "__main__":
    main()
