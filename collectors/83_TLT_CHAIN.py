"""Usage: python 83_TLT_CHAIN.py [--loop] [--interval 900]

Source: Tradier (primary) https://api.tradier.com/v1/markets/options/{expirations,chains}
    -> CBOE delayed https://cdn.cboe.com/api/global/delayed_quotes/options/TLT.json (15min)
    else Yahoo v7 options with crumb (delayed).
Symbol: TLT | Scope: front 2 expiries, ATM+-10 strikes (calls+puts)
Save: data/TLT_CHAIN/YYYY-MM-DD.jsonl (UTC date).
Loop: --loop repeats every 15min (900s); single run collects once.
Dedup by ts+expiry+strike+type (snapshot ts included).
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYM = "TLT"
OUT = Path(__file__).parent / "data" / "TLT_CHAIN"
TH = {"User-Agent": "Mozilla/5.0"}
TB = "https://api.tradier.com/v1"
YH = "https://query2.finance.yahoo.com/v7/finance/options"
YH1 = "https://query1.finance.yahoo.com/v7/finance/options"
YCHART = "https://query1.finance.yahoo.com/v8/finance/chart/TLT"
CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
FC_URL = "https://fc.yahoo.com"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/TLT.json"
CBOE_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cboe.com/",
    "Origin": "https://www.cboe.com",
}


def tkey():
    return os.getenv("TRADIER_TOKEN") or os.getenv("TRADIER_API_KEY")


def window(strikes, px, n=10):
    s = sorted(set(strikes))
    if not s:
        return set()
    i = min(range(len(s)), key=lambda k: abs(s[k] - px))
    return set(s[max(0, i - n):i + n + 1])


def tradier(key, now):
    h = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    q = requests.get(
        f"{TB}/markets/quotes", headers=h, params={"symbols": SYM}, timeout=20
    ).json()
    qq = q.get("quotes", {}).get("quote", {})
    if isinstance(qq, list):
        qq = qq[0] if qq else {}
    px = qq.get("last") or qq.get("close") or qq.get("prevclose")
    e = requests.get(
        f"{TB}/markets/options/expirations",
        headers=h,
        params={"symbol": SYM, "includeAllRoots": "true", "strikes": "false"},
        timeout=20,
    ).json()
    exps = (e.get("expirations") or {}).get("date") or []
    if isinstance(exps, str):
        exps = [exps]
    recs = []
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for xp in exps[:2]:
        c = requests.get(
            f"{TB}/markets/options/chains",
            headers=h,
            params={"symbol": SYM, "expiration": xp, "greeks": "true"},
            timeout=20,
        ).json()
        opts = ((c.get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        keep = window(
            [o.get("strike") for o in opts if o.get("strike") is not None],
            float(px),
            10,
        )
        for o in opts:
            st = o.get("strike")
            if st not in keep:
                continue
            g = o.get("greeks") or {}
            recs.append(
                {
                    "source": "tradier-delayed",
                    "symbol": SYM,
                    "delayed": True,
                    "ts_utc": ts,
                    "underlying": px,
                    "expiry": xp,
                    "strike": st,
                    "type": o.get("option_type"),
                    "bid": o.get("bid"),
                    "ask": o.get("ask"),
                    "last": o.get("last"),
                    "volume": o.get("volume"),
                    "oi": o.get("open_interest"),
                    "iv": o.get("implied_volatility") or g.get("mid_iv"),
                    "delta": g.get("delta"),
                    "status": "ok",
                }
            )
    if not recs:
        raise RuntimeError("empty-tradier")
    return recs


def _parse_occ(sym):
    """Parse OCC symbol e.g. TLT261111C00063000 -> (2026-11-11, 63.0, call)."""
    s = str(sym or "").strip()
    if len(s) < 15:
        return None, None, None
    tail = s[-8:]
    cp = s[-9:-8]
    dat = s[-15:-9]
    if cp not in ("C", "P") or not tail.isdigit() or not dat.isdigit():
        return None, None, None
    try:
        exp = f"20{dat[0:2]}-{dat[2:4]}-{dat[4:6]}"
        datetime.strptime(exp, "%Y-%m-%d")
        return exp, int(tail) / 1000.0, ("call" if cp == "C" else "put")
    except Exception:
        return None, None, None


def cboe(now):
    r = requests.get(CBOE_URL, headers=CBOE_UA, timeout=20)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j) if isinstance(j, dict) else {}
    try:
        px = data.get("current_price") or data.get("close") or data.get("price")
        px = float(px) if px is not None else None
    except (TypeError, ValueError):
        px = None
    opts = data.get("options", [])
    if not isinstance(opts, list) or not opts:
        raise RuntimeError("empty-cboe")
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    recs = []
    # grouped shape: [{expiration_date, calls:[], puts:[]}]
    if opts and isinstance(opts[0], dict) and ("calls" in opts[0] or "puts" in opts[0]):
        groups = sorted(
            [g for g in opts if isinstance(g, dict)], key=lambda g: str(g.get("expiration_date", ""))
        )[:2]
        for g in groups:
            xp = str(g.get("expiration_date", ""))[:10]
            legs = []
            for t, leg in (("call", g.get("calls") or []), ("put", g.get("puts") or [])):
                for o in leg or []:
                    if not isinstance(o, dict):
                        continue
                    st = o.get("strike_price") if o.get("strike_price") is not None else o.get("strike")
                    try:
                        st = float(st) if st is not None else None
                    except (TypeError, ValueError):
                        continue
                    if st is None:
                        continue
                    legs.append((st, t, o))
            if px is None:
                ss = sorted({s for s, _, _ in legs})
                px = ss[len(ss) // 2] if ss else None
            keep = window([s for s, _, _ in legs], float(px), 10) if px is not None else {s for s, _, _ in legs}
            for st, t, o in legs:
                if st not in keep:
                    continue
                last = o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last")
                ivv = o.get("iv")
                if ivv is None:
                    ivv = o.get("implied_volatility") if o.get("implied_volatility") is not None else o.get("impliedVolatility")
                recs.append(
                    {
                        "source": "cboe-delayed",
                        "symbol": SYM,
                        "delayed": True,
                        "ts_utc": ts,
                        "underlying": px,
                        "expiry": xp,
                        "strike": st,
                        "type": t,
                        "bid": o.get("bid"),
                        "ask": o.get("ask"),
                        "last": last,
                        "volume": o.get("volume"),
                        "oi": o.get("open_interest") if o.get("open_interest") is not None else o.get("openInterest"),
                        "iv": ivv,
                        "delta": o.get("delta"),
                        "status": "ok",
                    }
                )
        if not recs:
            raise RuntimeError("empty-cboe-chain")
        return recs
    # flat shape: [{option/symbol, bid, ask, ...}] with OCC symbol
    chains = {}
    for o in opts:
        if not isinstance(o, dict):
            continue
        exp = str(o.get("expiration_date") or o.get("expiry") or "")[:10] or None
        st = o.get("strike_price") if o.get("strike_price") is not None else o.get("strike")
        t = str(o.get("option_type") or o.get("type") or "").lower()
        typ = "put" if t.startswith("p") else ("call" if t.startswith("c") else None)
        if exp is None or st is None or typ is None:
            e2, s2, t2 = _parse_occ(o.get("option") or o.get("symbol") or o.get("contract") or o.get("contractSymbol"))
            if exp is None:
                exp = e2
            if st is None:
                st = s2
            if typ is None:
                typ = t2
        try:
            st = float(st) if st is not None else None
        except (TypeError, ValueError):
            continue
        if exp is None or st is None or typ is None:
            continue
        chains.setdefault(exp, []).append((st, typ, o))
    if px is None:
        spots = [data.get("bid"), data.get("ask")]
        try:
            spots = [float(v) for v in spots if v is not None]
            px = sum(spots) / len(spots) if spots else None
        except (TypeError, ValueError):
            px = None
    for xp in sorted(chains.keys())[:2]:
        legs = chains[xp]
        if px is None:
            ss = sorted({s for s, _, _ in legs})
            lpx = ss[len(ss) // 2] if ss else None
        else:
            lpx = px
        keep = window([s for s, _, _ in legs], float(lpx), 10) if lpx is not None else {s for s, _, _ in legs}
        for st, typ, o in legs:
            if st not in keep:
                continue
            last = o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last")
            ivv = o.get("iv")
            if ivv is None:
                ivv = o.get("implied_volatility") if o.get("implied_volatility") is not None else o.get("impliedVolatility")
            recs.append(
                {
                    "source": "cboe-delayed",
                    "symbol": SYM,
                    "delayed": True,
                    "ts_utc": ts,
                    "underlying": px if px is not None else lpx,
                    "expiry": xp,
                    "strike": st,
                    "type": typ,
                    "bid": o.get("bid"),
                    "ask": o.get("ask"),
                    "last": last,
                    "volume": o.get("volume"),
                    "oi": o.get("open_interest") if o.get("open_interest") is not None else o.get("openInterest"),
                    "iv": ivv,
                    "delta": o.get("delta"),
                    "status": "ok",
                }
            )
    if not recs:
        raise RuntimeError("empty-cboe-chain")
    return recs


def _yahoo_opt_type(contract_symbol, strike, in_the_money):
    sym = str(contract_symbol or "")
    if sym.find("C") > 0 and strike is not None:
        typ = "call"
    elif "P" in sym:
        typ = "put"
    else:
        typ = None
    if strike is not None and typ is None:
        # Original: "call" if bool(inTheMoney) or True else "put" -> always "call".
        typ = "call" if bool(in_the_money) or True else "put"
    if len(sym) >= 9 and sym[-9:-8] == "C":
        return "call"
    if len(sym) >= 9 and sym[-9:-8] == "P":
        return "put"
    return typ


def _yahoo_session():
    """fc.yahoo.com cookie + getcrumb session (Yahoo v7 401 fix)."""
    s = requests.Session()
    s.headers.update(TH)
    crumb = ""
    try:
        try:
            s.get(FC_URL, timeout=10)
        except Exception:
            pass
        r = s.get(CRUMB_URL, timeout=10)
        if r.ok and r.text and "crumb" not in r.text.lower():
            crumb = r.text.strip()
    except Exception:
        crumb = ""
    return s, crumb


def _yahoo_get(s, url, crumb, params=None):
    q = dict(params or {})
    if crumb:
        q["crumb"] = crumb
    last = None
    for base in (url, url.replace("query2.", "query1.")):
        try:
            r = s.get(base, params=q, timeout=20)
            if r.status_code == 401:
                try:
                    rr = s.get(CRUMB_URL, timeout=10)
                    if rr.ok and rr.text and "crumb" not in rr.text.lower():
                        crumb = rr.text.strip()
                        q["crumb"] = crumb
                        r = s.get(base, params=q, timeout=20)
                except Exception as ex:
                    last = ex
                    continue
            r.raise_for_status()
            return r.json(), crumb
        except Exception as ex:
            last = ex
    raise RuntimeError(f"yahoo_options_failed: {last}")


def yahoo(now):
    s, crumb = _yahoo_session()
    px = None
    try:
        m, _ = _yahoo_get(s, YCHART, crumb, {"range": "1d", "interval": "1d"})
        px = ((m.get("chart") or {}).get("result") or [{}])[0].get(
            "meta", {}
        ).get("regularMarketPrice")
    except Exception:
        pass
    j, crumb = _yahoo_get(s, f"{YH}/TLT", crumb)
    r = ((j.get("optionChain") or {}).get("result")) or []
    if not r:
        raise RuntimeError("empty-yahoo")
    r0 = r[0]
    if px is None:
        px = (r0.get("quote") or {}).get("regularMarketPrice")
    dates = r0.get("expirationDates") or []
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    recs = []
    chains = {}
    for ep in dates[:2]:
        d = datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%d")
        if ep in (r0.get("expirationDates") or [])[:1] and r0.get("options"):
            opts = r0.get("options") or [{}]
        else:
            cj, crumb = _yahoo_get(s, f"{YH}/TLT", crumb, {"date": ep})
            rr = ((cj.get("optionChain") or {}).get("result")) or [{}]
            opts = rr[0].get("options") or [{}]
        chains[d] = (opts[0].get("calls") or []) + (opts[0].get("puts") or [])
    allst = [
        c.get("strike") for v in chains.values() for c in v if c.get("strike") is not None
    ]
    keep = window(allst, float(px), 10)
    for xp, cons in chains.items():
        for c in cons:
            st = c.get("strike")
            if st not in keep:
                continue
            recs.append(
                {
                    "source": "yahoo-delayed",
                    "symbol": SYM,
                    "delayed": True,
                    "ts_utc": ts,
                    "underlying": px,
                    "expiry": xp,
                    "strike": st,
                    "type": _yahoo_opt_type(
                        c.get("contractSymbol", ""), c.get("strike"), c.get("inTheMoney")
                    ),
                    "bid": c.get("bid"),
                    "ask": c.get("ask"),
                    "last": c.get("lastPrice"),
                    "volume": c.get("volume"),
                    "oi": c.get("openInterest"),
                    "iv": c.get("impliedVolatility"),
                    "delta": None,
                    "status": "ok",
                }
            )
    if not recs:
        raise RuntimeError("empty-yahoo-chain")
    return recs


def append(recs):
    if not recs:
        return 0
    p = OUT / (recs[0]["ts_utc"][:10] + ".jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                seen.add(
                    f"{d.get('ts_utc')}|{d.get('expiry')}|{d.get('strike')}|{d.get('type')}"
                )
            except Exception:
                pass
    w = 0
    with p.open("a", encoding="utf-8") as fh:
        for r in recs:
            k = f"{r.get('ts_utc')}|{r.get('expiry')}|{r.get('strike')}|{r.get('type')}"
            if k in seen:
                continue
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            seen.add(k)
            w += 1
    return w


def main():
    ap = argparse.ArgumentParser(description="TLT option chain (delayed, front2 ATM+-10)")
    ap.add_argument("--loop", action="store_true", help="repeat every interval")
    ap.add_argument("--interval", type=int, default=900, help="loop seconds (default: 900)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        try:
            k = tkey()
            if k:
                try:
                    recs = tradier(k, now)
                except Exception as ex_t:
                    try:
                        recs = cboe(now)
                    except Exception:
                        recs = yahoo(now)
            else:
                try:
                    recs = cboe(now)
                except Exception as ex_c:
                    recs = yahoo(now)
            print(f"ok +{append(recs)} src={recs[0]['source']} n={len(recs)}")
        except Exception as ex:
            try:
                recs = yahoo(now)
                print(f"fallback ok +{append(recs)} ({ex})")
            except Exception as ex2:
                print(f"error {ex2}")
        if not a.loop:
            break
        time.sleep(max(60, a.interval))


if __name__ == "__main__":
    main()
