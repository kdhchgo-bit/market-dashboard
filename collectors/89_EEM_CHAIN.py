"""EEM option chain (delayed, front-2 expiries ATM+-10).

Tradier first (needs TRADIER_TOKEN), CBOE delayed JSON second, Yahoo (crumb) fallback.
Run once by default; --loop repeats every 15 minutes.
Saves to data/EEM_CHAIN/YYYY-MM-DD.jsonl without duplicates.
Usage: python 89_EEM_CHAIN.py [--loop]
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYM = "EEM"
OUT = Path(__file__).parent / "data" / "EEM_CHAIN"
UA = {"User-Agent": "Mozilla/5.0"}
CBOE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Referer": "https://www.cboe.com/"}
T_BASE = "https://api.tradier.com/v1/markets/options"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/EEM.json"
Y_OPT = "https://query2.finance.yahoo.com/v7/finance/options/" + SYM
OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def tradier_headers():
    tok = os.getenv("TRADIER_TOKEN") or os.getenv("TRADIER_API_KEY")
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}", "Accept": "application/json"}


def tradier_collect():
    h = tradier_headers()
    if not h:
        return None, "no-token"
    try:
        r = requests.get(T_BASE + "/expirations",
                         params={"symbol": SYM}, headers=h, timeout=20)
        if r.status_code != 200:
            return None, f"exp-{r.status_code}"
        exps = (r.json().get("expirations") or {}).get("date")
        if not exps:
            return None, "no-expirations"
        exps = [exps] if isinstance(exps, str) else list(exps)
        exps = sorted(exps)[:2]
        q = requests.get("https://api.tradier.com/v1/markets/quotes",
                         params={"symbols": SYM}, headers=h, timeout=20).json()
        qq = q.get("quotes", {}).get("quote", {})
        und = float(qq.get("last", 0) or 0)
        rows = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for exp in exps:
            c = requests.get(T_BASE + "/chains", params={
                "symbol": SYM, "expiration": exp, "greeks": "true"},
                headers=h, timeout=20)
            if c.status_code != 200:
                continue
            opts = (c.json().get("options") or {}).get("option")
            if not opts:
                continue
            if isinstance(opts, dict):
                opts = [opts]
            strikes = sorted({float(o["strike"]) for o in opts})
            if not strikes:
                continue
            _, keep = _atm_window(strikes, und)
            for o in opts:
                if float(o["strike"]) not in keep:
                    continue
                g = o.get("greeks")
                rows.append({"t": now, "symbol": SYM, "underlying": und,
                             "expiration": exp, "strike": float(o["strike"]),
                             "type": o.get("option_type"),
                             "bid": o.get("bid"), "ask": o.get("ask"),
                             "last": o.get("last"), "volume": o.get("volume"),
                             "openInterest": o.get("open_interest"),
                             "iv": g.get("mid_iv") if isinstance(g, dict) else None,
                             "source": "tradier"})
        return rows, "tradier" if rows else "empty"
    except Exception as e:
        return None, f"err-{e}"


def _atm_window(strikes, und, width=10):
    atm = min(strikes, key=lambda s: abs(s - und))
    i = strikes.index(atm)
    return atm, set(strikes[max(0, i - width):i + width + 1])


def _occ_parse(sym):
    m = OCC.match(sym or "")
    if not m:
        return None
    yymmdd, cp = m.group(2), m.group(3)
    exp = f"20{yymmdd[:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    return exp, int(m.group(4)) / 1000.0, ("call" if cp == "C" else "put")


def cboe_collect():
    try:
        r = requests.get(CBOE_URL, headers=CBOE_UA, timeout=20)
        if r.status_code != 200:
            return None, f"cboe-{r.status_code}"
        data = r.json().get("data", {})
        und = float(data.get("current_price") or data.get("close") or 0)
        if not und:
            return None, "cboe-no-spot"
        by_exp = {}
        for o in data.get("options") or []:
            p = _occ_parse(o.get("option"))
            if not p:
                continue
            exp, strike, typ = p
            by_exp.setdefault(exp, []).append((o, strike, typ))
        if not by_exp:
            return None, "cboe-empty"
        rows = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for exp in sorted(by_exp)[:2]:
            items = by_exp[exp]
            strikes = sorted({s for _, s, _ in items})
            if not strikes:
                continue
            _, keep = _atm_window(strikes, und)
            for o, strike, typ in items:
                if strike not in keep:
                    continue
                rows.append({"t": now, "symbol": SYM, "underlying": und,
                             "expiration": exp, "strike": strike,
                             "type": typ, "bid": o.get("bid"),
                             "ask": o.get("ask"),
                             "last": o.get("last_trade_price"),
                             "volume": o.get("volume"),
                             "openInterest": o.get("open_interest"),
                             "iv": o.get("iv"),
                             "source": "cboe"})
        return rows, "cboe" if rows else "empty"
    except Exception as e:
        return None, f"err-{e}"


def yahoo_session():
    s = requests.Session()
    s.headers.update(UA)
    s.get("https://fc.yahoo.com", timeout=20)
    c = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb",
              timeout=20)
    c.raise_for_status()
    crumb = c.text.strip()
    if not crumb:
        raise RuntimeError("empty crumb")
    return s, crumb


def yahoo_fetch(sess, crumb, exp_ts=None):
    params = {"crumb": crumb}
    if exp_ts:
        params["date"] = exp_ts
    r = sess.get(Y_OPT, params=params, headers=UA, timeout=20)
    r.raise_for_status()
    return r.json()["optionChain"]["result"][0]


def yahoo_collect():
    try:
        sess, crumb = yahoo_session()
        first = yahoo_fetch(sess, crumb)
        und = float(first["quote"]["regularMarketPrice"])
        dates = sorted(first.get("expirationDates", []))[:2]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = []
        for ts in dates:
            node = first if ts == first["options"][0]["expirationDate"] else yahoo_fetch(sess, crumb, ts)
            exp = datetime.fromtimestamp(node["options"][0]["expirationDate"],
                                         tz=timezone.utc).strftime("%Y-%m-%d")
            chain = node["options"][0]
            legs = [o for side in ("calls", "puts")
                    for o in chain.get(side, [])]
            strikes = sorted(set(chain.get("strikes", []))
                             or {float(o["strike"]) for o in legs})
            if not strikes:
                continue
            _, keep = _atm_window(strikes, und)
            for side, typ in (("calls", "call"), ("puts", "put")):
                for o in chain.get(side, []):
                    if float(o["strike"]) not in keep:
                        continue
                    rows.append({"t": now, "symbol": SYM, "underlying": und,
                                 "expiration": exp, "strike": float(o["strike"]),
                                 "type": typ, "bid": o.get("bid"),
                                 "ask": o.get("ask"), "last": o.get("lastPrice"),
                                 "volume": o.get("volume"),
                                 "openInterest": o.get("openInterest"),
                                 "iv": o.get("impliedVolatility"),
                                 "source": "yahoo"})
        return rows, "yahoo"
    except Exception as e:
        return None, f"err-{e}"


def _key(d):
    return (d.get("expiration"), d.get("strike"), d.get("type"),
            d.get("bid"), d.get("ask"), d.get("last"))


def save(rows):
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / (datetime.now(timezone.utc).strftime("%Y-%m-%d") + ".jsonl")
    seen = set()
    if fp.exists():
        for line in fp.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(_key(json.loads(line)))
            except Exception:
                pass
    n = 0
    with open(fp, "a", encoding="utf-8") as f:
        for d in rows:
            k = _key(d)
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            n += 1
    return fp, n


def once():
    rows, src = tradier_collect()
    if not rows:
        rows, src = cboe_collect()
    if not rows:
        rows, src = yahoo_collect()
    if not rows:
        print(f"[{SYM}] no data ({src})")
        return 0
    fp, n = save(rows)
    print(f"[{SYM}] {src} wrote {n}/{len(rows)} -> {fp}")
    return n


def main():
    ap = argparse.ArgumentParser(description="EEM chain collector")
    ap.add_argument("--loop", action="store_true", help="repeat every 15min")
    a = ap.parse_args()
    once()
    while a.loop:
        time.sleep(900)
        once()


if __name__ == "__main__":
    main()
