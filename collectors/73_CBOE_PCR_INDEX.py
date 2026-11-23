"""CBOE 지수 Put/Call Ratio(딜레이) 수집기 (키 불필요).

원천(primary, CBOE 공식): https://cdn.cboe.com/api/global/delayed_quotes/quotes/_PCR.json
폴백(fallback, CBOE 공식 페이지): https://www.cboe.com/markets/us/options/market-statistics/daily/
  일별 TOTAL/INDEX/EQUITY PUT/CALL RATIO 정규식 파싱. 모두 실패 -> status=http_error.
지연 데이터이므로 모든 레코드에 is_delayed=true.

Usage: python 73_CBOE_PCR_INDEX.py [--loop] [--interval 3600] [--duration 24]
  1회: 즉시 1회 수집 | --loop: --interval초 간격으로 --duration시간 반복(기본 1시간 간격 24시간).
저장: Path(__file__).parent/"data"/"PCR_INDEX"/YYYY-MM-DD.jsonl (일자=UTC 수신일).
레코드: {source,symbol,metric,obs_date,value,received_at_utc,is_delayed,status}
  metric=index_pcr|equity_pcr|total_pcr, status=ok|http_error. 중복금지 키=(metric,obs_date).
의존성: stdlib + requests.
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SOURCE = "CBOE"
SYMBOL = "PCR_INDEX"
PRIMARY_JSON = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_PCR.json"
FALLBACK_PAGE = "https://www.cboe.com/markets/us/options/market-statistics/daily/"
OUT_DIR = Path(__file__).resolve().parent / "data" / "PCR_INDEX"
UA = {"User-Agent": "Mozilla/5.0 (pcr-index-collector; delayed)"}


def utcnow():
    return datetime.now(timezone.utc)


def iso_z(dt=None):
    return (dt or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_path(now=None):
    return OUT_DIR / f"{(now or utcnow()).strftime('%Y-%m-%d')}.jsonl"


def load_keys(path):
    keys = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                keys.add((d.get("metric"), d.get("obs_date")))
            except (ValueError, AttributeError):
                continue
    return keys


def fetch_primary():
    """CDN delayed-quote JSON에서 PCR 값 추출. 실패 시 예외."""
    r = requests.get(PRIMARY_JSON, headers=UA, timeout=30)
    r.raise_for_status()
    j = r.json()
    d = j.get("data", j) if isinstance(j, dict) else {}
    ts = str(j.get("timestamp", "") or d.get("last_trade_time", ""))
    m = re.search(r"(\d{4}-\d{2}-\d{2})", ts)
    obs = m.group(1) if m else utcnow().strftime("%Y-%m-%d")
    val = d.get("current_price", d.get("close", d.get("price")))
    if val is None:
        raise ValueError("no pcr value in json")
    return [("index_pcr", obs, float(val))]


def parse_page(text):
    """Daily 페이지에서 TOTAL/INDEX/EQUITY PUT/CALL RATIO 파싱."""
    out = []
    pat = re.compile(
        r"(TOTAL|INDEX|EQUITY)[^0-9A-Z]{0,30}PUT\s*/\s*CALL[^0-9]{0,30}"
        r"([0-9]+\.[0-9]+)", re.IGNORECASE)
    day = re.search(r"(20\d{2}[-/]\d{2}[-/]\d{2})", text)
    obs = (day.group(1).replace("/", "-") if day
           else utcnow().strftime("%Y-%m-%d"))
    for kind, val in pat.findall(text):
        metric = f"{kind.lower()}_pcr"
        try:
            out.append((metric, obs, float(val)))
        except ValueError:
            continue
    if not out:  # 숫자 단독 표기 폴백
        m = re.search(r"Put/Call Ratio[^0-9]*([0-9]+\.[0-9]+)", text, re.I)
        if m:
            out.append(("index_pcr", obs, float(m.group(1))))
    if not out:
        raise ValueError("no pcr ratio in page")
    return out


def fetch_fallback():
    r = requests.get(FALLBACK_PAGE, headers=UA, timeout=30)
    r.raise_for_status()
    return parse_page(r.text)


def collect_once():
    now = utcnow()
    rx = iso_z(now)
    path = today_path(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = load_keys(path)
    try:
        rows = fetch_primary()
        src = SOURCE
    except Exception:
        try:
            rows = fetch_fallback()
            src = SOURCE
        except Exception as e:
            print(f"http_error: {type(e).__name__}")
            rows, src = [], SOURCE
    recs = []
    if rows:
        for metric, obs, val in rows:
            if (metric, obs) in keys:
                continue
            keys.add((metric, obs))
            recs.append({"source": src, "symbol": SYMBOL, "metric": metric,
                         "obs_date": obs, "value": val,
                         "received_at_utc": rx, "is_delayed": True,
                         "status": "ok"})
    if not recs and not rows:
        key = ("index_pcr", None)
        if key not in keys:
            recs.append({"source": src, "symbol": SYMBOL,
                         "metric": "index_pcr", "obs_date": None,
                         "value": None, "received_at_utc": rx,
                         "is_delayed": True, "status": "http_error"})
    if recs:
        with open(path, "a", encoding="utf-8") as f:
            for x in recs:
                f.write(json.dumps(x, ensure_ascii=False) + "\n")
    return recs, path


def main():
    ap = argparse.ArgumentParser(description="CBOE index PCR (delayed) collector")
    ap.add_argument("--loop", action="store_true", help="repeat until --duration hours")
    ap.add_argument("--interval", type=float, default=3600, help="loop interval seconds")
    ap.add_argument("--duration", type=float, default=24, help="loop duration hours")
    a = ap.parse_args()
    recs, path = collect_once()
    status = recs[0]["status"] if recs else "ok"
    print(f"done status={status} new={len(recs)} -> {path}")
    if not a.loop:
        return
    t0 = time.time()
    while time.time() - t0 < a.duration * 3600:
        time.sleep(max(a.interval, 60))
        try:
            recs, path = collect_once()
            print(f"loop new={len(recs)} -> {path}", flush=True)
        except Exception as e:
            print(f"loop error: {e}", flush=True)


if __name__ == "__main__":
    main()
