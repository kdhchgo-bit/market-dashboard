"""XLK option chain (delayed, front-2 expiries ATM+-10) — Tradier first, CBOE, Yahoo fallback.

Sources: Tradier GET /v1/markets/options/expirations + /v1/markets/options/chains
  (auth: env TRADIER_TOKEN, header Authorization: Bearer);
  CBOE GET https://cdn.cboe.com/api/global/delayed_quotes/options/XLK.json (15min delayed, no key);
  fallback Yahoo v7 with crumb
  GET https://query2.finance.yahoo.com/v7/finance/options/XLK (+chart for spot).
One-shot default; --loop: re-collect every 15 min. Saves
  Path(__file__).parent/"data"/"XLK_CHAIN"/YYYY-MM-DD.jsonl (ET date),
  exact-quote dedup. Stdlib + requests only, no classes.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = dt.timezone(dt.timedelta(hours=-5), "ET")
UTC = dt.timezone.utc

SYM = "XLK"
OUT_DIR = Path(__file__).parent / "data" / "XLK_CHAIN"
T_EXP = "https://api.tradier.com/v1/markets/options/expirations"
T_CHAIN = "https://api.tradier.com/v1/markets/options/chains"
T_QUOTE = "https://api.tradier.com/v1/markets/quotes"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/XLK.json"
Y_OPT = "https://query2.finance.yahoo.com/v7/finance/options/XLK"
Y_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/XLK"
Y_CRUMB = "https://query2.finance.yahoo.com/v1/test/getcrumb"
Y_FC = "https://fc.yahoo.com"
UA = {"User-Agent": "Mozilla/5.0"}
CBOE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Referer": "https://www.cboe.com/"}
OCC_RE = re.compile(r"^[A-Z]+(\d{6})([CP])(\d{8})$")


def now_utc():
    return dt.datetime.now(UTC)


def iso_z(d):
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def get_json(url, headers, params):
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_occ(sym):
    m = OCC_RE.match(str(sym or ""))
    if not m:
        return None, None, None
    ymd, cp, s8 = m.groups()
    exp = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}"
    strike = int(s8) / 1000.0
    return exp, ("call" if cp == "C" else "put"), strike


def window(strikes, spot, n=10):
    s = sorted(set(strikes))
    i = min(range(len(s)), key=lambda k: abs(s[k] - spot))
    return set(s[max(0, i - n):i + n + 1])


def key_of(r):
    keys = ("expiration", "strike", "option_type", "bid", "ask", "last",
            "volume", "open_interest")
    return "|".join(str(r.get(k)) for k in keys)


def existing_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                s.add(key_of(json.loads(line)))
            except Exception:
                pass
    return s


def save(recs):
    if not recs:
        print("no records")
        return
    day = now_utc().astimezone(ET).date().isoformat()
    p = OUT_DIR / f"{day}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = existing_keys(p)
    fresh = [r for r in recs if key_of(r) not in seen]
    if not fresh:
        print("duplicates skipped")
        return
    with p.open("a", encoding="utf-8") as f:
        for r in fresh:
            f.write(json.dumps(r) + "\n")
    print(f"saved {len(fresh)} -> {p}")


def tradier_collect(tok, recv):
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    exps = sorted(get_json(T_EXP, h, {"symbol": SYM}).get("expirations", {}).get("date", []))[:2]
    q = get_json(T_QUOTE, h, {"symbols": SYM}).get("quotes", {}).get("quote", {})
    if isinstance(q, list):
        q = q[0]
    spot = float(q.get("last") or q.get("close") or 0)
    if not exps or not spot:
        raise ValueError("tradier empty expirations/quote")
    out = []
    for exp in exps:
        opts = get_json(
            T_CHAIN, h, {"symbol": SYM, "expiration": exp}
        ).get("options", {}).get("option", [])
        if isinstance(opts, dict):
            opts = [opts]
        keep = window([float(o["strike"]) for o in opts], spot)
        for o in opts:
            st = float(o["strike"])
            if st not in keep:
                continue
            out.append({
                "source": "tradier-delayed", "symbol": SYM,
                "expiration": exp, "strike": st,
                "option_type": str(o.get("option_type", "")).lower(),
                "bid": o.get("bid"), "ask": o.get("ask"),
                "last": o.get("last"), "volume": o.get("volume"),
                "open_interest": o.get("open_interest"),
                "underlying": spot, "delayed": True,
                "received_at_utc": recv, "status": "ok",
            })
    return out


def cboe_collect(recv):
    r = requests.get(CBOE_URL, headers=CBOE_UA, timeout=30)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j)
    spot = num(data.get("current_price") or data.get("close"))
    opts = data.get("options", []) or []
    if not opts or not spot:
        raise ValueError("cboe empty options/spot")
    groups = {}
    for o in opts:
        exp, typ, st = parse_occ(o.get("option"))
        if exp is None:
            continue
        groups.setdefault(exp, []).append((o, typ, st))
    if not groups:
        raise ValueError("cboe unparseable OCC symbols")
    out = []
    for exp in sorted(groups)[:2]:
        legs = groups[exp]
        keep = window([st for _, _, st in legs], spot)
        for o, typ, st in legs:
            if st not in keep:
                continue
            out.append({
                "source": "cboe-delayed", "symbol": SYM,
                "expiration": exp, "strike": st,
                "option_type": typ, "bid": o.get("bid"),
                "ask": o.get("ask"), "last": o.get("last_trade_price"),
                "volume": o.get("volume"),
                "open_interest": o.get("open_interest"),
                "underlying": spot, "delayed": True,
                "received_at_utc": recv, "status": "ok",
            })
    if not out:
        raise ValueError("cboe empty after ATM window")
    return out


def yahoo_session():
    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get(Y_FC, timeout=20)
    except Exception:
        pass
    r = s.get(Y_CRUMB, timeout=20)
    r.raise_for_status()
    crumb = r.text.strip()
    if not crumb or "crumb" in crumb.lower() and len(crumb) > 64:
        raise ValueError("bad crumb")
    return s, crumb


def yahoo_collect(recv):
    s, crumb = yahoo_session()
    first = s.get(Y_OPT, params={"crumb": crumb}, timeout=30)
    first.raise_for_status()
    first = first.json().get("optionChain", {}).get("result", [])[0]
    epochs = (first.get("expirationDates", []) or [])[:2]
    cq = s.get(Y_CHART, params={"crumb": crumb}, timeout=30)
    cq.raise_for_status()
    spot = float(
        cq.json().get("chart", {}).get("result", [])[0]
        ["meta"]["regularMarketPrice"]
    )
    out = []
    for ep in epochs:
        r = s.get(Y_OPT, params={"date": ep, "crumb": crumb}, timeout=30)
        r.raise_for_status()
        r = r.json().get("optionChain", {}).get("result", [])[0]
        exp = dt.datetime.fromtimestamp(r["options"][0]["expirationDate"], tz=UTC).date().isoformat()
        legs = (r.get("options", []) or [{}])[0]
        calls, puts = legs.get("calls", []) or [], legs.get("puts", []) or []
        keep = window([float(o["strike"]) for o in calls + puts], spot)
        for typ, leg in (("call", calls), ("put", puts)):
            for o in leg:
                st = float(o["strike"])
                if st not in keep:
                    continue
                out.append({
                    "source": "yahoo-delayed", "symbol": SYM,
                    "expiration": exp, "strike": st,
                    "option_type": typ, "bid": o.get("bid"),
                    "ask": o.get("ask"), "last": o.get("lastPrice"),
                    "volume": o.get("volume"),
                    "open_interest": o.get("openInterest"),
                    "underlying": spot, "delayed": True,
                    "received_at_utc": recv, "status": "ok",
                })
    return out


def collect():
    recv = iso_z(now_utc())
    tok = os.environ.get("TRADIER_TOKEN", "")
    if tok:
        try:
            return tradier_collect(tok, recv)
        except Exception as e:
            print(f"tradier failed, cboe fallback: {e}", file=sys.stderr)
    try:
        return cboe_collect(recv)
    except Exception as e:
        print(f"cboe failed, yahoo fallback: {e}", file=sys.stderr)
    return yahoo_collect(recv)


def main():
    ap = argparse.ArgumentParser(description="XLK chain (delayed, front-2 ATM+-10)")
    ap.add_argument("--loop", action="store_true", help="repeat every 15 min")
    a = ap.parse_args()
    while True:
        try:
            save(collect())
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
        if not a.loop:
            break
        time.sleep(900)


if __name__ == "__main__":
    main()
