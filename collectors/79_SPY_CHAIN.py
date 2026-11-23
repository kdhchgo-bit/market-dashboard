"""SPY option chain (delayed, front-2 expirations ATM+-10).

Priority: Tradier first (Authorization: Bearer $TRADIER_API_KEY) when key exists,
no key -> CBOE official delayed JSON primary
(https://cdn.cboe.com/api/global/delayed_quotes/options/SPY.json),
Yahoo (cookie + crumb) last resort.
One shot by default, --loop repeats every 15 min. Delayed data only (is_delayed=true).
Saves JSONL to data/SPY_CHAIN/YYYY-MM-DD.jsonl, one line per contract,
dedup by (contract + snapshot bucket) so repeated snapshots accumulate.
Failure writes status line only, never fake values. stdlib + requests.
"""
import argparse
import datetime
import json
import os
import re
import time
from pathlib import Path

import requests

OUT_DIR = Path(__file__).parent / "data" / "SPY_CHAIN"
T_EXP = "https://api.tradier.com/v1/markets/options/expirations"
T_CHAIN = "https://api.tradier.com/v1/markets/options/chains"
T_QUOTE = "https://api.tradier.com/v1/markets/quotes"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/SPY.json"
Y_OPT = "https://query2.finance.yahoo.com/v7/finance/options/SPY"
Y_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/SPY"
Y_FC = "https://fc.yahoo.com"
Y_CRUMB = "https://query2.finance.yahoo.com/v1/test/getcrumb"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OCC_RE = re.compile(r"(\d{6})([CP])(\d{8})\s*$")


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def snap_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)


def iso(dt):
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def dedup_key(d):
    contract = d.get("contract")
    snap = d.get("snapshot_at_utc") or d.get("received_at_utc")
    if contract:
        return f"{contract}|{snap}"
    return f"{d.get('expiration')}|{d.get('strike')}|{d.get('option_type')}|{snap}"


def load_seen(p):
    s = set()
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if d.get("status") == "ok":
                s.add(dedup_key(d))
    return s


def rec(src, exp, strike, typ, bid, ask, last, vol, oi, iv, dl, gm, th, vg, ts,
        status="ok", contract=None, snapshot=None):
    return {"source": src, "underlying": "SPY", "contract": contract,
            "expiration": exp, "strike": strike,
            "option_type": typ, "bid": bid, "ask": ask, "last": last, "volume": vol,
            "open_interest": oi, "iv": iv, "delta": dl, "gamma": gm, "theta": th,
            "vega": vg, "snapshot_at_utc": snapshot or ts,
            "received_at_utc": ts, "is_delayed": True, "status": status}


def err_rec(src, msg, exp=None):
    return {"source": src, "underlying": "SPY", "contract": None,
            "expiration": exp, "strike": None,
            "option_type": None, "bid": None, "ask": None, "last": None, "volume": None,
            "open_interest": None, "iv": None, "delta": None, "gamma": None, "theta": None,
            "vega": None, "snapshot_at_utc": None,
            "received_at_utc": now_utc(), "is_delayed": True,
            "status": f"error: {msg}"[:300]}


def t_get(url, key, params):
    r = requests.get(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                     params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def tradier_flow(key, snap):
    exps = t_get(T_EXP, key, {"symbol": "SPY", "includeAllRoots": "true"})
    ed = exps.get("expirations", {}) or {}
    dates = ed.get("date")
    if isinstance(dates, str):
        dates = [dates]
    if not dates:
        raise RuntimeError(f"no expirations: {str(exps)[:200]}")
    tday = today()
    dates = sorted(d for d in dates if d >= tday)[:2] or sorted(dates)[:2]
    q = t_get(T_QUOTE, key, {"symbols": "SPY", "greeks": "false"})
    qq = (q.get("quotes") or {}).get("quote")
    if isinstance(qq, list):
        qq = qq[0] if qq else {}
    px = (qq or {}).get("last") or (qq or {}).get("close") or (qq or {}).get("prevclose")
    out = []
    ts = iso(snap)
    for e in dates:
        j = t_get(T_CHAIN, key, {"symbol": "SPY", "expiration": e, "greeks": "true"})
        opts = ((j.get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        if px is None:
            lasts = sorted([o.get("strike") for o in opts if o.get("strike") is not None])
            px = lasts[len(lasts) // 2] if lasts else None
        strikes = sorted({o.get("strike") for o in opts if o.get("strike") is not None})
        atm_i = min(
            range(len(strikes)),
            key=lambda i: abs(strikes[i] - px)) if strikes and px else 0
        keep = set(strikes[max(0, atm_i - 10):atm_i + 11])
        for o in opts:
            if o.get("strike") not in keep:
                continue
            typ = (o.get("option_type") or "").lower() or None
            if typ not in ("call", "put"):
                continue
            g = o.get("greeks") or {}
            out.append(rec("tradier", e, o.get("strike"), typ, o.get("bid"), o.get("ask"),
                           o.get("last"), o.get("volume"), o.get("open_interest"),
                           g.get("mid_iv") or o.get("greeks") and g.get("smv_vol"),
                           g.get("delta"), g.get("gamma"), g.get("theta"), g.get("vega"), ts,
                           contract=o.get("symbol"), snapshot=ts))
    if not out:
        raise RuntimeError("tradier returned no contracts")
    return out


def occ_parse(sym):
    m = OCC_RE.search(str(sym or ""))
    if not m:
        return None, None, None
    ymd, cp, strike8 = m.group(1), m.group(2), m.group(3)
    exp = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}"
    try:
        strike = int(strike8) / 1000.0
    except Exception:
        return None, None, None
    return exp, strike, ("call" if cp == "C" else "put")


def cboe_flow(snap):
    r = requests.get(CBOE_URL, headers=UA, timeout=30)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j)
    px = data.get("current_price") or data.get("close")
    try:
        px = float(px) if px is not None else None
    except (TypeError, ValueError):
        px = None
    opts = data.get("options", [])
    if not opts:
        raise RuntimeError("CBOE returned no options")
    ts = iso(snap)
    by_exp = {}
    for o in opts:
        exp, strike, typ = occ_parse(o.get("option"))
        if exp is None or strike is None or typ is None:
            continue
        by_exp.setdefault(exp, []).append((strike, typ, o))
    if not by_exp:
        raise RuntimeError("CBOE OCC parse yielded nothing")
    tday = today()
    dates = sorted(by_exp)
    fwd = [d for d in dates if d >= tday]
    dates = (fwd or dates)[:2]
    out = []
    for e in dates:
        items = by_exp[e]
        strikes = sorted({s for s, _, _ in items})
        atm_i = min(range(len(strikes)),
                    key=lambda i: abs(strikes[i] - px)) if strikes and px else 0
        keep = set(strikes[max(0, atm_i - 10):atm_i + 11])
        for strike, typ, o in items:
            if strike not in keep:
                continue
            last = o.get("last_trade_price")
            out.append(rec("cboe", e, strike, typ, o.get("bid"), o.get("ask"),
                           last, o.get("volume"), o.get("open_interest"),
                           o.get("iv"), o.get("delta"), o.get("gamma"),
                           o.get("theta"), o.get("vega"), ts,
                           contract=o.get("option"), snapshot=ts))
    if not out:
        raise RuntimeError("CBOE ATM slice empty")
    return out


def yahoo_session():
    s = requests.Session()
    s.headers.update(UA)
    s.get(Y_FC, timeout=20)
    r = s.get(Y_CRUMB, timeout=20)
    r.raise_for_status()
    crumb = r.text.strip()
    if not crumb:
        raise RuntimeError("empty yahoo crumb")
    return s, crumb


def yahoo_flow(snap):
    s, crumb = yahoo_session()

    def yget(url, params=None):
        p = dict(params or {})
        p["crumb"] = crumb
        rr = s.get(url, params=p, timeout=20)
        rr.raise_for_status()
        return rr.json()

    oc = (yget(Y_OPT).get("optionChain") or {}).get("result") or []
    if not oc:
        raise RuntimeError("empty yahoo optionChain")
    m0 = oc[0]
    dates = sorted(m0.get("expirationDates") or [])[:2]
    try:
        px = (m0.get("quote") or {}).get("regularMarketPrice")
    except Exception:
        px = None
    if px is None:
        try:
            px = yget(Y_CHART, {"range": "1d", "interval": "1d"})["chart"]["result"][0]["meta"]["regularMarketPrice"]
        except Exception:
            px = None
    out = []
    ts = iso(snap)
    for ts_exp in dates:
        e = datetime.datetime.fromtimestamp(ts_exp, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        j = yget(Y_OPT, {"date": ts_exp})
        res = ((j.get("optionChain") or {}).get("result") or [{}])[0]
        chain = res.get("options") or [{}]
        calls = (chain[0].get("calls") or []) if chain else []
        puts = (chain[0].get("puts") or []) if chain else []
        allc = [dict(c, _otype="call") for c in calls] + [dict(c, _otype="put") for c in puts]
        if px is None:
            ss = sorted({c.get("strike") for c in allc if c.get("strike") is not None})
            px = ss[len(ss) // 2] if ss else None
        strikes = sorted({c.get("strike") for c in allc if c.get("strike") is not None})
        atm_i = min(
            range(len(strikes)),
            key=lambda i: abs(strikes[i] - px)) if strikes and px else 0
        keep = set(strikes[max(0, atm_i - 10):atm_i + 11])
        for c in allc:
            if c.get("strike") not in keep:
                continue
            typ = c.get("_otype") or "call"
            out.append(rec("yahoo", e, c.get("strike"), typ, c.get("bid"), c.get("ask"),
                           c.get("lastPrice"), c.get("volume"), c.get("openInterest"),
                           c.get("impliedVolatility"), None, None, None, None, ts,
                           contract=c.get("contractSymbol"), snapshot=ts))
    if not out:
        raise RuntimeError("yahoo returned no contracts")
    return out


def collect_once():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"{today()}.jsonl"
    seen = load_seen(p)
    key = os.environ.get("TRADIER_API_KEY", "").strip()
    snap = snap_utc()
    rows, src = [], ("tradier" if key else "cboe")
    try:
        if key:
            try:
                rows = tradier_flow(key, snap)
            except Exception as e:
                print(f"[{(iso(snap))}] tradier failed ({e}), trying CBOE")
                src = "cboe"
                try:
                    rows = cboe_flow(snap)
                except Exception as e2:
                    print(f"[{(iso(snap))}] CBOE failed ({e2}), trying Yahoo")
                    src = "yahoo"
                    rows = yahoo_flow(snap)
        else:
            try:
                rows = cboe_flow(snap)
            except Exception as e:
                print(f"[{(iso(snap))}] CBOE failed ({e}), trying Yahoo")
                src = "yahoo"
                rows = yahoo_flow(snap)
    except Exception as e:
        rows = []
        terl = err_rec(src, str(e))
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(terl, ensure_ascii=False) + "\n")
        print(f"[{terl['received_at_utc']}] {src} failed: {e}")
        return 0
    n = 0
    with open(p, "a", encoding="utf-8") as f:
        for d in rows:
            k = dedup_key(d)
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            n += 1
    print(f"[{now_utc()}] {src} wrote {n}/{len(rows)} to {p.name}")
    return n


def main():
    ap = argparse.ArgumentParser(description="SPY chain collector (delayed, front-2 ATM+-10)")
    ap.add_argument("--loop", action="store_true", help="repeat every 15 minutes")
    a = ap.parse_args()
    collect_once()
    while a.loop:
        time.sleep(900)
        collect_once()


if __name__ == "__main__":
    main()
