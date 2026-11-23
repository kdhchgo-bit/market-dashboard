"""TSLA option chain collector (delayed, front 2 expirations ATM+-10).

Priority: Tradier first (env TRADIER_TOKEN),
  else CBOE official delayed JSON,
  else Yahoo Finance (cookie + crumb session).
CBOE: https://cdn.cboe.com/api/global/delayed_quotes/options/TSLA.json
Yahoo: https://query2.finance.yahoo.com/v7/finance/options/TSLA (+crumb)
One-shot default; --loop runs every 15 minutes.
Saves to data/TSLA_CHAIN/YYYY-MM-DD.jsonl with snapshot-aware dedup
(same contract re-saved only when quote snapshot changed; exact reruns deduped).
Existing row fields kept; extras (if any) appended at tail (ponytail).
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import requests
    HAS_REQ = True
except ImportError:
    HAS_REQ = False

SYM = "TSLA"
OUT = Path(__file__).parent / "data" / "TSLA_CHAIN"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/TSLA.json"
YAHOO_URL = f"https://query2.finance.yahoo.com/v7/finance/options/{SYM}"
UA = {"User-Agent": "Mozilla/5.0"}
N_EXP = 2
WING = 10
TIMEOUT = 20
LOOP_INTERVAL = 15 * 60


def http_json(url, params=None, headers=None, timeout=TIMEOUT):
    h = dict(UA)
    if headers:
        h.update(headers)
    if HAS_REQ:
        r = requests.get(url, params=params, headers=h, timeout=timeout)
        r.raise_for_status()
        return r.json()
    q = "?" + urllib.parse.urlencode(params or {}) if params else ""
    req = urllib.request.Request(url + q, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.loads(f.read().decode())


def tradier_chain(token):
    H = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    exps = http_json(
        "https://api.tradier.com/v1/markets/options/expirations",
        {"symbol": SYM}, H)["expirations"]["date"]
    exps = (exps if isinstance(exps, list) else [exps])[:N_EXP]
    px = float(http_json("https://api.tradier.com/v1/markets/quotes",
                         {"symbols": SYM}, H)["quotes"]["quote"]["last"])
    rows = []
    for e in exps:
        ch = http_json("https://api.tradier.com/v1/markets/options/chains",
                       {"symbol": SYM, "expiration": e, "greeks": "false"}, H)
        opts = ch["options"]["option"]
        opts = opts if isinstance(opts, list) else [opts]
        strikes = sorted({float(o["strike"]) for o in opts})
        atm = min(strikes, key=lambda s: abs(s - px))
        idx = strikes.index(atm)
        keep = set(strikes[max(0, idx - WING):idx + WING + 1])
        for o in opts:
            if float(o["strike"]) in keep:
                rows.append({"expiration": e, "strike": float(o["strike"]),
                             "type": o["option_type"], "bid": o.get("bid"),
                             "ask": o.get("ask"), "last": o.get("last"),
                             "volume": o.get("volume"), "oi": o.get("open_interest"),
                             "src": "tradier-delayed"})
    return px, rows


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def cboe_chain():
    """CBOE delayed quotes JSON -> (underlying, rows). Same row schema as tradier."""
    j = http_json(CBOE_URL)
    data = j.get("data", j) if isinstance(j, dict) else j
    if not isinstance(data, dict):
        raise ValueError("CBOE: unexpected payload")
    px = _num(data.get("current_price") or data.get("price") or data.get("close"))
    if px is None:
        raise ValueError("CBOE: no underlying price")
    groups = data.get("options", []) or []
    groups = sorted(groups, key=lambda g: str(g.get("expiration_date", "")))[:N_EXP]
    if not groups:
        raise ValueError("CBOE: no expirations")
    rows = []
    for g in groups:
        e = str(g.get("expiration_date", ""))[:10]
        calls = g.get("calls", []) or []
        puts = g.get("puts", []) or []
        allc = ([("call", o) for o in calls] + [("put", o) for o in puts])
        strikes = sorted({_num(o.get("strike_price")
                               if o.get("strike_price") is not None
                               else o.get("strike")) for _, o in allc} - {None})
        if not strikes:
            continue
        atm = min(strikes, key=lambda s: abs(s - px))
        idx = strikes.index(atm)
        keep = set(strikes[max(0, idx - WING):idx + WING + 1])
        for t, o in allc:
            st = _num(o.get("strike_price")
                      if o.get("strike_price") is not None else o.get("strike"))
            if st is None or st not in keep:
                continue
            last = o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last")
            rows.append({"expiration": e, "strike": st, "type": t,
                         "bid": o.get("bid"), "ask": o.get("ask"),
                         "last": last, "volume": o.get("volume"),
                         "oi": o.get("open_interest"),
                         "src": "cboe-delayed"})
    if not rows:
        raise ValueError("CBOE returned no contracts")
    return px, rows


def _yahoo_session():
    """requests.Session with Yahoo cookie set. Raises if requests missing."""
    if not HAS_REQ:
        raise RuntimeError("requests not available for crumb flow")
    s = requests.Session()
    s.headers.update(UA)
    # cookie (404 still sets A3 cookie - ignore status)
    try:
        s.get("https://fc.yahoo.com/", timeout=TIMEOUT)
    except Exception:
        pass
    return s


def _yahoo_crumb(s):
    r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=TIMEOUT)
    r.raise_for_status()
    crumb = r.text.strip()
    if not crumb or len(crumb) > 128 or " " in crumb or "<" in crumb:
        raise ValueError(f"bad crumb: {crumb[:60]}")
    return crumb


def _yahoo_opt(s, crumb, ts=None):
    params = {"crumb": crumb}
    if ts is not None:
        params["date"] = ts
    r = s.get(YAHOO_URL, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    res = r.json()["optionChain"]["result"][0]
    return res


def yahoo_chain():
    """Yahoo delayed chain via cookie+crumb -> (underlying, rows). Same row schema."""
    if not HAS_REQ:
        # legacy urllib path (no crumb; expected to 401 on current Yahoo)
        q = http_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{SYM}",
                      {"interval": "1d", "range": "1d"})
        px = float(q["chart"]["result"][0]["meta"]["regularMarketPrice"])
        base = http_json(YAHOO_URL)
        exps = base["optionChain"]["result"][0]["expirationDates"][:N_EXP]
        rows = []
        for ts in exps:
            e = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            d = http_json(YAHOO_URL, {"date": ts})
            chain = d["optionChain"]["result"][0]["options"][0]
            allc = ([("call", o) for o in chain.get("calls", [])]
                    + [("put", o) for o in chain.get("puts", [])])
            strikes = sorted({float(o["strike"]) for _, o in allc})
            atm = min(strikes, key=lambda s: abs(s - px))
            idx = strikes.index(atm)
            keep = set(strikes[max(0, idx - WING):idx + WING + 1])
            for t, o in allc:
                if float(o["strike"]) in keep:
                    rows.append({"expiration": e, "strike": float(o["strike"]), "type": t,
                                 "bid": o.get("bid"), "ask": o.get("ask"),
                                 "last": o.get("lastPrice"),
                                 "volume": o.get("volume"), "oi": o.get("openInterest"),
                                 "src": "yahoo-delayed"})
        return px, rows
    s = _yahoo_session()
    crumb = _yahoo_crumb(s)
    base = _yahoo_opt(s, crumb)
    px = float(base["quote"]["regularMarketPrice"])
    exps = sorted(base.get("expirationDates", []))[:N_EXP]
    if not exps:
        raise ValueError("Yahoo: no expirations")
    rows = []
    first_exp_ts = (base.get("options") or [{}])[0].get("expirationDate")
    for ts in exps:
        if ts == first_exp_ts:
            res = base
        else:
            res = _yahoo_opt(s, crumb, ts)
        chain = res["options"][0]
        e = datetime.fromtimestamp(chain["expirationDate"], tz=timezone.utc).strftime("%Y-%m-%d")
        allc = ([("call", o) for o in chain.get("calls", [])]
                + [("put", o) for o in chain.get("puts", [])])
        strikes = sorted({float(o["strike"]) for _, o in allc})
        atm = min(strikes, key=lambda s: abs(s - px))
        idx = strikes.index(atm)
        keep = set(strikes[max(0, idx - WING):idx + WING + 1])
        for t, o in allc:
            if float(o["strike"]) in keep:
                rows.append({"expiration": e, "strike": float(o["strike"]), "type": t,
                             "bid": o.get("bid"), "ask": o.get("ask"),
                             "last": o.get("lastPrice"),
                             "volume": o.get("volume"), "oi": o.get("openInterest"),
                             "src": "yahoo-delayed"})
    if not rows:
        raise ValueError("Yahoo returned no contracts")
    return px, rows


def _snap_key(d):
    """Snapshot-aware dedup key: contract + quote snapshot values.

    Same contract with changed bid/ask/last/volume/oi is a NEW snapshot
    (appended); byte-identical reruns dedup to zero new rows.
    """
    return (d.get("expiration"), d.get("strike"), d.get("type"),
            d.get("bid"), d.get("ask"), d.get("last"),
            d.get("volume"), d.get("oi"))


def save(px, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / f"{date.today().isoformat()}.jsonl"
    seen = set()
    if fp.exists():
        for ln in fp.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(_snap_key(json.loads(ln)))
            except Exception:
                pass
    ts = datetime.now(timezone.utc).isoformat()
    n = 0
    with open(fp, "a", encoding="utf-8") as f:
        for r in rows:
            k = _snap_key(r)
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps({"ts": ts, "symbol": SYM, "underlying": px, **r}) + "\n")
            n += 1
    print(f"{fp} underlying={px} new={n} total={len(seen)}", flush=True)
    return n


def once():
    tok = os.getenv("TRADIER_TOKEN", "")
    try:
        if tok:
            return save(*tradier_chain(tok))
        print("no TRADIER_TOKEN -> cboe fallback", file=sys.stderr)
    except Exception as e:
        print(f"tradier failed ({e}) -> cboe fallback", file=sys.stderr)
    try:
        return save(*cboe_chain())
    except Exception as e:
        print(f"cboe failed ({e}) -> yahoo(crumb) fallback", file=sys.stderr)
    return save(*yahoo_chain())


def loop():
    while True:
        try:
            once()
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
        time.sleep(LOOP_INTERVAL)


def main():
    global WING, TIMEOUT
    ap = argparse.ArgumentParser(description="TSLA chain collector")
    ap.add_argument("--loop", action="store_true", help="repeat every 15 min")
    # ponytail (optional, defaults preserve legacy behaviour)
    ap.add_argument("--window", type=int, default=WING, help="ATM+-N strikes per expiry")
    ap.add_argument("--timeout", type=int, default=TIMEOUT, help="HTTP timeout sec")
    a = ap.parse_args()
    WING, TIMEOUT = a.window, a.timeout
    if not a.loop:
        once()
        return
    loop()


if __name__ == "__main__":
    main()
