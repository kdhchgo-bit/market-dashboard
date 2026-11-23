"""USO 5분봉 수집기 — Yahoo 무키 수신.
원천: https://query1.finance.yahoo.com/v8/finance/chart/USO?range=1mo&interval=5m
키 없이 동작. 정규장 완성봉만 저장. VWAP 미제공이라 null.
거래대금 근삿값은 notional(c*v) 별도 기록.
사용법: python 02_USO.py [--loop]
저장: data/USO/YYYY-MM-DD.jsonl (날짜=ET 기준 bar 날짜, ponytail=append-only, 이력 보존)
loop: --loop면 5분 간격. ET 월-금 09:30-16:00만 수집.
에러시 임의가격 금지, status(rate_limited|http_error|delayed|closed) 기록.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "USO"
URL = ("https://query1.finance.yahoo.com/v8/finance/chart/"
       "USO?range=1mo&interval=5m")
SRC = "yahoo"
ET = ZoneInfo("America/New_York")
BAR = timedelta(minutes=5)
FMT = "%Y-%m-%dT%H:%M:%SZ"
UA = {"User-Agent": "Mozilla/5.0"}


def et_now():
    return datetime.now(ET)


def is_open(dt_utc):
    e = dt_utc.astimezone(ET)
    if e.weekday() >= 5:
        return False
    return (9, 30) <= (e.hour, e.minute) < (16, 0)


def fetch():
    r = None
    for attempt in (0, 1):  # 429 1회 재시도
        try:
            r = requests.get(URL, headers=UA, timeout=30)
        except requests.RequestException:
            return None, "http_error"
        if r.status_code == 429 and attempt == 0:
            time.sleep(5)
            continue
        break
    if r.status_code == 429:
        return None, "rate_limited"
    if r.status_code != 200:
        return None, "http_error"
    try:
        res = (r.json()["chart"]["result"] or [])[0]
    except (ValueError, KeyError, IndexError, TypeError):
        return None, "http_error"
    ts = res.get("timestamp") or []
    try:
        q = (res.get("indicators") or {})["quote"][0]
    except (KeyError, IndexError, TypeError):
        return None, "http_error"
    return (ts, q), None


def existing_ok(base):
    opens, latest = set(), None
    for fp in base.glob("*.jsonl"):
        try:
            lines = fp.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            if d.get("status") != "ok":
                continue  # 오류행이 실측 저장 차단 금지
            t = d.get("bar_open_utc")
            if t:
                opens.add(t)
                if latest is None or t > latest:
                    latest = t
    return opens, latest


def err_rec(cut, now_s, status):
    return {"source": SRC, "symbol": SYMBOL,
            "bar_open_utc": cut.strftime(FMT),
            "bar_close_utc": (cut + BAR).strftime(FMT),
            "received_at_utc": now_s, "o": None, "h": None,
            "l": None, "c": None, "v": None, "n": None,
            "vwap": None, "notional": None, "status": status}


def collect_once(base):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_s = now.strftime(FMT)
    cut = now.replace(second=0, microsecond=0)
    cut -= timedelta(minutes=cut.minute % 5)
    day = et_now().strftime("%Y-%m-%d")
    got, err = fetch()
    if err:
        fp = base / f"{day}.jsonl"
        with open(fp, "a", encoding="utf-8") as f:  # ponytail: append only, never rewrite history
            f.write(json.dumps(err_rec(cut, now_s, err)) + "\n")
        print(f"{err} recorded", flush=True)
        return 0
    ts, q = got
    opens, _ = existing_ok(base)
    grouped, n = {}, 0
    for i, t in enumerate(ts):
        try:
            o = datetime.fromtimestamp(t, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
        if o + BAR > now:  # 완성봉만
            continue
        if not is_open(o):  # 정규장만
            continue
        k = o.strftime(FMT)
        if k in opens:
            continue
        try:
            oo, hh, ll = q["open"][i], q["high"][i], q["low"][i]
            cc, vv = q["close"][i], q["volume"][i]
        except (KeyError, IndexError, TypeError):
            continue
        if oo is None or hh is None or ll is None or cc is None:
            continue
        notional = round(cc * vv, 2) if vv else None
        rec = {"source": SRC, "symbol": SYMBOL,
               "bar_open_utc": k,
               "bar_close_utc": (o + BAR).strftime(FMT),
               "received_at_utc": now_s, "o": oo, "h": hh,
               "l": ll, "c": cc, "v": vv, "n": None,
               "vwap": None, "notional": notional,
               "status": "ok"}
        grouped.setdefault(o.astimezone(ET).strftime("%Y-%m-%d"),
                           []).append(rec)
        opens.add(k)
        n += 1
    if not n:
        print("delayed: no new completed bars", flush=True)
        return 0
    for dk, recs in grouped.items():
        with open(base / f"{dk}.jsonl", "a", encoding="utf-8") as f:  # ponytail: append only
            for rec in recs:
                f.write(json.dumps(rec) + "\n")
    print(f"saved {n} bars", flush=True)
    return n


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Collect USO 5min bars (Yahoo public chart, no key) into data/USO/")
    ap.add_argument("--loop", action="store_true", help="repeat every 5min during ET open")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    base = Path(__file__).parent / "data" / SYMBOL
    base.mkdir(parents=True, exist_ok=True)
    if not a.loop:
        collect_once(base)
        return
    while True:
        cur = et_now()
        if not is_open(datetime.now(timezone.utc)):
            print(f"skip closed {cur.strftime('%Y-%m-%d %H:%M %Z')}", flush=True)
            time.sleep(300)
            continue
        collect_once(base)
        time.sleep(300)


if __name__ == "__main__":
    main()
