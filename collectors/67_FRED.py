"""FRED macro collector (FEDFUNDS/DGS10/CPIAUCSL) - keyless.

Primary: FRED public CSV per series (no key):
  https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}
  Short timeout (10s) + 1 retry per series (this path timed out before).
Fallbacks (only when CSV fails for that series):
  DGS10    -> US Treasury 10Y (yield.xml BC_10YEAR, latest obs kept as-is)
  CPIAUCSL -> BLS public API series CUSR0000SA0 (seasonally adjusted CPI)
  FEDFUNDS -> published monthly-average series only; NEVER put daily EFFR
              in as a monthly value (error record instead).
Frequencies kept as published: FEDFUNDS/CPIAUCSL monthly, DGS10 daily.

Usage:
  python 67_FRED.py            # one-shot collection
  python 67_FRED.py --loop     # repeat every 24h
Output: data/FRED/YYYY-MM-DD.jsonl (append-only / ponytail, history kept)
Record: {source, series_id, obs_date, value, received_at_utc, status}
Dedup key: (series_id, obs_date). Error rows (obs_date None) never block
later real observations. Stdlib + requests only.
"""
import argparse
import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SOURCE = "FRED"
SERIES = ["FEDFUNDS", "DGS10", "CPIAUCSL"]
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
UA = {"User-Agent": "Mozilla/5.0 (collector; fred-keyless)"}
TIMEOUT = 10  # short: this path timed out in a previous check
RETRIES = 1   # one retry (2 attempts total)

TREASURY_XML = "https://home.treasury.gov/sites/default/files/interest-rates/yield.xml"
TREASURY_PAGE = "https://home.treasury.gov/treasury-daily-interest-rate-xml-feed"
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

OUT_DIR = Path(__file__).resolve().parent / "data" / "FRED"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def today_path():
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return OUT_DIR / f"{d}.jsonl"


def get(url, timeout=TIMEOUT, **kw):
    """GET with 1 retry. Returns response or raises last exception."""
    kw.setdefault("headers", UA)
    err = None
    for _ in range(RETRIES + 1):
        try:
            r = requests.get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            err = e
    raise err


def parse_fred_csv(text, series_id):
    """Return (obs_date, value) of last non-missing row, or (None, None)."""
    rows = list(csv.DictReader(io.StringIO(text)))
    for row in reversed(rows):
        d = (row.get("observation_date") or "").strip()
        v = (row.get(series_id) or "").strip()
        if not d:
            continue
        if v in ("", "."):
            continue
        try:
            return d, float(v)
        except ValueError:
            continue
    return None, None


def fetch_csv(series_id):
    r = get(CSV_URL.format(series_id=series_id))
    return parse_fred_csv(r.text, series_id)


def fetch_treasury_10y():
    """Fallback for DGS10: latest BC_10YEAR obs from Treasury yield.xml."""
    import re
    import xml.etree.ElementTree as ET

    try:
        text = get(TREASURY_XML).text
    except requests.RequestException:
        html = get(TREASURY_PAGE).text
        m = re.search(r'https?://[^\s"\']*?yield\.xml', html)
        if not m:
            return None, None
        text = get(m.group(0)).text
    root = ET.fromstring(text)
    best = (None, None)
    for node in root.iter("G_NEW_DATE"):
        raw = (node.findtext("NEW_DATE") or "").strip()  # MM-DD-YYYY
        m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", raw)
        if not m:
            continue
        obs = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        cat = node.find(".//G_BC_CAT")
        if cat is None:
            continue
        try:
            v = float((cat.findtext("BC_10YEAR") or "").strip())
        except ValueError:
            continue
        if best[0] is None or obs > best[0]:
            best = (obs, v)
    return best


def fetch_bls_cpi():
    """Fallback for CPIAUCSL: BLS CUSR0000SA0 latest month -> (YYYY-MM-01, value)."""
    now_y = datetime.now(timezone.utc).year
    payload = {"seriesid": ["CUSR0000SA0"],
               "startyear": str(now_y - 2), "endyear": str(now_y)}
    err = None
    for _ in range(RETRIES + 1):
        try:
            r = requests.post(BLS_URL, json=payload, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            items = data.get("Results", {}).get("series", [{}])[0].get("data", [])
            cands = []
            for it in items:
                per = it.get("period", "")  # "M01".."M12" (M13 = annual avg, skip)
                try:
                    v = float(it.get("value"))
                except (TypeError, ValueError):
                    continue
                if len(per) == 3 and per[0] == "M" and per[1:].isdigit():
                    m = int(per[1:])
                    if 1 <= m <= 12:
                        cands.append((f"{it.get('year')}-{m:02d}-01", v))
            if not cands:
                return None, None
            cands.sort()
            return cands[-1]
        except (requests.RequestException, ValueError, KeyError,
                AttributeError, TypeError) as e:
            err = e
    raise err


def load_keys(path):
    keys = set()
    if not path.exists():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            keys.add((r.get("series_id"), r.get("obs_date")))
        except (json.JSONDecodeError, AttributeError, OSError):
            continue
    return keys


def collect_one(series_id):
    """Returns record dict (no I/O). Primary CSV, then per-series fallback."""
    try:
        d, v = fetch_csv(series_id)
        if d and v is not None:
            return {"source": SOURCE, "series_id": series_id, "obs_date": d,
                    "value": v, "received_at_utc": utc_now(), "status": "ok"}
    except requests.RequestException:
        pass
    # --- fallbacks ---
    if series_id == "DGS10":
        try:
            d, v = fetch_treasury_10y()
            if d and v is not None:
                return {"source": "us-treasury", "series_id": series_id,
                        "obs_date": d, "value": v, "received_at_utc": utc_now(),
                        "status": "ok:treasury-fallback"}
        except Exception as e:  # noqa: BLE001 - record cause, keep others running
            return {"source": "us-treasury", "series_id": series_id,
                    "obs_date": None, "value": None,
                    "received_at_utc": utc_now(),
                    "status": f"error: {type(e).__name__}"}
        return {"source": "us-treasury", "series_id": series_id,
                "obs_date": None, "value": None, "received_at_utc": utc_now(),
                "status": "error: csv+treasury failed"}
    if series_id == "CPIAUCSL":
        try:
            d, v = fetch_bls_cpi()
            if d and v is not None:
                return {"source": "BLS", "series_id": series_id,
                        "obs_date": d, "value": v, "received_at_utc": utc_now(),
                        "status": "ok:bls-fallback"}
        except Exception as e:  # noqa: BLE001
            return {"source": "BLS", "series_id": series_id,
                    "obs_date": None, "value": None,
                    "received_at_utc": utc_now(),
                    "status": f"error: {type(e).__name__}"}
        return {"source": "BLS", "series_id": series_id,
                "obs_date": None, "value": None, "received_at_utc": utc_now(),
                "status": "error: csv+bls failed"}
    # FEDFUNDS: monthly-average series only — no daily EFFR substitution.
    return {"source": SOURCE, "series_id": series_id,
            "obs_date": None, "value": None, "received_at_utc": utc_now(),
            "status": "error: csv failed (no EFFR substitution)"}


def collect_once():
    path = today_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = load_keys(path)
    rows = []
    for s in SERIES:
        rec = collect_one(s)
        if (rec["series_id"], rec["obs_date"]) in keys:
            continue
        rows.append(rec)
        keys.add((rec["series_id"], rec["obs_date"]))
    if rows:  # ponytail: append only, never rewrite history
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=float, default=24 * 3600)
    a = ap.parse_args()
    rows = collect_once()
    print(f"[FRED] wrote {len(rows)} records -> {today_path()}")
    for r in rows:
        print(f"  {r['series_id']} {r['obs_date']} {r['value']} [{r['status']}]")
    while a.loop:
        time.sleep(a.interval)
        rows = collect_once()
        print(f"[FRED] wrote {len(rows)} records -> {today_path()}")


if __name__ == "__main__":
    main()
