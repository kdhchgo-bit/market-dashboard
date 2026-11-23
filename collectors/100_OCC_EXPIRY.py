"""OCC/CBOE option expiry calendar collector (monthly/weekly).

Source priority: CBOE expiration calendar first, OCC site fallback.
Deps: stdlib + requests only (re/html.parser parsing, no API key).
Run once by default; --loop repeats every 24h.
Dedup key: (expiration_date, expiry_type); changed record -> status=updated.
Output: data/OCC_EXPIRY/YYYY-MM-DD.jsonl with
  {source,expiration_date,expiry_type,underlying_group,url,received_at_utc,status}
"""
import argparse
import datetime as dt
import html.parser
import json
import re
import time
from pathlib import Path

import requests

CBOE_URL = "https://www.cboe.com/tradable_products/expiration_calendar/"
OCC_URL = "https://www.theocc.com"
OUT_DIR = Path(__file__).parent / "data" / "OCC_EXPIRY"
HDRS = {"User-Agent": "Mozilla/5.0 market-collector/1.0"}


class _TextParser(html.parser.HTMLParser):
    """Collect stripped text nodes, one per line."""

    def __init__(self):
        super().__init__()
        self.buf = []

    def handle_data(self, d):
        t = d.strip()
        if t:
            self.buf.append(t)

    def text(self):
        return "\n".join(self.buf)


def fetch(url, timeout=25):
    r = requests.get(url, headers=HDRS, timeout=timeout)
    r.raise_for_status()
    return r.text


def _norm_date(m, sep):
    try:
        if sep == "-":
            y, mo, d = map(int, m.split("-"))
        else:
            mo, d, y = map(int, m.split("/"))
            if y < 100:
                y += 2000
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_html(html, source, url):
    p = _TextParser()
    p.feed(html)
    lines = p.text().split("\n")
    recs = {}
    pat = re.compile(r"(\d{4}-\d{2}-\d{2})|(\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))")
    sym = re.compile(r"\b(SPX|VIX|RUT|NDX|XSP|SPY|QQQ|AAPL|TSLA|NVDA)\b", re.I)
    for ln in lines:
        for mt in pat.finditer(ln):
            tok = mt.group(0)
            iso = _norm_date(tok, "-" if "-" in tok else "/")
            if not iso:
                continue
            low = ln.lower()
            if "month" in low:
                et = "monthly"
            elif "week" in low:
                et = "weekly"
            elif "quart" in low:
                et = "quarter"
            else:
                et = "monthly"
            sm = sym.search(ln)
            grp = sm.group(1).upper() if sm else "ALL"
            recs[(iso, et)] = {
                "source": source,
                "expiration_date": iso,
                "expiry_type": et,
                "underlying_group": grp,
                "url": url,
            }
    # fallback: raw html dates with nearby context
    if not recs:
        for mt in pat.finditer(html):
            tok = mt.group(0)
            iso = _norm_date(tok, "-" if "-" in tok else "/")
            if iso and (iso, "monthly") not in recs:
                recs[(iso, "monthly")] = {
                    "source": source,
                    "expiration_date": iso,
                    "expiry_type": "monthly",
                    "underlying_group": "ALL",
                    "url": url,
                }
    return list(recs.values())


def collect():
    for source, url in (("CBOE", CBOE_URL), ("OCC", OCC_URL)):
        try:
            return parse_html(fetch(url), source, url)
        except Exception:
            continue
    return []


def load_existing(path):
    old = {}
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                old[(d.get("expiration_date"), d.get("expiry_type"))] = d
            except ValueError:
                continue
    return old


def run_once():
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{day}.jsonl"
    old = load_existing(path)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    seen = {}
    for r in collect():
        key = (r["expiration_date"], r["expiry_type"])
        if key in seen:
            continue
        prev = old.get(key) or seen.get(key)
        if prev and (
            prev.get("underlying_group") != r["underlying_group"]
            or prev.get("source") != r["source"]
        ):
            r["status"] = "updated"
        else:
            r["status"] = "new" if key not in old else old[key].get("status", "new")
        r["received_at_utc"] = now
        seen[key] = r
    if seen:
        with open(path, "w", encoding="utf-8") as f:
            for k in sorted(seen):
                f.write(json.dumps(seen[k], ensure_ascii=False) + "\n")
    return path, len(seen)


def main():
    ap = argparse.ArgumentParser(description="Option expiry calendar collector (CBOE->OCC).")
    ap.add_argument("--loop", action="store_true", help="repeat every 24h")
    a = ap.parse_args()
    while True:
        p, n = run_once()
        print(f"{p}: {n} records")
        if not a.loop:
            break
        time.sleep(24 * 3600)


if __name__ == "__main__":
    main()
