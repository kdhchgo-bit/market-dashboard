"""US Treasury 2Y/10Y yields collector (관측일 보존, 하루 1회).

원천: 미재무부 공식 XML (키 불필요)
  월별 공식: https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=YYYYMM
  당월 우선, 당월에 today 이하 관측이 없으면(월초·휴일) 전월도 조회해 병합.
  대체: 레거시 https://home.treasury.gov/sites/default/files/interest-rates/yield.xml
  최후 대체: 피드 페이지 https://home.treasury.gov/treasury-daily-interest-rate-xml-feed
  파싱은 xml.etree. 신규(Atom/OData)와 구(G_NEW_DATE) 포맷 모두 지원.

사용법:
  python 26_US_YIELDS.py            # 1회 수집 (최근관측일 기준 obs_date)
  python 26_US_YIELDS.py --loop     # 24시간마다 반복 수집

저장:
  Path(__file__).parent/"data"/"US_YIELDS"/YYYY-MM-DD.jsonl  (YYYY-MM-DD = 원래 관측일)
  (append-only / ponytail, history kept)
레코드:
  {"source":"us-treasury","tenor":"2Y|10Y","rate":float|None,
   "obs_date":"YYYY-MM-DD","received_at_utc":...,"status":"ok|missing|http_error"}
규칙: 주말/휴일 결측은 status=missing, rate=None (가짜값 금지).
  Dedup key: (obs_date, tenor) — 기존 파일 내용 유지하고 추가하지 않음.
의존성: 표준lib + requests only.
"""
import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

XML_URL = "https://home.treasury.gov/sites/default/files/interest-rates/yield.xml"
PAGE_URL = "https://home.treasury.gov/treasury-daily-interest-rate-xml-feed"
OFFICIAL_BASE = ("https://home.treasury.gov/resource-center/data-chart-center/"
                 "interest-rates/pages/xml")
OFFICIAL_DATA = "daily_treasury_yield_curve"
UA = {"User-Agent": "Mozilla/5.0 (collector; daily-yields)"}
OUT_DIR = Path(__file__).resolve().parent / "data" / "US_YIELDS"


def fetch_text(url, timeout=30):
    r = requests_get(url, timeout)
    r.raise_for_status()
    return r.text


def requests_get(url, timeout):
    import requests
    return requests.get(url, headers=UA, timeout=timeout)


def to_float(s):
    try:
        s = (s or "").strip()
        return float(s) if s else None
    except ValueError:
        return None


def parse_yield_xml(text):
    """Return {obs_date YYYY-MM-DD: (rate_2y|None, rate_10y|None)}."""
    root = ET.fromstring(text)
    out = {}
    for node in root.iter("G_NEW_DATE"):
        raw = (node.findtext("NEW_DATE") or "").strip()  # MM-DD-YYYY
        m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", raw)
        if not m:
            continue
        mm, dd, yyyy = (int(m.group(1)), int(m.group(2)), m.group(3))
        obs = f"{yyyy}-{mm:02d}-{dd:02d}"
        cat = node.find(".//G_BC_CAT")
        if cat is None:
            continue
        r2 = to_float(cat.findtext("BC_2YEAR"))
        r10 = to_float(cat.findtext("BC_10YEAR"))
        out[obs] = (r2, r10)
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag.split(":")[-1]


def parse_official_xml(text):
    """Parse Treasury official Atom/OData monthly XML.

    Return {obs_date YYYY-MM-DD: (rate_2y|None, rate_10y|None)}.
    """
    root = ET.fromstring(text)
    out = {}
    for entry in root.iter():
        if _local(entry.tag) != "entry":
            continue
        obs = None
        r2 = None
        r10 = None
        for el in entry.iter():
            name = _local(el.tag)
            if name == "NEW_DATE":
                raw = (el.text or "").strip()  # 2026-11-10T00:00:00
                m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
                if m:
                    obs = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            elif name == "BC_2YEAR":
                r2 = to_float(el.text)
            elif name == "BC_10YEAR":
                r10 = to_float(el.text)
        if obs:
            out[obs] = (r2, r10)
    return out


def official_month_url(yyyymm):
    return (f"{OFFICIAL_BASE}?data={OFFICIAL_DATA}"
            f"&field_tdr_date_value_month={yyyymm}")


def fetch_official_month(yyyymm, timeout=30):
    return parse_official_xml(fetch_text(official_month_url(yyyymm), timeout))


def month_keys(today_iso):
    """Return (cur_yyyymm, prev_yyyymm) for today YYYY-MM-DD."""
    y, m = int(today_iso[:4]), int(today_iso[5:7])
    cur = f"{y:04d}{m:02d}"
    if m == 1:
        prev = f"{y - 1:04d}12"
    else:
        prev = f"{y:04d}{m - 1:02d}"
    return cur, prev


def fallback_xml_text(timeout=30):
    html = fetch_text(PAGE_URL, timeout)
    m = re.search(r'https?://[^\s"\']*?yield\.xml', html)
    if not m:
        raise ValueError("fallback yield.xml URL not found on feed page")
    return fetch_text(m.group(0), timeout)


def load_xml_map(timeout=30, today_iso=None):
    """Load merged obs map from official cur+prev month, then legacy.

    Returns (data|None, err|None). Data covers 당월+전월 so 월초·휴일에도
    최근관측일을 찾을 수 있다.
    """
    if today_iso is None:
        today_iso = datetime.now(timezone.utc).date().isoformat()
    cur_mm, prev_mm = month_keys(today_iso)
    merged = {}
    errs = []
    # 1) 공식 월별: 당월
    try:
        merged.update(fetch_official_month(cur_mm, timeout))
    except Exception as e:
        errs.append(f"official {cur_mm}: {e}")
    # 2) 공식 월별: 전월 (월초·휴일 대비 항상 탐색)
    try:
        merged.update(fetch_official_month(prev_mm, timeout))
    except Exception as e:
        errs.append(f"official {prev_mm}: {e}")
    if merged:
        return merged, (None if not errs else "; ".join(errs))
    # 3) 레거시 yield.xml (당월만)
    try:
        return parse_yield_xml(fetch_text(XML_URL, timeout)), None
    except Exception as e1:
        try:
            return parse_yield_xml(fallback_xml_text(timeout)), None
        except Exception as e2:
            all_err = "; ".join(errs + [f"legacy: {e1} | fallback: {e2}"])
            return None, all_err


def save_records(obs_date, items):
    """items: [(tenor, rate, status)]. Dedupe on obs_date+tenor. Returns (path, added)."""
    # ponytail: append only, never rewrite history
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{obs_date}.jsonl"
    seen = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                seen.add((d.get("obs_date"), d.get("tenor")))
            except ValueError:
                continue
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    with open(path, "a", encoding="utf-8") as f:
        for tenor, rate, status in items:
            if (obs_date, tenor) in seen:
                continue
            rec = {"source": "us-treasury", "tenor": tenor, "rate": rate,
                   "obs_date": obs_date, "received_at_utc": now, "status": status}
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
            seen.add((obs_date, tenor))
            added += 1
    return path, added


def collect_once(timeout=30):
    today = datetime.now(timezone.utc).date().isoformat()
    data, err = load_xml_map(timeout, today)
    if data is None:
        path, n = save_records(today, [("2Y", None, "http_error"), ("10Y", None, "http_error")])
        print(f"[http_error] {today} -> {path} (+{n}) err={err}")
        return path
    # 최근관측일: today 이하 중 최대일. 휴일·주말·월초면 전일/전월일로 자동 후퇴.
    cands = sorted(d for d in data if d <= today)
    if not cands:
        path, n = save_records(today, [("2Y", None, "missing"), ("10Y", None, "missing")])
        print(f"[missing] {today} (no obs <= today in XML) -> {path} (+{n})")
        return path
    obs = cands[-1]
    r2, r10 = data[obs]
    s2 = "ok" if r2 is not None else "missing"
    s10 = "ok" if r10 is not None else "missing"
    path, n = save_records(obs, [("2Y", r2, s2), ("10Y", r10, s10)])
    tag = "today" if obs == today else "latest"
    print(f"[{tag}] obs={obs} (today={today}) 2Y={r2} 10Y={r10} -> {path} (+{n})")
    return path


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Collect US Treasury 2Y/10Y yields (official XML, no key) into data/US_YIELDS/")
    ap.add_argument("--loop", action="store_true", help="24시간마다 반복")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    while True:
        try:
            collect_once()
        except Exception as e:
            print(f"[error] {e}")
        if not a.loop:
            break
        time.sleep(24 * 3600)


if __name__ == "__main__":
    main()
