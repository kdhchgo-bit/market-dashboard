"""CBOE 개별주(Equity) Put/Call Ratio 수집 (딜레이, 키 불필요).
Usage: python 72_CBOE_PCR_EQUITY.py [--loop] [--interval 86400]
  1회: 공식 CBOE 일간통계에서 EQUITY P/C 조회 후 저장 | --loop: interval초마다 반복(기본 24시간)
  원천(primary): https://www.cboe.com/markets/us/options/market-statistics/daily/
  폴백: https://www.cboe.com/us/options/market_statistics/daily/ + EQUITY volume으로 직접 계산(put/call)
  out: data/PCR_EQUITY/YYYY-MM-DD.jsonl (일자=수신 UTC 기준)
  record: {source,ratio_type,date,ratio,received_at_utc,is_delayed,status}
  docs: https://www.cboe.com/markets/us/options/market-statistics/daily/
"""
import argparse as A, json as J, re as E, time as T
from datetime import datetime as D, timezone as Z
from pathlib import Path as P
import requests as R

RT, SRC = "EQUITY", "CBOE"
PRI = "https://www.cboe.com/markets/us/options/market-statistics/daily/"
FB = "https://www.cboe.com/us/options/market_statistics/daily/"
UA = {"User-Agent": "Mozilla/5.0 (collector; cboe-pcr-equity)"}
OUT = P(__file__).parent / "data" / "PCR_EQUITY"


def get(url, timeout=20):
    r = R.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text


def fnum(s):
    try:
        return float((s or "").replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def find_ratio(html):
    m = E.search(r"EQUITY\s+PUT/CALL\s+RATIO[^0-9]*([0-9]+\.[0-9]+)", html, E.I | E.S)
    if m:
        v = fnum(m.group(1))
        if v is not None:
            return v
    m = E.search(r"EQUITY[^<]{0,200}?([0-9]+\.[0-9]+)\s*</", html, E.I)
    return fnum(m.group(1)) if m else None


def derived_ratio(html):
    m = E.search(r"EQUITY\s+OPTIONS.*?VOLUME.*?([0-9,]+).*?([0-9,]+).*?([0-9,]+)", html, E.I | E.S)
    if not m:
        m = E.search(r"EQUITY.*?([0-9,]{4,})\s*<\s*[^>]*>\s*([0-9,]{4,})", html, E.I | E.S)
    if not m:
        return None
    nums = [fnum(g) for g in m.groups()]
    if len(nums) >= 2 and nums[0] and nums[1] and nums[0] > 0:
        return round(nums[1] / nums[0], 4)
    return None


def find_date(html):
    pats = [r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
            r"20\d{2}-\d{2}-\d{2}", r"\d{1,2}/\d{1,2}/20\d{2}"]
    for p in pats:
        m = E.search(p, html)
        if not m:
            continue
        s = m.group(0)
        for f in ("%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                return D.strptime(s, f).strftime("%Y-%m-%d")
            except ValueError:
                pass
    return D.now(Z.utc).strftime("%Y-%m-%d")


def fetch(timeout=20):
    for url, tag in ((PRI, "ok"), (FB, "fallback")):
        try:
            h = get(url, timeout=timeout)
            v = find_ratio(h)
            if v is not None:
                return v, find_date(h), SRC if tag == "ok" else SRC + "-fallback", tag
            v = derived_ratio(h)
            if v is not None:
                return v, find_date(h), SRC + "-volume-derived", "fallback"
        except (R.RequestException, ValueError):
            continue
    return None, D.now(Z.utc).strftime("%Y-%m-%d"), SRC, "error"


def save(ratio, date, source, status):
    now = D.now(Z.utc)
    recv = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / f"{now.strftime('%Y-%m-%d')}.jsonl"
    seen = set()
    if fp.exists():
        for ln in fp.read_text(encoding="utf-8").splitlines():
            try:
                d = J.loads(ln)
                seen.add((d.get("source"), d.get("date"), d.get("ratio_type")))
            except ValueError:
                pass
    key = (source, date, RT)
    if key in seen:
        return fp, False
    rec = {"source": source, "ratio_type": RT, "date": date, "ratio": ratio,
           "received_at_utc": recv, "is_delayed": True, "status": status}
    with fp.open("a", encoding="utf-8") as f:
        f.write(J.dumps(rec) + "\n")
    return fp, True


def once(timeout=20):
    v, dt, src, st = fetch(timeout)
    if v is None:
        fp, saved = save(None, dt, src, "error")
        print(f"error date={dt} file={fp} saved={saved}")
        return False
    fp, saved = save(v, dt, src, st)
    print(f"status={st} date={dt} ratio={v} source={src} file={fp} saved={saved}")
    return True


def main():
    ap = A.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=float, default=86400)
    ap.add_argument("--timeout", type=float, default=20)
    a = ap.parse_args()
    if not a.loop:
        once(a.timeout)
        return
    while True:
        try:
            once(a.timeout)
        except (R.RequestException, OSError, ValueError) as e:
            print(f"loop_error {e}")
        T.sleep(a.interval)


if __name__ == "__main__":
    main()
