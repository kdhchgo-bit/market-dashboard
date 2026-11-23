"""BBC World RSS collector (geopolitics headlines).

Usage:
  python 30_BBC_WORLD.py            # one-shot collect
  python 30_BBC_WORLD.py --loop     # every 5 min for 24h (288 runs)

Source: https://feeds.bbci.co.uk/news/world/rss.xml (no key, xml.etree).
Output: <script_dir>/data/BBC_WORLD/YYYY-MM-DD.jsonl
Record: {"source","title","link","published_at","received_at_utc","status"}
Dedup: link/guid never stored twice (memory + existing jsonl scan).
Deps: stdlib + requests only.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

FEED_URL = "https://feeds.bbci.co.uk/news/world/rss.xml"
SOURCE = "bbc-world-rss"
OUT_DIR = Path(__file__).parent / "data" / "BBC_WORLD"
UA = {"User-Agent": "market-collector/1.0 (+research)"}


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_pubdate(s):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def load_seen(path):
    seen = set()
    if path.exists():
        for line in path.open(encoding="utf-8"):
            try:
                r = json.loads(line)
                k = r.get("link") or ""
                if k:
                    seen.add(k)
            except Exception:
                continue
    return seen


def append(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def collect_once(seen_global):
    day_path = OUT_DIR / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
    seen = seen_global | load_seen(day_path)
    now = utcnow_iso()
    try:
        r = requests.get(FEED_URL, headers=UA, timeout=20)
        if r.status_code != 200:
            append(day_path, {"source": SOURCE, "title": None, "link": FEED_URL,
                              "published_at": None, "received_at_utc": now,
                              "status": "http_error", "http_status": r.status_code})
            return 0
        content = r.content
    except Exception as e:
        append(day_path, {"source": SOURCE, "title": None, "link": FEED_URL,
                          "published_at": None, "received_at_utc": now,
                          "status": "http_error", "error": str(e)[:200]})
        return 0
    try:
        root = ET.fromstring(content)
    except Exception as e:
        append(day_path, {"source": SOURCE, "title": None, "link": FEED_URL,
                          "published_at": None, "received_at_utc": now,
                          "status": "parse_error", "error": str(e)[:200]})
        return 0
    n = 0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip() or None
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        key = link or guid
        if not key or key in seen:
            continue
        rec = {"source": SOURCE, "title": title, "link": link or guid,
               "published_at": parse_pubdate(item.findtext("pubDate")),
               "received_at_utc": utcnow_iso(), "status": "ok"}
        # date rollover at midnight UTC
        dp = OUT_DIR / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
        if dp != day_path:
            day_path = dp
            seen |= load_seen(day_path)
            if key in seen:
                continue
        append(day_path, rec)
        seen.add(key)
        seen_global.add(key)
        n += 1
    print(f"[bbc-world] +{n} -> {day_path}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="5min interval x 24h")
    a = ap.parse_args()
    seen = set()
    if not a.loop:
        collect_once(seen)
        return
    end = time.time() + 24 * 3600
    while True:
        try:
            collect_once(seen)
        except Exception as e:
            print(f"[bbc-world] loop error: {e}")
        if time.time() + 300 > end:
            break
        time.sleep(300)


if __name__ == "__main__":
    main()
