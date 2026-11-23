"""GOOGL 옵션 체인 수집기 (딜레이, 근월2개 ATM±10).
우선: Tradier (env TRADIER_TOKEN 또는 --tradier-key) — expirations→chains 2개→ATM±10.
차선: CBOE delayed (https://cdn.cboe.com/api/global/delayed_quotes/options/GOOGL.json, 15분 딜레이).
폴백: Yahoo Finance v7 options + crumb (토큰 불필요, 15분 딜레이).
용법: python 95_GOOGL_CHAIN.py (1회) / python 95_GOOGL_CHAIN.py --loop (15분 반복).
저장: <this_dir>/data/GOOGL_CHAIN/YYYY-MM-DD.jsonl (당일 expiry|strike|type|snapshot_utc 중복금지).
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

SYMBOL = "GOOGL"
OUT = Path(__file__).parent / "data" / "GOOGL_CHAIN"
UA = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 30
LOOP_INTERVAL = 900
N_EXP = 2
WING = 10
T_EXP = "https://api.tradier.com/v1/markets/options/expirations"
T_CHAIN = "https://api.tradier.com/v1/markets/options/chains"
Y_BASE = "https://query2.finance.yahoo.com/v7/finance/options/GOOGL"
Y_HOSTS = [
    "https://query1.finance.yahoo.com/v7/finance/options/GOOGL",
    "https://query2.finance.yahoo.com/v7/finance/options/GOOGL",
]
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/GOOGL.json"
_OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def utcnow():
    return datetime.now(timezone.utc)


def seen_keys(path: Path) -> set:
    s = set()
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(ln)
                snap = o.get("snapshot_utc") or o.get("snapshot") or ""
                s.add((o.get("expiry"), float(o.get("strike", 0)), o.get("type"), snap))
            except Exception:
                pass
    return s


def atm_window(strikes, px, n=WING):
    s = sorted(set(strikes))
    i = min(range(len(s)), key=lambda k: abs(s[k] - px))
    return set(s[max(0, i - n):i + n + 1])


def tradier_fetch(key: str):
    h = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    r = requests.get(T_EXP, params={"symbol": SYMBOL}, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    exps = (r.json().get("expirations") or {}).get("date") or []
    if isinstance(exps, str):
        exps = [exps]
    today = utcnow().date().isoformat()
    exps = sorted(e for e in exps if e >= today)[:N_EXP]
    out, px = [], 0.0
    for e in exps:
        q = requests.get(T_CHAIN, params={"symbol": SYMBOL, "expiration": e, "greeks": "true"},
                         headers=h, timeout=TIMEOUT)
        q.raise_for_status()
        opts = ((q.json().get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        for o in opts:
            px = px or float(o.get("underlying_price") or 0)
            out.append({"expiry": e, "strike": float(o.get("strike", 0)),
                        "type": "C" if o.get("option_type") == "call" else "P",
                        "bid": o.get("bid"), "ask": o.get("ask"), "last": o.get("last"),
                        "volume": o.get("volume", 0), "oi": o.get("open_interest", 0),
                        "iv": (o.get("greeks") or {}).get("mid_iv") if o.get("greeks") else None})
    return px, out, "tradier"


def _parse_occ(sym: str):
    m = _OCC.match(str(sym).strip())
    if not m:
        return None
    _, ymd, cp, strike = m.groups()
    exp = f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}"
    return exp, int(strike) / 1000.0, "C" if cp == "C" else "P"


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def cboe_fetch():
    r = requests.get(CBOE_URL, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j)
    px = _num(data.get("current_price") or data.get("close") or data.get("price")) or 0.0
    groups = data.get("options", [])
    out = []
    if groups and isinstance(groups[0], dict) and ("calls" in groups[0] or "puts" in groups[0]):
        # grouped schema: [{expiration_date, calls:[...], puts:[...]}]
        for grp in groups:
            ed = str(grp.get("expiration_date", ""))[:10]
            for k, t in (("calls", "C"), ("puts", "P")):
                for o in grp.get(k, []) or []:
                    st = o.get("strike_price") if o.get("strike_price") is not None else o.get("strike")
                    try:
                        st = float(st)
                    except (TypeError, ValueError):
                        continue
                    out.append({"expiry": ed, "strike": st, "type": t,
                                "bid": o.get("bid"), "ask": o.get("ask"),
                                "last": o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last"),
                                "volume": o.get("volume", 0), "oi": o.get("open_interest", 0),
                                "iv": o.get("iv") if o.get("iv") is not None else o.get("implied_volatility")})
    else:
        # flat OCC schema: [{option: "GOOGL261111C00200000", bid/ask/...}]
        for o in groups:
            if not isinstance(o, dict):
                continue
            p = _parse_occ(o.get("option", ""))
            if not p:
                # flat non-OCC fallback (expiration_date/strike keys)
                ed = str(o.get("expiration_date") or o.get("expiry") or "")[:10]
                try:
                    st = float(o.get("strike_price") if o.get("strike_price") is not None else o.get("strike"))
                except (TypeError, ValueError):
                    continue
                t = str(o.get("option_type") or o.get("type") or "C").upper()
                t = "C" if t.startswith("C") else "P"
                if not ed:
                    continue
                p = (ed, st, t)
            ed, st, t = p
            out.append({"expiry": ed, "strike": st, "type": t,
                        "bid": o.get("bid"), "ask": o.get("ask"),
                        "last": o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last"),
                        "volume": o.get("volume", 0), "oi": o.get("open_interest", 0),
                        "iv": o.get("iv") if o.get("iv") is not None else o.get("implied_volatility")})
    if not out:
        raise ValueError("empty cboe")
    today = utcnow().date().isoformat()
    exps = sorted({x["expiry"] for x in out if x["expiry"] >= today})[:N_EXP]
    if exps:
        out = [x for x in out if x["expiry"] in exps]
    return px, out, "cboe-delayed"


def _yahoo_session_crumb():
    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get("https://fc.yahoo.com", timeout=TIMEOUT)
    except Exception:
        pass
    crumb = None
    for url in ("https://query1.finance.yahoo.com/v1/test/getcrumb",
                "https://query2.finance.yahoo.com/v1/test/getcrumb"):
        try:
            r = s.get(url, timeout=TIMEOUT)
            t = (r.text or "").strip()
            if r.ok and t and "{" not in t and len(t) < 100:
                crumb = t
                break
        except Exception:
            continue
    return s, crumb


def _yahoo_get(sess, url, params, crumb):
    p = dict(params or {})
    if crumb:
        p["crumb"] = crumb
    r = sess.get(url, params=p, timeout=TIMEOUT)
    if r.status_code == 401 and crumb is None:
        s2, c2 = _yahoo_session_crumb()
        sess.cookies.update(s2.cookies)
        return _yahoo_get(sess, url, params, c2)
    r.raise_for_status()
    return r


def yahoo_fetch():
    sess, crumb = _yahoo_session_crumb()
    last_ex = None
    for base in Y_HOSTS:
        try:
            r = _yahoo_get(sess, base, None, crumb)
            res = r.json()["optionChain"]["result"][0]
            px = float(res["quote"]["regularMarketPrice"])
            exps = sorted(res.get("expirationDates", []))[:N_EXP]
            out = []
            for e in exps:
                q = _yahoo_get(sess, base, {"date": e}, crumb)
                ch = q.json()["optionChain"]["result"][0]["options"][0]
                ed = datetime.fromtimestamp(e, tz=timezone.utc).strftime("%Y-%m-%d")
                for k, t in (("calls", "C"), ("puts", "P")):
                    for o in ch.get(k, []):
                        out.append({"expiry": ed, "strike": float(o.get("strike", 0)), "type": t,
                                    "bid": o.get("bid"), "ask": o.get("ask"), "last": o.get("lastPrice"),
                                    "volume": o.get("volume", 0), "oi": o.get("openInterest", 0),
                                    "iv": (o.get("impliedVolatility") or None)})
            if not out:
                raise ValueError("empty yahoo")
            return px, out, "yahoo"
        except Exception as ex:
            last_ex = ex
            continue
    raise last_ex or ValueError("yahoo failed")


def atm_filter(px: float, rows: list, n: int = WING) -> list:
    if not rows:
        return rows
    keep = []
    for e in sorted({r["expiry"] for r in rows}):
        grp = [r for r in rows if r["expiry"] == e]
        want = atm_window([r["strike"] for r in grp], px, n)
        keep += [r for r in grp if r["strike"] in want]
    return keep


def once(key: str | None = None) -> int:
    day = utcnow().strftime("%Y-%m-%d")
    p = OUT / f"{day}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = seen_keys(p)
    px, rows, src = None, [], ""
    try:
        if key:
            px, rows, src = tradier_fetch(key)
        if not rows:
            raise ValueError("empty tradier")
    except Exception as ex:
        print(f"tradier fail ({ex}), cboe fallback", file=sys.stderr)
        try:
            px, rows, src = cboe_fetch()
        except Exception as ex2:
            print(f"cboe fail ({ex2}), yahoo fallback", file=sys.stderr)
            px, rows, src = yahoo_fetch()
    rows = atm_filter(px, rows)
    ts = utcnow().isoformat(timespec="seconds").replace("+00:00", "Z")
    snap = utcnow().replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    new = []
    for r in rows:
        k = (r["expiry"], r["strike"], r["type"], snap)
        if k not in seen:
            seen.add(k)
            new.append({"ts": ts, "snapshot_utc": snap, "symbol": SYMBOL, "underlying": px, "source": src, **r})
    if new:
        with p.open("a", encoding="utf-8") as f:
            for o in new:
                f.write(json.dumps(o) + "\n")
    print(f"{ts} px={px:.2f} src={src} +{len(new)}/{len(rows)} -> {p}")
    return len(new)


def loop(key: str | None = None):
    while True:
        try:
            once(key)
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
        time.sleep(LOOP_INTERVAL)


def main():
    ap = argparse.ArgumentParser(description="GOOGL chain collector")
    ap.add_argument("--loop", action="store_true", help="15분마다 반복")
    ap.add_argument("--tradier-key", default=os.getenv("TRADIER_TOKEN", ""))
    a = ap.parse_args()
    key = a.tradier_key or None
    if not a.loop:
        once(key)
        return
    loop(key)


if __name__ == "__main__":
    main()
