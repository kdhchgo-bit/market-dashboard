"""XLP 5분봉 수집 (Yahoo Finance 공개 차트, 키 불필요).
Usage: python 33_XLP.py [--days 20] [--loop]
  --days N: 최근 N거래일 커버 백필(기본 20, 제공 범위 내) | --loop: ET정규장만 5분마다 완성봉 수집
  out: data/XLP/YYYY-MM-DD.jsonl (ET 일자별)
  docs: https://query1.finance.yahoo.com/v8/finance/chart/XLP?range=5d&interval=5m
"""
import argparse as A, json as J, time as T
from datetime import datetime as D, timedelta as TD, timezone as TZ
from pathlib import Path as P
import requests as R

SYM = "XLP"
SRC = "yahoo-chart"
TF_MIN = 5
OUT = P(__file__).parent / "data" / SYM
URLS = [
    f"https://query1.finance.yahoo.com/v8/finance/chart/{SYM}",
    f"https://query2.finance.yahoo.com/v8/finance/chart/{SYM}",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def et_now():
    u = D.now(TZ.utc)
    y = u.year
    d0 = D(y, 3, 1, tzinfo=TZ.utc)
    dst_s = (d0 + TD(days=(6 - d0.weekday()) % 7 + 7)).replace(hour=7)
    d1 = D(y, 11, 1, tzinfo=TZ.utc)
    dst_e = (d1 + TD(days=(6 - d1.weekday()) % 7)).replace(hour=6)
    off = TD(hours=-4) if dst_s <= u < dst_e else TD(hours=-5)
    return u, u + off


def _et_of(op):
    d0 = D(op.year, 3, 1, tzinfo=TZ.utc)
    s3 = d0 + TD(days=(6 - d0.weekday()) % 7 + 7)
    d1 = D(op.year, 11, 1, tzinfo=TZ.utc)
    s11 = d1 + TD(days=(6 - d1.weekday()) % 7)
    dst = s3.replace(hour=7) <= op < s11.replace(hour=6)
    return op + (-TD(hours=4) if dst else -TD(hours=5))


def fetch_bars(start_ts, end_ts):
    q = {"period1": int(start_ts), "period2": int(end_ts),
         "interval": "5m", "includePrePost": "false"}
    last_st = "http_error"
    for url in URLS:
        try:
            r = R.get(url, params=q, headers=UA, timeout=30)
        except Exception:
            last_st = "http_error"
            continue
        if r.status_code == 429:
            return [], "rate_limited"
        if r.status_code != 200:
            last_st = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            last_st = "http_error"
            continue
        try:
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                return [], "empty"
            res = res[0]
            ts = res.get("timestamp") or []
            qq = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            oo, hh, ll, cc, vv = (qq.get("open") or [], qq.get("high") or [],
                                 qq.get("low") or [], qq.get("close") or [],
                                 qq.get("volume") or [])
        except Exception:
            return [], "http_error"
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
            bars.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": v})
        return bars, "ok"
    return [], last_st


def save_bars(bars, status="ok"):
    u, et = et_now()
    recv = u.strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    done = 0
    grp = {}
    for b in bars:
        try:
            op = D.fromtimestamp(int(b["t"]), tz=TZ.utc)
        except Exception:
            continue
        # 완성봉만: 5분 그리드 + 종가시각이 현재 이하
        if int(b["t"]) % (TF_MIN * 60) != 0:
            continue
        cl = op + TD(minutes=TF_MIN)
        if cl > u:
            continue
        o, h, l, c, v = b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v")
        if o is None and h is None and l is None and c is None:
            continue
        ex = _et_of(op)
        # 정규장만 (ET 월~금 09:30~16:00, Yahoo는 includePrePost=false이나 이중 확인)
        if ex.weekday() >= 5:
            continue
        if not (TD(hours=9, minutes=30) <= TD(hours=ex.hour, minutes=ex.minute) < TD(hours=16)):
            continue
        grp.setdefault(ex.strftime("%Y-%m-%d"), []).append((op, cl, o, h, l, c, v))
    for day, rows in grp.items():
        fp = OUT / f"{day}.jsonl"
        seen_valid = set()
        old_lines = []
        if fp.exists():
            for ln in fp.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                try:
                    r0 = J.loads(ln)
                except Exception:
                    continue
                k = r0.get("bar_open_utc")
                # 유효 실측만 중복 기준, 오류(null) 행은 뒤늦은 실측이 대체 가능
                if k and r0.get("o") is not None:
                    seen_valid.add(k)
                old_lines.append((k, r0, ln))
        new_keys = {op.strftime("%Y-%m-%dT%H:%M:%SZ") for op, cl, o, h, l, c, v in rows}
        # 같은 시각의 과거 오류 행은 제거 후 실측으로 대체 (이력 유효행은 보존)
        if any(k in new_keys for k, r0, ln in old_lines if k and r0.get("o") is None):
            kept = [ln for k, r0, ln in old_lines
                    if not (k in new_keys and r0.get("o") is None)]
            fp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        with fp.open("a", encoding="utf-8") as f:
            for op, cl, o, h, l, c, v in sorted(rows, key=lambda x: x[0]):
                k = op.strftime("%Y-%m-%dT%H:%M:%SZ")
                if k in seen_valid:  # 중복금지
                    continue
                try:
                    notional = (float(c) * float(v)) if (c is not None and v is not None) else None
                except Exception:
                    notional = None
                rec = {"source": SRC, "symbol": SYM, "bar_open_utc": k,
                       "bar_close_utc": cl.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "received_at_utc": recv, "o": o, "h": h,
                       "l": l, "c": c, "v": v,
                       "n": None, "vwap": None, "notional": notional,
                       "status": status}
                f.write(J.dumps(rec) + "\n")
                seen_valid.add(k)
                done += 1
    if status != "ok" or not bars or done == 0:
        # 휴장/지연 등 신규 실측이 없으면 당일 파일에 상태행 1줄 (실측 중복 방지용 bar_open_utc=None)
        if done == 0:
            st = status if status != "ok" else ("closed" if _is_closed(et) else "delayed")
            fp = OUT / f"{et.strftime('%Y-%m-%d')}.jsonl"
            # ponytail: same null-bar status tail -> skip append (rerun dup guard)
            try:
                if fp.exists():
                    _ls = [ln for ln in fp.read_text(encoding="utf-8").splitlines() if ln.strip()]
                    if _ls:
                        _rl = J.loads(_ls[-1])
                        if _rl.get("bar_open_utc") is None and _rl.get("status") == st:
                            return done
            except Exception:
                pass
            rec = {"source": SRC, "symbol": SYM, "bar_open_utc": None, "bar_close_utc": None,
                   "received_at_utc": recv, "o": None, "h": None, "l": None, "c": None,
                   "v": None, "n": None, "vwap": None, "notional": None, "status": st}
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
        e = int(u.timestamp())
        s = int((u - TD(days=back)).timestamp())
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
        e = int(u.timestamp())
        s = int((u - TD(days=2)).timestamp())
        bars, st = fetch_bars(s, e)
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"loop {et:%H:%M} ET status={st} saved={n}")
        T.sleep(300)


if __name__ == "__main__":
    main()
