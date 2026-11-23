"""BTC Deribit options collector (public API, no key).

Instruments: GET /api/v2/public/get_instruments?currency=BTC&kind=option&expired=false
Ticker per contract: GET /api/v2/public/ticker?instrument_name=BTC-...
Filter: nearest 2 expirations, ATM+-10 strikes (both C/P).
Storage: data/BTC_DERIBIT/YYYY-MM-DD.jsonl, one JSON per contract.
Run once by default; --loop repeats every 15 minutes. No fake values.
"""
import argparse
import datetime
import json
import time
import urllib.parse
from pathlib import Path

import requests

BASE = "https://www.deribit.com/api/v2/public"
SOURCE = "deribit"
UNDERLYING = "BTC"
OUT_DIR = Path(__file__).parent / "data" / "BTC_DERIBIT"


def fetch(path, params):
    q = urllib.parse.urlencode(params)
    r = requests.get(f"{BASE}/{path}?{q}", timeout=20)
    r.raise_for_status()
    j = r.json()
    if "error" in j and j["error"]:
        raise RuntimeError(str(j["error"]))
    return j["result"]


def spot():
    try:
        r = fetch("get_index_price", {"index_name": "btc_usd"})
        return float(r.get("index_price"))
    except Exception:
        t = fetch("ticker", {"instrument_name": "BTC-PERPETUAL"})
        return float(t.get("last_price") or t.get("mark_price"))


def instruments():
    return fetch("get_instruments", {"currency": "BTC", "kind": "option", "expired": "false"})


def pick(all_ins, px):
    act = [i for i in all_ins if i.get("expiration_timestamp", 0) / 1000 > time.time() - 3600]
    exps = sorted({i["expiration_timestamp"] for i in act if "expiration_timestamp" in i})[:2]
    out = []
    for e in exps:
        grp = [i for i in act if i.get("expiration_timestamp") == e]
        strikes = sorted({float(i["strike"]) for i in grp})
        atm = min(range(len(strikes)), key=lambda k: abs(strikes[k] - px))
        keep = set(strikes[max(0, atm - 10): atm + 11])
        out += [i for i in grp if float(i["strike"]) in keep]
    return sorted(out, key=lambda i: (i["expiration_timestamp"], i["strike"], i.get("option_type", "")))


def ticker(name):
    return fetch("ticker", {"instrument_name": name})


def row(t, ins, now):
    st = t.get("stats", {}) or {}
    g = t.get("greeks", {}) or {}
    exp = datetime.datetime.fromtimestamp(
        ins["expiration_timestamp"] / 1000, tz=datetime.timezone.utc).date().isoformat()
    return {
        "source": SOURCE, "underlying": UNDERLYING,
        "instrument": ins["instrument_name"], "expiration": exp,
        "strike": float(ins["strike"]), "option_type": ins.get("option_type"),
        "bid": t.get("best_bid_price"), "ask": t.get("best_ask_price"),
        "last": t.get("last_price"), "volume": st.get("volume", t.get("volume")),
        "open_interest": t.get("open_interest"), "iv": t.get("mark_iv"),
        "delta": g.get("delta"), "gamma": g.get("gamma"),
        "theta": g.get("theta"), "vega": g.get("vega"),
        "received_at_utc": now, "is_delayed": False, "status": "ok",
    }


def seen_keys(p):
    s = set()
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                s.add((d.get("instrument"), d.get("received_at_utc")))
            except Exception:
                pass
    return s


def run_once():
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    p = OUT_DIR / f"{day}.jsonl"
    seen = seen_keys(p)
    px = spot()
    sel = pick(instruments(), px)
    n = 0
    with p.open("a", encoding="utf-8") as f:
        for ins in sel:
            try:
                t = ticker(ins["instrument_name"])
            except Exception:
                continue
            d = row(t, ins, now)
            if any(v is None for v in (d["bid"], d["ask"], d["last"])):
                pass  # keep partial quote but never fabricate
            k = (d["instrument"], d["received_at_utc"])
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps(d) + "\n")
            n += 1
            time.sleep(0.15)
    print(f"wrote {n}/{len(sel)} spot={px} -> {p}")


def main():
    a = argparse.ArgumentParser(description="Collect BTC Deribit options (near 2 expiries, ATM+-10).")
    a.add_argument("--loop", action="store_true", help="repeat every 15 minutes")
    o = a.parse_args()
    if not o.loop:
        run_once()
        return
    while True:
        try:
            run_once()
        except Exception as e:
            print(f"loop error: {e}")
        time.sleep(900)


if __name__ == "__main__":
    main()
