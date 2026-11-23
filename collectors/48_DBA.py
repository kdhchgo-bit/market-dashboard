"""Usage: python 48_DBA.py [--loop] [--days N]
Symbol: DBA (농산물)
Source: https://query1.finance.yahoo.com/v8/finance/chart/DBA?range=5d&interval=5m (fallback query2)
Save: data/DBA/YYYY-MM-DD.jsonl (bar-start UTC date) e.g. {"source":"yahoo","symbol":"DBA","bar_open_utc":"2026-11-11T14:30:00Z","status":"ok"}
Loop: --loop repeats every 5min, ET Mon-Fri 09:30-16:00 else skip-log+wait; single run = 20-trading-day backfill then incremental; drops in-progress bar.
Notes: keyless Yahoo chart 5m regular-session completed bars only; VWAP=null (source has none); notional_est=typical*x volume approx; keeps existing fields.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SYM = "DBA"
SRC = "yahoo"
OUT = Path(__file__).parent / "data" / "DBA"
BAR = timedelta(minutes=5)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) yahoo-collector"}
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")


def et_open(now):
    y = now.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(y, 3, s2, 7, tzinfo=timezone.utc) <= now < datetime(y, 11, s1, 6, tzinfo=timezone.utc)
    e = now + timedelta(hours=-4 if dst else -5)
    if e.weekday() >= 5:
        return False
    return (e.hour, e.minute) >= (9, 30) and (e.hour, e.minute) < (16, 0)


def _to_et(dt):
    y = dt.year
    m1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    s2 = 1 + (6 - m1.weekday()) % 7 + 7
    n1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    s1 = 1 + (6 - n1.weekday()) % 7
    dst = datetime(y, 3, s2, 7, tzinfo=timezone.utc) <= dt < datetime(y, 11, s1, 6, tzinfo=timezone.utc)
    return dt + timedelta(hours=-4 if dst else -5)


def fetch(period1=None, period2=None):
    params: dict
    if period1 is not None and period2 is not None:
        params = {"period1": int(period1), "period2": int(period2), "interval": "5m", "includePrePost": "false"}
    else:
        params = {"range": "1mo", "interval": "5m", "includePrePost": "false"}
    last_err = "http_error"
    for host in HOSTS:
        try:
            r = requests.get(host + "/v8/finance/chart/" + SYM, headers=UA, params=params, timeout=20)
        except Exception:
            last_err = "delayed"
            continue
        if r.status_code == 429:
            return None, "rate_limited"
        if r.status_code != 200:
            last_err = "http_error"
            continue
        try:
            j = r.json()
        except Exception:
            last_err = "http_error"
            continue
        try:
            chart = j.get("chart") or {}
            if chart.get("error"):
                last_err = "http_error"
                continue
            res = (chart.get("result") or [None])[0]
            if not res:
                last_err = "delayed"
                continue
            ts = res.get("timestamp") or []
            quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
            if not ts:
                last_err = "delayed"
                continue
            return (ts, quote), None
        except Exception:
            last_err = "http_error"
            continue
    return None, last_err


def append(recs):
    # ponytail: append-only, history rewrite 없음 ("a" 모드 + bar_open_utc 중복제외).
    w, seen = 0, {}
    for r in recs:
        d = r["bar_open_utc"][:10]
        if d not in seen:
            p = OUT / (d + ".jsonl")
            ok_set, all_set = set(), set()
            if p.exists():
                for ln in p.read_text(encoding="utf-8").splitlines():
                    try:
                        o = json.loads(ln)
                    except Exception:
                        continue
                    t = o.get("bar_open_utc")
                    if t is None:
                        continue
                    all_set.add(t)
                    if o.get("status") == "ok":
                        ok_set.add(t)
            seen[d] = (p, ok_set, all_set)
        p, ok_set, all_set = seen[d]
        is_ok = r.get("status") == "ok"
        if is_ok:
            if r["bar_open_utc"] in ok_set:
                continue
        else:
            if r["bar_open_utc"] in all_set:
                continue
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
        ok_set.add(r["bar_open_utc"]) if is_ok else None
        all_set.add(r["bar_open_utc"])
        w += 1
    return w


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Collect DBA 5Min bars (yahoo).")
    p.add_argument("--loop", action="store_true", help="repeat every 5min during ET Mon-Fri 09:30-16:00")
    p.add_argument("--days", type=int, default=20, help="trading-day backfill window (default: 20)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    loop = args.loop
    days = args.days
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        f = "%Y-%m-%dT%H:%M:%SZ"
        cut = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
        base = {"source": SRC, "symbol": SYM, "bar_open_utc": cut.strftime(f), "bar_close_utc": (cut + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": None, "h": None, "l": None, "c": None, "v": None, "n": None, "vwap": None, "notional_est": None}
        if not et_open(now):
            if loop:
                print(f"skip closed {now.strftime(f)}")
            else:
                append([dict(base, status="closed")])
                print("closed")
        else:
            mx = None
            for p in OUT.glob("*.jsonl"):
                try:
                    for ln in p.read_text(encoding="utf-8").splitlines():
                        try:
                            o = json.loads(ln)
                        except Exception:
                            continue
                        if o.get("status") != "ok":
                            continue
                        t = o.get("bar_open_utc")
                        if t and (mx is None or t > mx):
                            mx = t
                except Exception:
                    pass
            if mx:
                try:
                    mx_dt = datetime.strptime(mx, f).replace(tzinfo=timezone.utc)
                    p1 = (mx_dt + BAR).timestamp()
                    p2 = now.timestamp()
                    raw, err = fetch(period1=p1, period2=p2)
                except Exception:
                    raw, err = None, "http_error"
            else:
                raw, err = fetch()
                # fallback to 5d range if 1mo empty (handled inside as delayed); try explicit 5d once
                if err == "delayed" or (raw is not None and not (raw[0] or [])):
                    try:
                        last_err = err
                        raw2, err2 = None, err
                        for host in HOSTS:
                            try:
                                rr = requests.get(host + "/v8/finance/chart/" + SYM, headers=UA, params={"range": "5d", "interval": "5m", "includePrePost": "false"}, timeout=20)
                            except Exception:
                                continue
                            if rr.status_code != 200:
                                continue
                            try:
                                jj = rr.json()
                                rs = (jj.get("chart", {}).get("result") or [None])[0]
                                if rs and rs.get("timestamp"):
                                    raw2 = (rs["timestamp"], (rs.get("indicators", {}).get("quote") or [{}])[0])
                                    err2 = None
                                    break
                            except Exception:
                                continue
                        if raw2 is not None:
                            raw, err = raw2, err2
                    except Exception:
                        pass
            if err:
                append([dict(base, status=err)])
                print(err)
            else:
                try:
                    ts_list, quote = raw  # type: ignore
                except Exception:
                    ts_list, quote = [], {}
                opens = quote.get("open") or []
                highs = quote.get("high") or []
                lows = quote.get("low") or []
                closes = quote.get("close") or []
                vols = quote.get("volume") or []
                recs = []
                for i, epoch in enumerate(ts_list):
                    try:
                        o_dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
                    except Exception:
                        continue
                    # 완성봉만: 5분 경계 + 이미 닫힌 봉
                    if o_dt.second != 0 or o_dt.minute % 5 != 0:
                        continue
                    if o_dt >= cut:
                        continue
                    # 정규장만 (ET 09:30-16:00, 평일)
                    e = _to_et(o_dt)
                    if e.weekday() >= 5:
                        continue
                    if (e.hour, e.minute) < (9, 30) or (e.hour, e.minute) >= (16, 0):
                        continue
                    try:
                        o = opens[i] if i < len(opens) else None
                        h = highs[i] if i < len(highs) else None
                        lo = lows[i] if i < len(lows) else None
                        c = closes[i] if i < len(closes) else None
                        v = vols[i] if i < len(vols) else None
                    except Exception:
                        continue
                    if c is None:
                        continue
                    try:
                        v_int = int(v) if v is not None else None
                    except Exception:
                        v_int = None
                    try:
                        if c is not None and v_int is not None:
                            typ = (h + lo + c) / 3.0 if (h is not None and lo is not None) else c
                            notional = round(float(typ) * float(v_int), 2)
                        else:
                            notional = None
                    except Exception:
                        notional = None
                    try:
                        recs.append({"source": SRC, "symbol": SYM, "bar_open_utc": o_dt.strftime(f), "bar_close_utc": (o_dt + BAR).strftime(f), "received_at_utc": now.strftime(f), "o": o, "h": h, "l": lo, "c": c, "v": v_int, "n": None, "vwap": None, "notional_est": notional, "status": "ok"})
                    except Exception:
                        continue
                if recs:
                    print(f"ok +{append(recs)}")
                else:
                    append([dict(base, status="delayed")])
                    print("delayed")
        if not loop:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
