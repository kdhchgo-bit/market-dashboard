"""GLD option chain collector (delayed, front-2 expiries, ATM +-10).

Tradier first (env TRADIER_TOKEN), then CBOE delayed
https://cdn.cboe.com/api/global/delayed_quotes/options/GLD.json,
else Yahoo (crumb session) fallback. Single run by default;
--loop repeats every 15 minutes. Appends JSONL to
data/GLD_CHAIN/YYYY-MM-DD.jsonl with dedup. stdlib + requests only.
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYM = "GLD"
WIDTH = 10
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/GLD.json"
OPT_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def out_file() -> Path:
    d = Path(__file__).parent / "data" / "GLD_CHAIN"
    d.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    return d / (day + ".jsonl")


def load_keys(p: Path) -> set:
    if not p.exists():
        return set()
    keys = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            keys.add((r.get("expiration"), str(r.get("strike")), r.get("type"), r.get("ts")))
        except Exception:
            keys.add(line.strip())
    return keys


def atm_window(strikes, px, w=WIDTH) -> set:
    s = sorted(set(strikes))
    if not s or not px:
        return set(s)
    atm = min(s, key=lambda x: abs(float(x) - float(px)))
    i = s.index(atm)
    return set(s[max(0, i - w):i + w + 1])


def tradier_chain(tok: str) -> list:
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    exp_resp = requests.get(
        "https://api.tradier.com/v1/markets/options/expirations",
        headers=headers,
        params={"symbol": SYM, "includeAllRoots": "true"},
        timeout=20,
    ).json()
    dates = (exp_resp.get("expirations") or {}).get("date") or []
    if isinstance(dates, str):
        dates = [dates]
    dates = sorted(dates)[:2]
    quote_resp = requests.get(
        "https://api.tradier.com/v1/markets/quotes",
        headers=headers,
        params={"symbols": SYM, "greeks": "false"},
        timeout=20,
    ).json()
    quote = (quote_resp.get("quotes") or {}).get("quote")
    if isinstance(quote, list):
        quote = quote[0]
    px = (quote or {}).get("last")
    recs, strikes = [], set()
    raws = []
    for exp in dates:
        chain = requests.get(
            "https://api.tradier.com/v1/markets/options/chains",
            headers=headers,
            params={"symbol": SYM, "expiration": exp, "greeks": "true"},
            timeout=20,
        ).json()
        opts = ((chain.get("options") or {}).get("option")) or []
        raws.append((exp, opts))
        strikes.update(o.get("strike") for o in opts if o.get("strike") is not None)
    win = atm_window(strikes, px)
    now = datetime.now(timezone.utc).isoformat()
    for exp, opts in raws:
        for o in opts:
            st = o.get("strike")
            if st not in win:
                continue
            greeks = o.get("greeks") or {}
            recs.append({
                "fetched_at": now, "symbol": SYM, "source": "tradier-delayed",
                "expiration": exp, "strike": st, "type": o.get("option_type"),
                "last": o.get("last"), "bid": o.get("bid"), "ask": o.get("ask"),
                "volume": o.get("volume"), "oi": o.get("open_interest"),
                "iv": o.get("iv") or greeks.get("mid_iv"),
                "delta": greeks.get("delta"), "underlying": px,
                "ts": now[:16],
            })
    return recs


def _parse_cboe_opt(code: str):
    m = OPT_RE.match(str(code or ""))
    if not m:
        return None, None, None
    yymmdd, cp, strike8 = m.group(2), m.group(3), m.group(4)
    exp = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    typ = "call" if cp == "C" else "put"
    try:
        strike = int(strike8) / 1000.0
    except Exception:
        return None, None, None
    return exp, typ, strike


def cboe_chain() -> list:
    r = requests.get(CBOE_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    j = r.json()
    data = j.get("data") or {}
    px = data.get("current_price")
    opts = data.get("options") or []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parsed = []
    strikes = set()
    for o in opts:
        exp, typ, strike = _parse_cboe_opt(o.get("option"))
        if exp is None or typ is None or strike is None:
            continue
        if exp < today:
            continue
        parsed.append((exp, typ, strike, o))
        strikes.add(strike)
    if not parsed:
        raise RuntimeError("empty-cboe")
    exps = sorted({e for e, _, _, _ in parsed})[:2]
    front = [p for p in parsed if p[0] in exps]
    win = atm_window({s for _, _, s, _ in front}, px)
    if not win:
        win = atm_window(strikes, px)
    now = datetime.now(timezone.utc).isoformat()
    recs = []
    for exp, typ, strike, o in front:
        if strike not in win:
            continue
        last = o.get("last_trade_price") or o.get("theo") or o.get("prev_day_close")
        recs.append({
            "fetched_at": now, "symbol": SYM, "source": "cboe-delayed",
            "expiration": exp, "strike": strike, "type": typ,
            "last": last, "bid": o.get("bid"), "ask": o.get("ask"),
            "volume": o.get("volume"), "oi": o.get("open_interest"),
            "iv": o.get("iv"), "delta": o.get("delta"),
            "underlying": px, "ts": now[:16],
        })
    if not recs:
        raise RuntimeError("empty-cboe-window")
    return recs


def _yahoo_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    crumb = None
    try:
        s.get("https://finance.yahoo.com/quote/GLD", timeout=15)
    except Exception:
        pass
    try:
        cr = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=15)
        if cr.ok and cr.text.strip() and "crumb" not in cr.text.lower()[:200]:
            crumb = cr.text.strip()
        elif cr.ok and len(cr.text.strip()) < 100:
            crumb = cr.text.strip()
    except Exception:
        crumb = None
    return s, crumb


def yahoo_chain() -> list:
    s, crumb = _yahoo_session()
    base = f"https://query2.finance.yahoo.com/v7/finance/options/{SYM}"

    def _get(url, params=None):
        p = dict(params or {})
        if crumb:
            p["crumb"] = crumb
        resp = s.get(url, params=p, timeout=20)
        if resp.status_code in (401, 403) and crumb is None:
            # plain retry without crumb (old path)
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    first = _get(base)["optionChain"]["result"][0]
    px = (first.get("quote") or {}).get("regularMarketPrice")
    exps = sorted(first.get("expirationDates") or [])[:2]
    now = datetime.now(timezone.utc).isoformat()
    recs, cache = [], {}
    for epoch in exps:
        if epoch == exps[0]:
            cache[epoch] = first
        else:
            url = base
            cache[epoch] = _get(url, {"date": epoch})["optionChain"]["result"][0]
    strikes = set()
    for node in cache.values():
        chains = node.get("options", [{}])[0] if node.get("options") else node
        for side in ("calls", "puts"):
            for o in chains.get(side, []):
                if o.get("strike") is not None:
                    strikes.add(o["strike"])
    win = atm_window(strikes, px)
    for epoch, node in cache.items():
        exp = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")
        opts = node.get("options", [{}])[0] if node.get("options") else node
        for side, typ in (("calls", "call"), ("puts", "put")):
            for o in opts.get(side, []):
                if o.get("strike") not in win:
                    continue
                recs.append({
                    "fetched_at": now, "symbol": SYM, "source": "yahoo-delayed",
                    "expiration": exp, "strike": o.get("strike"), "type": typ,
                    "last": o.get("lastPrice"), "bid": o.get("bid"), "ask": o.get("ask"),
                    "volume": o.get("volume"), "oi": o.get("openInterest"),
                    "iv": o.get("impliedVolatility"), "delta": None,
                    "underlying": px, "ts": now[:16],
                })
    return recs


def collect() -> list:
    tok = os.getenv("TRADIER_TOKEN") or os.getenv("TRADIER_API_KEY")
    if tok:
        try:
            r = tradier_chain(tok)
            if r:
                return r
        except Exception as e:
            print(f"tradier failed ({e}), cboe fallback")
    try:
        r = cboe_chain()
        if r:
            return r
    except Exception as e:
        print(f"cboe failed ({e}), yahoo fallback")
    return yahoo_chain()


def save(recs):
    p = out_file()
    keys = load_keys(p)
    n = 0
    with p.open("a", encoding="utf-8") as f:
        for r in recs:
            k = (r.get("expiration"), str(r.get("strike")), r.get("type"), r.get("ts"))
            if k in keys:
                continue
            keys.add(k)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {n}/{len(recs)} -> {p}")
    return n


def main():
    ap = argparse.ArgumentParser(description="GLD chain collector")
    ap.add_argument("--loop", action="store_true", help="repeat every 15min")
    a = ap.parse_args()
    while True:
        try:
            save(collect())
        except Exception as e:
            print(f"collect error: {e}")
        if not a.loop:
            break
        time.sleep(15 * 60)


if __name__ == "__main__":
    main()
