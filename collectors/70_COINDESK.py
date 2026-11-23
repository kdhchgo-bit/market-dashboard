"""CoinDesk 크립토 뉴스 수집기 (RSS, 키 불필요).

원천: https://www.coindesk.com/arc/outboundfeeds/rss/
폴백: https://feeds.feedburner.com/CoinDesk
수집: 제목·시각·URL. link 기준 중복 제거.
저장: data/COINDESK/YYYY-MM-DD.jsonl (레코드: source/title/link/published_at/received_at_utc/status)
실행: 1회 `python 70_COINDESK.py`, 반복 `python 70_COINDESK.py --loop` (5분 간격 24시간).
표준lib + requests만 사용.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

SOURCE = "COINDESK"
PRIMARY = "https://www.coindesk.com/arc/outboundfeeds/rss/"
FALLBACK = "https://feeds.feedburner.com/CoinDesk"
DATA_ROOT = Path(__file__).resolve().parent / "data" / "COINDESK"
UA = {"User-Agent": "Mozilla/5.0 (market-collector)"}
INTERVAL = 300
LIMIT = 24 * 3600  # 24시간


def fetch(url: str) -> bytes:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.content


def fetch_feed() -> bytes:
    try:
        return fetch(PRIMARY)
    except Exception:
        return fetch(FALLBACK)


def parse(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (
            item.findtext("pubDate")
            or item.findtext("{http://www.w3.org/2005/Atom}published")
            or item.findtext("{http://www.w3.org/2005/Atom}updated")
            or ""
        ).strip()
        if link:
            out.append({"title": title, "link": link, "published_at": pub})
    return out


def load_seen() -> set:
    seen = set()
    if DATA_ROOT.exists():
        for f in DATA_ROOT.glob("*.jsonl"):
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    try:
                        d = json.loads(line)
                        if d.get("link"):
                            seen.add(d["link"])
                    except json.JSONDecodeError:
                        continue
            except OSError:
                continue
    return seen


def save(items, seen: set) -> int:
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    path = DATA_ROOT / f"{day}.jsonl"
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for it in items:
            if it["link"] in seen:
                continue
            seen.add(it["link"])
            rec = {
                "source": SOURCE,
                "title": it["title"],
                "link": it["link"],
                "published_at": it["published_at"],
                "received_at_utc": now.isoformat(),
                "status": "ok",
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


def run_once(seen: set) -> int:
    xml_bytes = fetch_feed()
    return save(parse(xml_bytes), seen)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="5분 간격 24시간 반복")
    a = ap.parse_args()
    seen = load_seen()
    if not a.loop:
        print(f"saved={run_once(seen)}")
        return
    end = time.time() + LIMIT
    while True:
        try:
            print(f"saved={run_once(seen)}")
        except Exception as e:
            print(f"error: {e}")
        if time.time() + INTERVAL > end:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
