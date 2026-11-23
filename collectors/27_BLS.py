"""BLS CPI/Employment Situation schedule collector (bls-schedule).
Src: https://www.newyorkfed.org/research/calendars/nationalecon_cal.html (NY Fed, all ET; next-month calendar followed when needed)
Usage: python 27_BLS.py  # 1회수집 | python 27_BLS.py --loop  # 24h 반복
Out: <scriptdir>/data/BLS/YYYY-MM-DD.jsonl (requests+re/html.parser only).
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

MAIN = "https://www.newyorkfed.org/research/calendars/nationalecon_cal.html"
UA = {"User-Agent": "Mozilla/5.0 (market-collector)"}
OUT = Path(__file__).parent / "data" / "BLS"
SOURCE = "nyfed-nationalecon-cal"
MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
TARGETS = ("CPI", "Employment Situation")


class _NYCal(HTMLParser):
    """Parse NY Fed monthly calendar day cells (class somatdR)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_cell = False
        self.cell_buf = ""
        self.cell_links = []
        self.in_span = False
        self.in_a = False
        self.a_href = ""
        self.a_text = ""
        self.cells = []

    def handle_starttag(self, t, a):
        d = dict(a)
        if t == "td" and "somatdR" in d.get("class", ""):
            self.in_cell = True
            self.cell_buf = ""
            self.cell_links = []
        if self.in_cell and t == "span" and "ts-accordion-content" in d.get("class", ""):
            self.in_span = True
        if self.in_cell and self.in_span and t == "a":
            self.in_a = True
            self.a_href = d.get("href", "")
            self.a_text = ""

    def handle_endtag(self, t):
        if t == "a" and self.in_a:
            self.in_a = False
            self.cell_links.append((self.a_text.strip(), self.a_href.strip()))
        if t == "span" and self.in_span:
            self.in_span = False
        if t == "td" and self.in_cell:
            self.in_cell = False
            m = re.match(r"\s*(\d{1,2})\b", self.cell_buf or "")
            if m and self.cell_links:
                times = re.findall(r"\((\d{1,2}:\d{2})\)", self.cell_buf or "")
                items = []
                for i, (nm, hr) in enumerate(self.cell_links):
                    items.append((nm, hr, times[i] if i < len(times) else None))
                self.cells.append((int(m.group(1)), items))

    def handle_data(self, d):
        if self.in_cell and not self.in_a:
            self.cell_buf += d
        if self.in_a:
            self.a_text += d


def parse_nyfed(html, page_url):
    m = re.search(r'<div align="center">\s*(' + MONTHS + r')\s+(\d{4})\s*</div>', html, re.I)
    if not m:
        m = re.search(r"(" + MONTHS + r")\s+(20\d\d)", html, re.I)
    if not m:
        raise ValueError("month title not found")
    mon = MON[m.group(1)[:3].lower()]
    yr = int(m.group(2))
    nx = re.search(r'href="([^"]+)"[^>]*>\s*NEXT MONTH', html, re.I)
    nxt = urljoin(page_url, nx.group(1)) if nx else None
    p = _NYCal()
    p.feed(html)
    out = []
    for day, items in p.cells:
        dt = f"{yr}-{mon:02d}-{day:02d}"
        for nm, hr, tm in items:
            ev = None
            if re.search(r"Consumer Price Index", nm, re.I):
                ev = "CPI"
            elif re.search(r"Employment Situation", nm, re.I):
                ev = "Employment Situation"
            else:
                continue
            tstr = f"{tm} ET" if tm else None
            url = urljoin(page_url, hr) if hr else page_url
            out.append((ev, dt, tstr, url))
    return out, nxt


def get(url):
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def collect():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    cands, errs = [], []
    try:
        html = get(MAIN)
        cur, nxt = parse_nyfed(html, MAIN)
        cands.extend(cur)
    except Exception as e:
        errs.append((MAIN, str(e)[:200]))
        cur, nxt = [], None
    have_future = {ev for (ev, dt, _t, _u) in cands if dt >= today}
    if nxt and any(ev not in have_future for ev in TARGETS):
        try:
            html2 = get(nxt)
            cur2, _nxt2 = parse_nyfed(html2, nxt)
            cands.extend(cur2)
        except Exception as e:
            errs.append((nxt, str(e)[:200]))
    # next upcoming release per event (date >= today)
    best = {}
    for ev, dt, tm, url in cands:
        if ev not in TARGETS or dt < today:
            continue
        key = (dt, tm or "")
        if ev not in best or key < (best[ev][1], best[ev][2] or ""):
            best[ev] = (ev, dt, tm, url)
    uniq = [best[ev] for ev in TARGETS if ev in best]
    return uniq, errs, now


def save(uniq, errs, now):
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / (now.strftime("%Y-%m-%d") + ".jsonl")
    have, hist = set(), {}
    for f in OUT.glob("*.jsonl"):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            have.add((o.get("event"), o.get("release_date"), o.get("release_time_et")))
            hist.setdefault((o.get("event"), o.get("release_date")), set()).add(o.get("release_time_et"))
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    with open(fp, "a", encoding="utf-8") as fh:
        for ev, dt, tm, url in uniq:
            if (ev, dt, tm) in have:
                continue
            st = "updated" if (ev, dt) in hist else "scheduled"
            fh.write(json.dumps({"source": SOURCE, "event": ev, "release_date": dt, "release_time_et": tm,
                                 "url": url, "received_at_utc": ts, "status": st}, ensure_ascii=False) + "\n")
            have.add((ev, dt, tm))
            hist.setdefault((ev, dt), set()).add(tm)
            n += 1
        for url, msg in errs:
            ev = "schedule"
            fh.write(json.dumps({"source": SOURCE, "event": ev, "release_date": now.strftime("%Y-%m-%d"),
                                 "release_time_et": None, "url": url, "received_at_utc": ts,
                                 "status": "http_error"}, ensure_ascii=False) + "\n")
            n += 1
    return n, str(fp)


def main(argv=None):
    ap = argparse.ArgumentParser(description="BLS CPI/Employment Situation schedule collector (bls-schedule)")
    ap.add_argument("--loop", action="store_true", help="24시간마다 반복 수집")
    a = ap.parse_args(argv)
    while True:
        u, e, nw = collect()
        n, fp = save(u, e, nw)
        print(f"[bls] candidates={len(u)} errors={len(e)} appended={n} file={fp}", flush=True)
        if not a.loop:
            break
        time.sleep(24 * 3600)


if __name__ == "__main__":
    main()
