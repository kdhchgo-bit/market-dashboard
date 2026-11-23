"""CBOE SKEW 스냅샷(딜레이) 수집 — 키 불필요.
원천: stooq ^skew 스냅샷 -> stooq 일봉 -> Yahoo ^SKEW (순서대로 폴백)
Usage: python 75_CBOE_SKEW.py [--loop] [--interval 900] [--duration 24]
  1회: 스냅샷 저장 | --loop: 24시간 동안 15분마다 반복
저장: Path(__file__).parent/"data"/"SKEW"/YYYY-MM-DD.jsonl (일자=obs_time UTC)
레코드: {source,symbol,value,obs_time,received_at_utc,is_delayed,status}
  status=ok|closed|http_error, is_delayed=true 고정. 중복금지(obs_time 기준).
Deps: stdlib + requests only.
"""
import argparse
import csv
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
SYMBOL, SOURCE = "SKEW", "stooq"
Q_URL = "https://stooq.com/q/l/?s=^skew&f=sd2t2ohlcv&h&e=csv"
D_URL = "https://stooq.com/q/d/l/?s=^skew&i=d"
Y_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ESKEW?interval=1d&range=5d"
OUTDIR = Path(__file__).parent / "data" / "SKEW"
UA = {"User-Agent": "Mozilla/5.0 (skew-collector; delayed-snapshot)"}

def utcnow():
    return datetime.now(timezone.utc)

def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def num(s):
    try:
        s = str(s).strip()
        if not s or s.upper() in ("N/D", "N/A", "NA", "NULL", "-", "NAN"):
            return None
        return float(s.replace(",", ""))
    except (ValueError, TypeError):
        return None

def dst_bounds(y):
    m1 = datetime(y, 3, 1).weekday()
    mar = 1 + ((6 - m1) % 7) + 7
    n1 = datetime(y, 11, 1).weekday()
    nov = 1 + ((6 - n1) % 7)
    return datetime(y, 3, mar, 7, tzinfo=timezone.utc), datetime(y, 11, nov, 6, tzinfo=timezone.utc)

def is_dst(dt):
    s, e = dst_bounds(dt.year)
    return s <= dt < e

def et_to_utc(d, t):
    d = (d or "").strip()
    t = (t or "16:00:00").strip() or "16:00:00"
    if len(t) == 5:
        t += ":00"
    try:
        naive = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    for off in (timedelta(hours=4), timedelta(hours=5)):
        cand = naive.replace(tzinfo=timezone.utc) + off
        if (timedelta(hours=4) if is_dst(cand) else timedelta(hours=5)) == off:
            return cand
    return naive.replace(tzinfo=timezone.utc) + timedelta(hours=5)

def mk_rec(source, value, obs, rx, status):
    return {"source": source, "symbol": SYMBOL, "value": value,
            "obs_time": iso_z(obs) if obs else None, "received_at_utc": rx,
            "is_delayed": True, "status": status}

def fetch_quote(rx):
    r = requests.get(Q_URL, headers=UA, timeout=30)
    r.raise_for_status()
    rows = list(csv.reader(r.text.splitlines()))
    if len(rows) < 2 or len(rows[1]) < 8:
        raise ValueError("empty quote csv")
    _, d, t, _, _, _, c, _ = rows[1][:8]
    v = num(c)
    if v is None:
        raise ValueError("no close")
    return mk_rec("stooq", v, et_to_utc(d, t), rx, "ok")

def fetch_daily(rx):
    r = requests.get(D_URL, headers=UA, timeout=30)
    r.raise_for_status()
    lines = [x for x in r.text.splitlines() if x.strip()]
    if len(lines) < 2:
        raise ValueError("empty daily csv")
    last = None
    for row in csv.DictReader(lines):
        c = num(row.get("Close"))
        if c is None:
            continue
        last = (row.get("Date", "").strip(), c)
    if not last:
        raise ValueError("no daily close")
    return mk_rec("stooq", last[1], et_to_utc(last[0], "16:00:00"), rx, "ok")

def fetch_yahoo(rx):
    r = requests.get(Y_URL, headers={**UA, "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        raise ValueError("empty yahoo")
    res = res[0]
    ts = res.get("timestamp") or []
    qs = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    for t, c in reversed(list(zip(ts, qs))):
        if c is None:
            continue
        return mk_rec("yahoo", float(c), datetime.fromtimestamp(t, tz=timezone.utc), rx, "ok")
    raise ValueError("no yahoo close")

def load_obs(path):
    s = set()
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        s.add(json.loads(line).get("obs_time"))
                    except ValueError:
                        pass
    return s

def save(rec):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    fp = OUTDIR / f"{(rec['obs_time'] or rec['received_at_utc'])[:10]}.jsonl"
    if rec["obs_time"] in load_obs(fp):
        print(f"dup skip obs_time={rec['obs_time']}")
        return 0
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote 1 -> {fp}")
    return 1

def once():
    rx = iso_z(utcnow())
    errs = []
    for fn in (fetch_quote, fetch_daily, fetch_yahoo):
        try:
            return save(fn(rx)), "ok"
        except Exception as e:
            errs.append(f"{fn.__name__}: {e}")
    st = "closed" if utcnow().weekday() >= 5 else "http_error"
    OUTDIR.mkdir(parents=True, exist_ok=True)
    with open(OUTDIR / f"{rx[:10]}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(mk_rec(SOURCE, None, None, rx, st), ensure_ascii=False) + "\n")
    print(f"{st} {' | '.join(errs)}")
    return 0, st

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=900)
    ap.add_argument("--duration", type=float, default=24)
    a = ap.parse_args()
    n, st = once()
    print(f"done status={st} new={n}")
    if not a.loop:
        return
    t0 = time.time()
    while time.time() - t0 < a.duration * 3600:
        time.sleep(max(a.interval, 60))
        try:
            n, st = once()
            print(f"loop status={st} new={n}", flush=True)
        except Exception as e:
            print(f"loop error: {e}", flush=True)
if __name__ == "__main__":
    main()
