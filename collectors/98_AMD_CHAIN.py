"""AMD 옵션 체인 수집기 (딜레이, 근월 2개 만기 ATM±10).

우선: Tradier (env TRADIER_TOKEN) expirations + chains.
차선: CBOE delayed quotes (https://cdn.cboe.com/.../options/AMD.json, 토큰 불필요).
폴백: Yahoo Finance v7 options (crumb+쿠키 방식, 토큰 불필요).
용법: python 98_AMD_CHAIN.py (1회) / python 98_AMD_CHAIN.py --loop (15분 반복).
저장: <this_dir>/data/AMD_CHAIN/YYYY-MM-DD.jsonl (UTC 일자별, 키 중복금지).
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOL = "AMD"
OUT = Path(__file__).parent / "data" / "AMD_CHAIN"
TH = "https://api.tradier.com/v1/markets"
CB = "https://cdn.cboe.com/api/global/delayed_quotes/options"
YH1 = "https://query1.finance.yahoo.com/v7/finance/options"
YH2 = "https://query2.finance.yahoo.com/v7/finance/options"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SPAN = 10


def utcnow():
    return datetime.now(timezone.utc)


def seen(path):
    s = set()
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                s.add((
                    d.get("snapshot_at_utc"),
                    str(d.get("expiration")),
                    str(d.get("strike")),
                    str(d.get("type")),
                    str(d.get("contract")),
                ))
            except Exception:
                pass
    return s


def atm_strikes(px, strikes, n=SPAN):
    return set(sorted(set(strikes), key=lambda k: abs(float(k) - px))[: 2 * n + 1])


def mk(ts, src, px, exp, strike, typ, contract, bid, ask, last, vol, oi, iv):
    return {
        "ts": ts,
        "source": src,
        "symbol": SYMBOL,
        "underlying": px,
        "expiration": exp,
        "strike": strike,
        "type": typ,
        "contract": contract,
        "bid": bid,
        "ask": ask,
        "last": last,
        "volume": vol,
        "openInterest": oi,
        "iv": iv,
    }


def tradier_get(path, token, params):
    r = requests.get(
        f"{TH}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def tradier_fetch(token):
    ex = tradier_get(
        "options/expirations",
        token,
        {"symbol": SYMBOL, "includeAllRoots": "false", "strikes": "false"},
    )
    dates = (ex.get("expirations") or {}).get("date") or []
    if isinstance(dates, str):
        dates = [dates]
    dates = sorted(dates)[:2]
    q = tradier_get("quotes", token, {"symbols": SYMBOL, "greeks": "false"})
    qq = (q.get("quotes") or {}).get("quote")
    if isinstance(qq, list):
        qq = qq[0]
    px = float((qq or {}).get("last") or 0)
    recs = []
    ts = utcnow().isoformat().replace("+00:00", "Z")
    for exp in dates:
        ch = tradier_get(
            "options/chains",
            token,
            {"symbol": SYMBOL, "expiration": exp, "greeks": "true"},
        )
        opts = ((ch.get("options") or {}).get("option")) or []
        keep = atm_strikes(px, [o.get("strike") for o in opts])
        for o in opts:
            if float(o.get("strike")) not in keep:
                continue
            g = o.get("greeks") or {}
            recs.append(mk(
                ts, "tradier-delayed", px, exp, float(o.get("strike")),
                o.get("option_type"), o.get("symbol"),
                o.get("bid"), o.get("ask"), o.get("last"),
                o.get("volume"), o.get("open_interest"),
                g.get("ask_iv") or g.get("mid_iv"),
            ))
    return recs


OPT_RE = re.compile(r"^[A-Z]+(\d{6})([CP])(\d{8})$")


def parse_cboe_option(code):
    m = OPT_RE.match(str(code))
    if not m:
        return None, None, None
    exp = f"20{m.group(1)[:2]}-{m.group(1)[2:4]}-{m.group(1)[4:6]}"
    typ = "call" if m.group(2) == "C" else "put"
    return exp, typ, int(m.group(3)) / 1000.0


def cboe_fetch():
    last = None
    for _ in range(3):
        try:
            r = requests.get(f"{CB}/{SYMBOL}.json", headers=UA, timeout=30)
            if r.status_code == 429:
                last = "429 rate limited"
                time.sleep(10)
                continue
            r.raise_for_status()
            break
        except Exception as e:
            last = e
            time.sleep(5)
    else:
        raise RuntimeError(f"cboe failed: {last}")
    payload = r.json()
    data = payload.get("data") or {}
    published = payload.get("timestamp")
    if not published:
        raise ValueError("Cboe snapshot timestamp missing")
    stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    px = float(data.get("current_price") or 0)
    by_exp = {}
    for o in data.get("options") or []:
        exp, typ, strike = parse_cboe_option(o.get("option"))
        if exp is None:
            continue
        by_exp.setdefault(exp, []).append((o, typ, strike))
    recs = []
    ts = utcnow().isoformat().replace("+00:00", "Z")
    for exp in sorted(by_exp)[:2]:
        rows = by_exp[exp]
        keep = atm_strikes(px, [s for _, _, s in rows])
        for o, typ, strike in rows:
            if float(strike) not in keep:
                continue
            recs.append(mk(
                ts, "cboe-delayed", px, exp, float(strike),
                typ, o.get("option"),
                o.get("bid"), o.get("ask"), o.get("last_trade_price"),
                o.get("volume"), o.get("open_interest"),
                o.get("iv"),
            ))
    for contract in recs:
        contract["snapshot_at_utc"] = stamp.astimezone(timezone.utc).isoformat()
    return recs


def yahoo_session():
    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get("https://fc.yahoo.com", timeout=15)
    except Exception:
        pass
    return s


def yahoo_crumb(s):
    for u in ("https://query1.finance.yahoo.com/v1/test/getcrumb",
              "https://query2.finance.yahoo.com/v1/test/getcrumb"):
        try:
            r = s.get(u, timeout=15)
            t = (r.text or "").strip()
            if r.status_code == 200 and t and "<" not in t and "crumb" not in t.lower():
                return t
        except Exception:
            continue
    return ""


def yahoo_get(s, sym, crumb, date=None):
    p = {"crumb": crumb} if crumb else {}
    if date is not None:
        p["date"] = date
    last = None
    for base in (YH1, YH2):
        try:
            r = s.get(f"{base}/{sym}", params=p, timeout=30)
            r.raise_for_status()
            return r.json().get("optionChain", {}).get("result", [{}])[0]
        except Exception as e:
            last = e
    raise RuntimeError(f"yahoo failed: {last}")


def infer_type(o):
    pc = o.get("putCall")
    if pc:
        return str(pc).lower()
    sym = str(o.get("contractSymbol", ""))
    return "call" if "C" in sym[1:] else "put"


def yahoo_fetch():
    s = yahoo_session()
    crumb = yahoo_crumb(s)
    top = yahoo_get(s, SYMBOL, crumb)
    px = float((top.get("quote") or {}).get("regularMarketPrice") or 0)
    dates = sorted(top.get("expirationDates") or [])[:2]
    recs = []
    ts = utcnow().isoformat().replace("+00:00", "Z")
    for d in dates:
        exp = datetime.fromtimestamp(d, tz=timezone.utc).date().isoformat()
        det = yahoo_get(s, SYMBOL, crumb, d)
        opts = []
        for chain in det.get("options") or []:
            opts += (chain.get("calls") or []) + (chain.get("puts") or [])
        keep = atm_strikes(px, [o.get("strike") for o in opts])
        for o in opts:
            if float(o.get("strike")) not in keep:
                continue
            recs.append(mk(
                ts, "yahoo-delayed", px, exp, float(o.get("strike")),
                infer_type(o), o.get("contractSymbol"),
                o.get("bid"), o.get("ask"), o.get("lastPrice"),
                o.get("volume"), o.get("openInterest"),
                o.get("impliedVolatility"),
            ))
    return recs


def save(recs):
    if not recs:
        print("no records", flush=True)
        return 0
    path = OUT / f"{utcnow().date().isoformat()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = seen(path)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for d in recs:
            k = (d.get("snapshot_at_utc"), str(d["expiration"]), str(d["strike"]), str(d["type"]), str(d["contract"]))
            if k in keys:
                continue
            keys.add(k)
            f.write(json.dumps(d) + "\n")
            n += 1
    print(f"fetched={len(recs)} new={n} -> {path}", flush=True)
    return n


def once():
    tok = os.environ.get("TRADIER_TOKEN", "")
    try:
        if tok:
            return save(tradier_fetch(tok))
        print("no TRADIER_TOKEN, use cboe", file=sys.stderr)
    except Exception as e:
        print(f"tradier fail ({e}), fallback cboe", file=sys.stderr)
    try:
        return save(cboe_fetch())
    except Exception as e:
        print(f"cboe fail ({e}), fallback yahoo", file=sys.stderr)
    return save(yahoo_fetch())


def loop():
    while True:
        try:
            once()
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
        time.sleep(900)


def main():
    ap = argparse.ArgumentParser(description="AMD option chain collector")
    ap.add_argument("--loop", action="store_true", help="15분 간격 반복")
    a = ap.parse_args()
    if a.loop:
        loop()
    else:
        once()


if __name__ == "__main__":
    main()
