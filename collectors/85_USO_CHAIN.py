"""USO option chain collector (delayed, front-2 expiries ATM+-10).

Tradier first (needs TRADIER_TOKEN or --tradier-key), then CBOE delayed,
then Yahoo Finance with crumb fallback.
Single run by default; --loop repeats every 15 minutes.
Appends JSONL to data/USO_CHAIN/YYYY-MM-DD.jsonl without duplicates.
Dedup key: (expiry, strike, type, snapshot-minute) = contract + snapshot.
Deps: stdlib + requests.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import requests

SYM = "USO"
UA = {"User-Agent": "Mozilla/5.0"}
T_EXP = "https://api.tradier.com/v1/markets/options/expirations"
T_CHAIN = "https://api.tradier.com/v1/markets/options/chains"
Y_BASE = "https://query2.finance.yahoo.com/v7/finance/options/USO"
Y_BASE1 = "https://query1.finance.yahoo.com/v7/finance/options/USO"
FC_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/USO.json"


def today_path() -> Path:
    d = dt.datetime.now().strftime("%Y-%m-%d")
    p = Path(__file__).parent / "data" / "USO_CHAIN" / f"{d}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def snap_key(ts: str) -> str:
    # snapshot bucket: minute precision (YYYY-MM-DDTHH:MM), 15-min loop safe
    try:
        return str(ts)[:16]
    except Exception:
        return str(ts)


def load_keys(p: Path) -> set:
    s = set()
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(ln)
                s.add((
                    o.get("expiry"),
                    float(o.get("strike", 0)),
                    o.get("type"),
                    snap_key(o.get("ts", "")),
                ))
            except Exception:
                pass
    return s


def tradier_fetch(key: str):
    h = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    r = requests.get(T_EXP, params={"symbol": SYM}, headers=h, timeout=20)
    r.raise_for_status()
    exps = (r.json().get("expirations") or {}).get("date") or []
    if isinstance(exps, str):
        exps = [exps]
    today = dt.datetime.now().strftime("%Y-%m-%d")
    exps = sorted(e for e in exps if e >= today)[:2] or sorted(exps)[:2]
    out, px = [], 0.0
    for e in exps:
        q = requests.get(
            T_CHAIN,
            params={"symbol": SYM, "expiration": e, "greeks": "true"},
            headers=h,
            timeout=20,
        )
        q.raise_for_status()
        opts = ((q.json().get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        for o in opts:
            px = px or float(o.get("underlying_price") or 0)
            g = o.get("greeks") or {}
            out.append({
                "expiry": e,
                "strike": float(o.get("strike", 0)),
                "type": "C" if o.get("option_type") == "call" else "P",
                "bid": o.get("bid"),
                "ask": o.get("ask"),
                "last": o.get("last"),
                "volume": o.get("volume", 0),
                "oi": o.get("open_interest", 0),
                "iv": g.get("mid_iv") if o.get("greeks") else None,
            })
    return px, out, "tradier"


def parse_occ(sym: str):
    """USO261111C00120000 -> (2026-11-11, 12.0, C/P)."""
    m = re.match(r"^[A-Z]+(\d{6})([CP])(\d{8})$", sym or "")
    if not m:
        raise ValueError(f"bad OCC: {sym}")
    ymd, cp, strike = m.groups()
    exp = dt.datetime.strptime(ymd, "%y%m%d").strftime("%Y-%m-%d")
    return exp, int(strike) / 1000.0, "C" if cp == "C" else "P"


def cboe_fetch():
    r = requests.get(CBOE_URL, headers=UA, timeout=20)
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or {}
    px = (
        data.get("current_price")
        or data.get("close")
        or data.get("price")
        or data.get("regular_market_price")
    )
    try:
        px = float(px) if px is not None else 0.0
    except (TypeError, ValueError):
        px = 0.0
    opts = data.get("options") or []
    grouped: dict = {}
    for o in opts:
        try:
            exp, strike, typ = parse_occ(o.get("option") or "")
        except (TypeError, ValueError):
            continue
        grouped.setdefault(exp, []).append((strike, typ, o))
    today = dt.datetime.now().strftime("%Y-%m-%d")
    exps = sorted(e for e in grouped if e >= today)[:2] or sorted(grouped)[:2]
    if not exps:
        raise ValueError("empty cboe")
    if not px:
        # fallback underlying: median strike of front expiry
        all_strikes = sorted({s for e in exps for s, _, _ in grouped[e]})
        px = float(all_strikes[len(all_strikes) // 2]) if all_strikes else 0.0
    out = []
    for e in exps:
        for strike, typ, o in grouped[e]:
            out.append({
                "expiry": e,
                "strike": float(strike),
                "type": typ,
                "bid": o.get("bid"),
                "ask": o.get("ask"),
                "last": o.get("last_trade_price"),
                "volume": o.get("volume", 0),
                "oi": o.get("open_interest", 0),
                "iv": o.get("iv") or None,
            })
    return px, out, "cboe"


def yahoo_session():
    sess = requests.Session()
    sess.headers.update(UA)
    sess.get(FC_URL, timeout=20)
    cr = sess.get(CRUMB_URL, timeout=20)
    cr.raise_for_status()
    crumb = cr.text.strip()
    if not crumb or "error" in crumb.lower():
        raise RuntimeError("no crumb")
    return sess, crumb


def yahoo_get(sess, crumb, url, params=None):
    p = dict(params or {})
    p["crumb"] = crumb
    resp = sess.get(url, params=p, timeout=20)
    if resp.status_code == 401 and url == Y_BASE:
        resp = sess.get(Y_BASE1, params=p, timeout=20)
    resp.raise_for_status()
    return resp


def yahoo_fetch():
    sess, crumb = yahoo_session()
    r = yahoo_get(sess, crumb, Y_BASE)
    r.raise_for_status()
    res = r.json()["optionChain"]["result"][0]
    px = float(res["quote"]["regularMarketPrice"])
    exps = sorted(res.get("expirationDates", []))[:2]
    out = []
    for e in exps:
        q = yahoo_get(sess, crumb, Y_BASE, params={"date": e})
        q.raise_for_status()
        ch = q.json()["optionChain"]["result"][0]["options"][0]
        ed = dt.datetime.utcfromtimestamp(e).strftime("%Y-%m-%d")
        for k, t in (("calls", "C"), ("puts", "P")):
            for o in ch.get(k, []):
                out.append({
                    "expiry": ed,
                    "strike": float(o.get("strike", 0)),
                    "type": t,
                    "bid": o.get("bid"),
                    "ask": o.get("ask"),
                    "last": o.get("lastPrice"),
                    "volume": o.get("volume", 0),
                    "oi": o.get("openInterest", 0),
                    "iv": o.get("impliedVolatility") or None,
                })
    return px, out, "yahoo"


def atm_filter(px: float, rows: list, n: int = 10) -> list:
    keep = []
    for e in sorted({r["expiry"] for r in rows}):
        grp = [r for r in rows if r["expiry"] == e]
        strikes = sorted({r["strike"] for r in grp})
        i = min(range(len(strikes)), key=lambda j: abs(strikes[j] - px))
        want = set(strikes[max(0, i - n):i + n + 1])
        keep += [r for r in grp if r["strike"] in want]
    return keep


def collect(key: str | None) -> int:
    p = today_path()
    seen = load_keys(p)
    px, rows, src = None, [], ""
    if key:
        try:
            px, rows, src = tradier_fetch(key)
            if not rows:
                raise ValueError("empty tradier")
        except Exception as ex:
            print(f"tradier fail ({ex}), cboe fallback")
            px, rows, src = None, [], ""
    if not rows:
        try:
            px, rows, src = cboe_fetch()
            if not rows:
                raise ValueError("empty cboe")
        except Exception as ex:
            print(f"cboe fail ({ex}), yahoo fallback")
            px, rows, src = yahoo_fetch()
    rows = atm_filter(px, rows)
    ts = dt.datetime.now().isoformat(timespec="seconds")
    snap = snap_key(ts)
    new = []
    for r in rows:
        k = (r["expiry"], r["strike"], r["type"], snap)
        if k not in seen:
            seen.add(k)
            new.append({"ts": ts, "symbol": SYM, "underlying": px, "source": src, **r})
    if new:
        with p.open("a", encoding="utf-8") as f:
            for o in new:
                f.write(json.dumps(o) + "\n")
    print(f"{ts} px={px:.2f} src={src} +{len(new)}/{len(rows)} -> {p}")
    return len(new)


def main():
    ap = argparse.ArgumentParser(description="USO chain collector")
    ap.add_argument("--loop", action="store_true", help="repeat every 15 min")
    ap.add_argument("--tradier-key", default=os.getenv("TRADIER_TOKEN", ""))
    a = ap.parse_args()
    key = a.tradier_key or None
    collect(key)
    while a.loop:
        time.sleep(15 * 60)
        collect(key)


if __name__ == "__main__":
    main()
