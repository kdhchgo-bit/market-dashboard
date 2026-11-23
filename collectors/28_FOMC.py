"""FOMC 금리결정 일정 수집기 (source: fomc-calendar).

원천: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
동작: 1회 수집, --loop 지정 시 24시간마다 반복. 공식 발표시각이
페이지에 없으면 날짜만 저장(release_time_et=null). 기존 저장값과
발표시각이 달라지면 status=updated로 추가, 동일 키 중복 저장 금지.
저장: Path(__file__).parent/"data"/"FOMC"/YYYY-MM-DD.jsonl (수집일 UTC).
레코드: source/event/release_date/release_time_et/url/received_at_utc/status.
의존성: 표준lib + requests, 파싱은 re + html.parser만 사용.
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
SOURCE = "fomc-calendar"
OUTDIR = Path(__file__).parent / "data" / "FOMC"
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
HDR = {"User-Agent": "Mozilla/5.0 (market-collector)"}


class _T(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []

    def handle_data(self, d):
        d = d.strip()
        if d:
            self.chunks.append(d)


def _text(html):
    p = _T()
    p.feed(html)
    return "\n".join(p.chunks)


def parse(html):
    text = _text(html)
    m = re.search(r"2\s*:\s*00\s*p\.?m\.?|14\s*:\s*00", text, re.I)
    t = "14:00" if (m and "ET" in text.upper()) else None
    years = [(m.start(), m.group(1)) for m in re.finditer(r"\b(20\d{2})\b", text)]
    pat = re.compile(
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+(\d{1,2})\s*[-\u2013\u2014]\s*"
        r"(?:(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+)?(\d{1,2})(?:\s*,\s*(20\d{2}))?")
    out, seen = [], set()
    for mm in pat.finditer(text):
        m1, d1, m2, d2, y = mm.groups()
        if y is None:
            y = next((yy for pos, yy in reversed(years) if pos <= mm.start()), None)
            if y is None:
                y = next((yy for pos, yy in years if pos > mm.start()),
                         str(datetime.now(timezone.utc).year))
        mon = MONTHS[(m2 or m1)]
        try:
            dt = datetime(int(y), mon, int(d2)).date()
        except ValueError:
            continue
        ds = dt.isoformat()
        if ds in seen:
            continue
        seen.add(ds)
        out.append({"source": SOURCE, "event": "FOMC Statement",
                    "release_date": ds, "release_time_et": t, "url": URL})
    return sorted(out, key=lambda r: r["release_date"])


def load_existing():
    known = {}
    if OUTDIR.is_dir():
        for f in OUTDIR.glob("*.jsonl"):
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("source") == SOURCE and r.get("release_date"):
                        known[(r.get("event"), r.get("release_date"))] = \
                            r.get("release_time_et")
            except OSError:
                continue
    return known


def collect():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    fp = OUTDIR / f"{now.date().isoformat()}.jsonl"
    known = load_existing()
    try:
        r = requests.get(URL, headers=HDR, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"http {r.status_code}")
        items = parse(r.text)
    except Exception as e:
        rec = {"source": SOURCE, "event": "FOMC Statement",
               "release_date": now.date().isoformat(),
               "release_time_et": None, "url": URL,
               "received_at_utc": now.isoformat(), "status": "http_error",
               "note": str(e)[:200]}
        with fp.open("a", encoding="utf-8") as f:  # ponytail: append-only, never rewrite history
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"http_error: {e}")
        return
    n_new = 0
    with fp.open("a", encoding="utf-8") as f:  # ponytail: append-only, never rewrite history
        for it in items:
            key = (it["event"], it["release_date"])
            if key not in known:
                st = "scheduled"
            elif known[key] != it["release_time_et"]:
                st = "updated"
            else:
                continue
            known[key] = it["release_time_et"]
            it["received_at_utc"] = datetime.now(timezone.utc).isoformat()
            it["status"] = st
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
            n_new += 1
    print(f"parsed={len(items)} new={n_new} file={fp}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="FOMC 금리결정 일정 수집기")
    ap.add_argument("--loop", action="store_true",
                    help="24시간마다 반복 수집")
    args = ap.parse_args(argv)
    if args.loop:
        while True:
            collect()
            time.sleep(24 * 3600)
    else:
        collect()


if __name__ == "__main__":
    main()
