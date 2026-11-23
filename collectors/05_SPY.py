"""SPY 5분봉 수집 (Yahoo Finance 공개 차트, 키 불필요).
Usage: python 05_SPY.py [--days 20] [--loop]
  --days N: 최근 N거래일 커버 백필(기본 20, N>5면 1mo 범위) | --loop: ET정규장만 5분마다 완성봉 수집
  keys: 불필요 (공개 차트) | out: data/SPY/YYYY-MM-DD.jsonl (ponytail=append-only, 이력 보존)
  docs: https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=5m
"""
import argparse as A, json as J, time as T
from datetime import datetime as D, timedelta as TD, timezone as TZ
from pathlib import Path as P
import requests as R

HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
SRC, SYM, TF = "yahoo-chart", "SPY", "5m"
OUT = P(__file__).parent / "data" / SYM
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


def fetch_bars(rng="1mo"):
    bars = []
    status = "ok"
    for host in HOSTS:
        try:
            r = R.get(f"{host}/v8/finance/chart/{SYM}",
                      params={"range": rng, "interval": "5m", "includePrePost": "false"},
                      headers=UA, timeout=30)
        except Exception:
            status = "http_error"
            continue
        if r.status_code == 429:
            status = "rate_limited"
            continue
        if r.status_code != 200:
            status = "http_error"
            continue
        try:
            j = r.json()
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                status = "empty"
                continue
            res = res[0]
            ts = res.get("timestamp") or []
            q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            o, h, l, c, v = (q.get("open") or []), (q.get("high") or []), \
                (q.get("low") or []), (q.get("close") or []), (q.get("volume") or [])
            u = D.now(TZ.utc)
            out = []
            for i, t in enumerate(ts):
                try:
                    op = D.fromtimestamp(int(t), tz=TZ.utc)
                except Exception:
                    continue
                if int(t) % 300 != 0:  # 정규 5분 경계가 아닌 라이브 틱 제외
                    continue
                cl = op + TD(minutes=5)
                if cl > u:  # 완성봉만
                    continue
                vals = [o[i] if i < len(o) else None, h[i] if i < len(h) else None,
                        l[i] if i < len(l) else None, c[i] if i < len(c) else None,
                        v[i] if i < len(v) else None]
                if vals[0] is None and vals[1] is None and vals[2] is None and vals[3] is None:
                    continue
                out.append({"t": op.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "o": vals[0], "h": vals[1], "l": vals[2], "c": vals[3], "v": vals[4]})
            if out:
                return out, "ok"
            status = "empty"
        except Exception:
            status = "http_error"
            continue
    return bars, status


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
                except Exception:
                    continue
                if k:  # 과거 오류(None) 행이 실측 저장 차단 금지
                    seen.add(k)
        with fp.open("a", encoding="utf-8") as f:  # ponytail: append only, never rewrite history
            for op, cl, b in sorted(rows, key=lambda x: x[0]):
                o = op.strftime("%Y-%m-%dT%H:%M:%SZ")
                if o in seen:  # 중복금지
                    continue
                cc, vv = b.get("c"), b.get("v")
                try:
                    notional = float(cc) * int(vv) if cc is not None and vv is not None else None
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
    ap = A.ArgumentParser(description="Collect SPY 5min bars (Yahoo public chart, no key) into data/SPY/")
    ap.add_argument("--days", type=int, default=20, help="trading-day backfill window (default 20)")
    ap.add_argument("--loop", action="store_true", help="repeat every 5min during ET open")
    a = ap.parse_args()
    rng = "1mo" if a.days > 5 else "5d"
    if not a.loop:
        bars, st = fetch_bars(rng)
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"once days~{a.days} range={rng} status={st} saved={n}")
        return
    while True:
        u, et = et_now()
        if _is_closed(et):
            print(f"closed {et:%Y-%m-%d %H:%M} ET skip")
            T.sleep(300)
            continue
        bars, st = fetch_bars("5d")
        n = save_bars(bars, st if st != "ok" else "ok")
        print(f"loop {et:%H:%M} ET status={st} saved={n}")
        T.sleep(300)


if __name__ == "__main__":
    main()
