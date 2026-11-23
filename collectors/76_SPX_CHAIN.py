"""SPX index option chain snapshot (delayed, front 2 expiries ATM+-10).

Source priority: CBOE official delayed JSON first, Yahoo fallback.
https://cdn.cboe.com/api/global/delayed_quotes/options/SPX.json
https://query2.finance.yahoo.com/v7/finance/options/%5ESPX
No API key. One shot by default, --loop repeats every 15 min.
Output: data/SPX_CHAIN/YYYY-MM-DD.jsonl, one line per contract.
Dedup key is (snapshot_ts+expiration+strike+option_type);
same contract re-snapshotted later is kept. No fake values.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "SPX"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/SPX.json"
YAHOO_URL = "https://query2.finance.yahoo.com/v7/finance/options/%5ESPX"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json,text/plain,*/*",
      "Accept-Language": "en-US,en;q=0.9",
      "Referer": "https://www.cboe.com/",
      "Origin": "https://www.cboe.com"}
OUTDIR = Path(__file__).parent / "data" / "SPX_CHAIN"


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def atm_slice(contracts, spot, window):
    """contracts: list of dicts with 'strike'; keep ATM+-window strikes."""
    if not contracts or spot is None:
        return contracts
    sk = sorted(contracts, key=lambda c: c["strike"] or 0)
    idx = min(range(len(sk)), key=lambda i: abs((sk[i]["strike"] or 0) - spot))
    return sk[max(0, idx - window): idx + window + 1]


def fetch_cboe(sess, timeout, window):
    r = sess.get(CBOE_URL, headers=UA, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j)
    spot = num(data.get("current_price") or data.get("close") or data.get("price"))
    opts = data.get("options", [])
    rows, seen = [], set()
    ts = now_utc()
    for grp in sorted(opts, key=lambda g: str(g.get("expiration_date", "")))[:2]:
        exp = str(grp.get("expiration_date", ""))[:10]
        for typ, leg in (("C", grp.get("calls", [])), ("P", grp.get("puts", []))):
            contracts = [{"strike": num(o.get("strike_price") if o.get("strike_price") is not None else o.get("strike")), "_o": o} for o in (leg or [])]
            contracts = [c for c in contracts if c["strike"] is not None]
            for c in atm_slice(contracts, spot, window):
                o = c["_o"]
                # dedup key includes snapshot ts: same contract
                # re-snapshotted later must NOT be dropped.
                key = (ts, exp, c["strike"], typ)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"source": "CBOE", "underlying": SYMBOL, "expiration": exp, "strike": c["strike"],
                             "option_type": typ, "bid": num(o.get("bid")), "ask": num(o.get("ask")),
                             "last": num(o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last")),
                             "volume": num(o.get("volume")), "open_interest": num(o.get("open_interest")),
                             "iv": num(o.get("iv") if o.get("iv") is not None else o.get("implied_volatility")),
                             "received_at_utc": ts, "is_delayed": True, "delay_minutes": 15, "status": "delayed"})
    if not rows:
        raise ValueError("CBOE returned no contracts")
    return rows


def yahoo_crumb(sess, timeout):
    """fc.yahoo.com cookie -> getcrumb (Yahoo v7 401 fix)."""
    try:
        sess.get("https://fc.yahoo.com/",
                 headers=UA, timeout=timeout)
    except Exception:
        pass
    r = sess.get("https://query2.finance.yahoo.com/v1/test/getcrumb",
                 headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.text.strip()


def yahoo_chain(sess, timeout, crumb, date=None):
    params = {"crumb": crumb} if crumb else {}
    if date:
        params["date"] = date
    r = sess.get(YAHOO_URL, headers=UA, params=params,
                 timeout=timeout)
    if r.status_code == 401:  # crumb expired -> refresh once
        crumb = yahoo_crumb(sess, timeout)
        params["crumb"] = crumb
        r = sess.get(YAHOO_URL, headers=UA, params=params,
                     timeout=timeout)
    r.raise_for_status()
    return r.json()["optionChain"]["result"][0], crumb


def fetch_yahoo(sess, timeout, window):
    crumb = yahoo_crumb(sess, timeout)
    first, crumb = yahoo_chain(sess, timeout, crumb)
    spot = num((first.get("quote") or {}).get("regularMarketPrice"))
    dates = sorted(first.get("expirationDates", []))[:2]
    rows, seen = [], set()
    ts = now_utc()
    for d in dates:
        if d == first["options"][0]["expirationDate"]:
            res = first
        else:
            res, crumb = yahoo_chain(sess, timeout, crumb, d)
        exp = datetime.fromtimestamp(res["options"][0]["expirationDate"], tz=timezone.utc).date().isoformat()
        for typ, leg in (("C", res["options"][0].get("calls", [])), ("P", res["options"][0].get("puts", []))):
            contracts = [{"strike": num(o.get("strike")), "_o": o} for o in (leg or []) if num(o.get("strike")) is not None]
            for c in atm_slice(contracts, spot, window):
                o = c["_o"]
                # dedup key includes snapshot ts (see CBOE).
                key = (ts, exp, c["strike"], typ)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"source": "Yahoo", "underlying": SYMBOL, "expiration": exp, "strike": c["strike"],
                             "option_type": typ, "bid": num(o.get("bid")), "ask": num(o.get("ask")),
                             "last": num(o.get("lastPrice")), "volume": num(o.get("volume")),
                             "open_interest": num(o.get("openInterest")), "iv": num(o.get("impliedVolatility")),
                             "received_at_utc": ts, "is_delayed": True, "delay_minutes": 15, "status": "delayed"})
    if not rows:
        raise ValueError("Yahoo returned no contracts")
    return rows


def collect(sess, timeout, window):
    try:
        return fetch_cboe(sess, timeout, window)
    except Exception as e:
        print(f"CBOE failed ({e}), falling back to Yahoo")
        return fetch_yahoo(sess, timeout, window)


def save(rows):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    p = OUTDIR / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def main():
    ap = argparse.ArgumentParser(description="SPX chain snapshot (delayed)")
    ap.add_argument("--loop", action="store_true", help="repeat every 15 min")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--window", type=int, default=10, help="ATM+-N strikes per expiry")
    global ARGS  # kept for compatibility; prefer local `args`
    ARGS = ap.parse_args()
    args = ARGS
    sess = requests.Session()
    while True:
        try:
            rows = collect(sess, args.timeout, args.window)
            p = save(rows)
            print(f"{now_utc()} wrote {len(rows)} contracts -> {p} [{rows[0]['source']}]")
        except Exception as e:
            print(f"{now_utc()} ERROR: {e}")
        if not args.loop:
            break
        time.sleep(15 * 60)


if __name__ == "__main__":
    main()
