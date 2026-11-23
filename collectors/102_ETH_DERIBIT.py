"""ETH Deribit option chain snapshot (front 2 expiries ATM+-10).

Sources (Deribit public GET, no key):
- https://www.deribit.com/api/v2/public/get_instruments?currency=ETH&kind=option
- https://www.deribit.com/api/v2/public/ticker?instrument_name=<name>
- https://www.deribit.com/api/v2/public/get_index_price?index_name=eth_usd (spot)
Run: python 102_ETH_DERIBIT.py [--loop]  # once, or every 15 min.
Store: Path(__file__).parent/"data"/"ETH_DERIBIT"/YYYY-MM-DD.jsonl, one line per contract.
Record: source/underlying/expiration/strike/option_type/bid/ask/last/volume/
open_interest/iv/received_at_utc/is_delayed/delay_minutes/status. Dedup on
(expiration,strike,option_type) per run + exact-line skip in file. No fake values.
Stdlib + requests only.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CURRENCY = "ETH"
UNDERLYING = "ETH"
BASE = "https://www.deribit.com/api/v2/public"
OUTDIR = Path(__file__).parent / "data" / "ETH_DERIBIT"


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def api(sess, method, params, timeout):
    r = sess.get(f"{BASE}/{method}", params=params, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    if "error" in j and j["error"]:
        raise ValueError(f"deribit {method}: {j['error']}")
    return j["result"]


def atm_slice(items, spot, window):
    items = [c for c in items if c["strike"] is not None]
    if not items or spot is None:
        return items
    sk = sorted(items, key=lambda c: c["strike"])
    idx = min(range(len(sk)), key=lambda i: abs(sk[i]["strike"] - spot))
    return sk[max(0, idx - window): idx + window + 1]


def spot_price(sess, timeout):
    try:
        res = api(sess, "get_index_price", {"index_name": "eth_usd"}, timeout)
        p = num(res.get("index_price") if isinstance(res, dict) else res)
        if p is not None:
            return p
    except Exception:
        pass
    return None


def collect(sess, timeout, window):
    ins = api(sess, "get_instruments", {"currency": CURRENCY, "kind": "option", "expired": "false"}, timeout)
    if not ins:
        raise ValueError("get_instruments returned empty")
    parsed = []
    for o in ins:
        try:
            ts = int(o.get("expiration_timestamp"))
            exp = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
            st = num(o.get("strike"))
            ot = str(o.get("option_type", "")).lower()
            typ = "C" if ot.startswith("c") else "P" if ot.startswith("p") else None
            if st is None or typ is None or not o.get("instrument_name"):
                continue
            parsed.append({"exp": exp, "strike": st, "typ": typ, "name": o["instrument_name"]})
        except Exception:
            continue
    if not parsed:
        raise ValueError("no parseable ETH instruments")
    expiries = sorted({p["exp"] for p in parsed})[:2]
    spot = spot_price(sess, timeout)
    if spot is None:  # fallback: median strike of first expiry
        fst = sorted(p["strike"] for p in parsed if p["exp"] == expiries[0])
        spot = fst[len(fst) // 2] if fst else None
    rows, seen = [], set()
    ts = now_utc()
    for exp in expiries:
        for typ in ("C", "P"):
            pool = [p for p in parsed if p["exp"] == exp and p["typ"] == typ]
            for p in atm_slice(pool, spot, window):
                key = (exp, p["strike"], typ)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    t = api(sess, "ticker", {"instrument_name": p["name"]}, timeout)
                    stt = t.get("stats", {}) if isinstance(t, dict) else {}
                    rows.append({"source": "Deribit", "underlying": UNDERLYING,
                                 "instrument": p["name"], "expiration": exp,
                                 "strike": p["strike"], "option_type": typ,
                                 "bid": num(t.get("best_bid_price")), "ask": num(t.get("best_ask_price")),
                                 "last": num(t.get("last_price")), "volume": num(stt.get("volume")),
                                 "open_interest": num(t.get("open_interest")), "iv": num(t.get("mark_iv")),
                                 "underlying_price": num(t.get("underlying_price")) or spot,
                                 "received_at_utc": ts, "is_delayed": False,
                                 "delay_minutes": 0, "status": "ok"})
                except Exception as e:
                    rows.append({"source": "Deribit", "underlying": UNDERLYING,
                                 "instrument": p["name"], "expiration": exp,
                                 "strike": p["strike"], "option_type": typ,
                                 "bid": None, "ask": None, "last": None, "volume": None,
                                 "open_interest": None, "iv": None, "underlying_price": spot,
                                 "received_at_utc": ts, "is_delayed": False,
                                 "delay_minutes": 0, "status": f"ticker_error: {e}"})
                time.sleep(0.05)
    if not rows:
        raise ValueError("no contracts collected")
    return rows


def save(rows):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    p = OUTDIR / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
    old = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                old.add((d.get("expiration"), d.get("strike"), d.get("option_type"), d.get("received_at_utc")))
            except Exception:
                pass
    n = 0
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            k = (r["expiration"], r["strike"], r["option_type"], r["received_at_utc"])
            if k in old:
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            old.add(k)
            n += 1
    return p, n


def main():
    ap = argparse.ArgumentParser(description="ETH Deribit chain snapshot (front 2 expiries ATM+-N)")
    ap.add_argument("--loop", action="store_true", help="repeat every 15 min")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--window", type=int, default=10, help="ATM+-N strikes per expiry/type")
    a = ap.parse_args()
    sess = requests.Session()
    while True:
        try:
            rows = collect(sess, a.timeout, a.window)
            p, n = save(rows)
            print(f"{now_utc()} wrote {n}/{len(rows)} contracts -> {p} [Deribit]")
        except Exception as e:
            print(f"{now_utc()} ERROR: {e}")
        if not a.loop:
            break
        time.sleep(15 * 60)


if __name__ == "__main__":
    main()
