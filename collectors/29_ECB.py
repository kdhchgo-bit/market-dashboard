"""ECB 통화정책회의 금리결정 일정 수집기 (source: ecb-calendar).

원천: https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html
동작: 1회 수집, --loop 지정 시 24시간마다 반복. 금리결정(통화정책회의)
일정만 추출하며 페이지에 발표시각이 없으므로 날짜만 저장
(release_time_cet=null). 기존 저장값과 달라지면 status=updated로 추가,
동일 키 중복 저장 금지.
저장: Path(__file__).parent/"data"/"ECB"/YYYY-MM-DD.jsonl (수집일 UTC).
레코드: source/event/release_date/release_time_cet/url/received_at_utc/status.
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

URL = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
SOURCE = "ecb-calendar"
EVENT = "ECB monetary policy decision"
OUTDIR = Path(__file__).parent / "data" / "ECB"
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
    pat = re.compile(r"(\d{1,2})/(\d{1,2})/(20\d{2})\s*:?\s*([^\n]+)")
    out, seen = [], set()
    for m in pat.finditer(text):
        dd, mm, yy, desc = m.groups()
        low = desc.lower()
        if "non-monetary" in low or "nonmonetary" in low:
            continue
        if "monetary policy meeting" not in low:
            continue
        # 금리결정은 Day 2(기자회견일) 또는 단일일 회의만 채택, Day 1 제외
        if "day 1" in low:
            continue
        try:
            dt = datetime(int(yy), int(mm), int(dd)).date()
        except ValueError:
            continue
        ds = dt.isoformat()
        if ds in seen:
            continue
        seen.add(ds)
        out.append({"source": SOURCE, "event": EVENT,
                    "release_date": ds, "release_time_cet": None,
                    "url": URL})
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
                            r.get("release_time_cet")
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
        rec = {"source": SOURCE, "event": EVENT,
               "release_date": now.date().isoformat(),
               "release_time_cet": None, "url": URL,
               "received_at_utc": now.isoformat(), "status": "http_error",
               "note": str(e)[:200]}
        with fp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"http_error: {e}")
        return
    n_new = 0
    with fp.open("a", encoding="utf-8") as f:
        for it in items:
            key = (it["event"], it["release_date"])
            if key not in known:
                st = "scheduled"
            elif known[key] != it["release_time_cet"]:
                st = "updated"
            else:
                continue
            known[key] = it["release_time_cet"]
            it["received_at_utc"] = datetime.now(timezone.utc).isoformat()
            it["status"] = st
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
            n_new += 1
    print(f"parsed={len(items)} new={n_new} file={fp}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="ECB monetary policy decision schedule collector")
    ap.add_argument("--loop", action="store_true", help="repeat every 24h")
    args = ap.parse_args(argv)
    if args.loop:
        while True:
            collect()
            time.sleep(24 * 3600)
    else:
        collect()


if __name__ == "__main__":
    main()
