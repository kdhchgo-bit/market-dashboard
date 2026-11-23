"""CBOE Total Put/Call Ratio daily snapshot (delayed, 15min).

Official-first: cdn.cboe.com delayed quote (_PCR_TOT), then
cboe.com daily market statistics scrape. Fallback: Yahoo/Stooq.
One-shot by default; --loop repeats every 24h. No fake values:
on total failure an error record with nulls is stored.
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PCR_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_PCR_TOT"
DAILY_URL = "https://www.cboe.com/us/options/market_statistics/daily/"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ECPC?interval=1d&range=5d"
STOOQ_URL = "https://stooq.com/q/d/l/?s=%5Ecpc&i=d"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.cboe.com/"}
OUTDIR = Path(__file__).parent / "data" / "PCR_TOTAL"
DELAY_MIN = 15


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def parse_daily(text):
    t = text.replace('\\"', '"').replace("\\/", "/")
    m = re.search(r'"name":"TOTAL PUT/CALL RATIO","value":"([\d.]+)"', t)
    v = re.search(r'"SUM OF ALL PRODUCTS":\[{"name":"VOLUME","call":(\d+),"put":(\d+)', t)
    d = re.search(r'"selectedDate":"(\d{4}-\d{2}-\d{2})"', t)
    if not d:
        return None
    ratio = float(m.group(1)) if m else None
    call = int(v.group(1)) if v else None
    put = int(v.group(2)) if v else None
    if ratio is None and put is not None and call:
        ratio = round(put / call, 4)
    return {"date": d.group(1), "put_vol": put, "call_vol": call, "ratio": ratio}


def fetch_cboe_direct():
    try:
        r = get(PCR_URL).json()
    except (requests.RequestException, ValueError):
        return None
    try:
        d = r.get("data", r) if isinstance(r, dict) else None
        if not isinstance(d, dict):
            return None
        date = str(d.get("date") or d.get("trade_date") or d.get("selectedDate") or "")[:10]
        put = d.get("put_volume", d.get("put"))
        call = d.get("call_volume", d.get("call"))
        ratio = d.get("ratio", d.get("put_call_ratio"))
        put = int(put) if put is not None else None
        call = int(call) if call is not None else None
        ratio = float(ratio) if ratio is not None else None
        if ratio is None and put is not None and call:
            ratio = round(put / call, 4)
        if not date or ratio is None:
            return None
        return {"date": date, "put_vol": put, "call_vol": call, "ratio": ratio}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def fetch_cboe_daily():
    try:
        return parse_daily(get(DAILY_URL).text)
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return None


def fetch_yahoo():
    try:
        j = get(YAHOO_URL).json()
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return None
        q = res[0]
        ts = q.get("timestamp") or []
        closes = (((q.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
        for t, c in reversed(list(zip(ts, closes))):
            if c is not None:
                date = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
                return {"date": date, "put_vol": None, "call_vol": None, "ratio": float(c)}
        return None
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        return None


def fetch_stooq():
    try:
        lines = get(STOOQ_URL, timeout=25).text.strip().splitlines()
        if len(lines) < 2 or not lines[0].lower().startswith("symbol"):
            return None
        last = lines[-1].split(",")
        if len(last) < 7:
            return None
        ratio = float(last[6])
        return {"date": last[1][:10], "put_vol": None, "call_vol": None, "ratio": ratio}
    except (requests.RequestException, ValueError, TypeError, IndexError, AttributeError):
        return None


def collect():
    for name, fn in (("cboe_direct", fetch_cboe_direct), ("cboe_daily", fetch_cboe_daily),
                     ("yahoo", fetch_yahoo), ("stooq", fetch_stooq)):
        try:
            got = fn()
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
            got = None
        if got and got.get("ratio") is not None and got.get("date"):
            return {"source": name, "ratio_type": "TOTAL", "date": got["date"],
                    "put_vol": got.get("put_vol"), "call_vol": got.get("call_vol"),
                    "ratio": got["ratio"], "received_at_utc": utcnow(),
                    "is_delayed": True, "delay_minutes": DELAY_MIN, "status": "ok"}
    return {"source": "none", "ratio_type": "TOTAL",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "put_vol": None, "call_vol": None, "ratio": None,
            "received_at_utc": utcnow(), "is_delayed": True,
            "delay_minutes": DELAY_MIN, "status": "error"}


def existing_dates(path):
    dates = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                dates.add(json.loads(line).get("date"))
            except (ValueError, AttributeError):
                pass
    return dates


def save(rec):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    path = OUTDIR / (rec["date"] + ".jsonl")
    if rec["date"] in existing_dates(path):
        return path, False
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path, True


def parse_args():
    p = argparse.ArgumentParser(description="CBOE total P/C ratio daily snapshot (delayed)")
    p.add_argument("--loop", action="store_true", help="repeat every 24h")
    return p.parse_args()


def main():
    args = parse_args()
    while True:
        rec = collect()
        path, wrote = save(rec)
        print(("wrote " if wrote else "skip ") + str(path) + " " + rec["status"])
        if not args.loop:
            break
        time.sleep(24 * 3600)


if __name__ == "__main__":
    main()
