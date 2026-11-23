"""XLF 옵션 체인 수집기 (딜레이, 근월 2개 만기 ATM±10).

원천(우선순위):
  1) Tradier: GET https://api.tradier.com/v1/markets/options/expirations
     + /chains (헤더 Authorization: Bearer $TRADIER_TOKEN).
  2) CBOE: GET https://cdn.cboe.com/api/global/delayed_quotes/options/XLF.json (15분 딜레이).
  3) Yahoo 폴백(crumb): https://query2.finance.yahoo.com/v7/finance/options/XLF (+crumb).

동작: 1회 수집 = 근월 2개 만기 × ATM±10 스트라이크(call/put).
  --loop: 15분 간격 반복 수집 (Ctrl+C 종료).
저장: Path(__file__).parent/"data"/"XLF_CHAIN"/YYYY-MM-DD.jsonl (ET 일자, 만기+행사가+타입 중복 금지).
행 스키마: {"symbol","expiration","strike","type","bid","ask","last","volume","oi","iv","underlying","ts","source"}.
의존성: 표준lib + requests.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "XLF"
ET = ZoneInfo("America/New_York")
OUT = Path(__file__).parent / "data" / "XLF_CHAIN"
UA = {"User-Agent": "Mozilla/5.0"}
TSRC = "https://api.tradier.com/v1/markets/options"
YSRC = "https://query2.finance.yahoo.com/v7/finance/options"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/XLF.json"
CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def tradier_exp():
    tok = os.getenv("TRADIER_TOKEN", "")
    r = requests.get(
        f"{TSRC}/expirations",
        params={"symbol": SYMBOL},
        headers=_headers(tok),
        timeout=30,
    )
    r.raise_for_status()
    e = r.json().get("expirations", {}).get("date")
    e = [e] if isinstance(e, str) else (e or [])
    return sorted(e)[:2]


def tradier_chain(exp):
    tok = os.getenv("TRADIER_TOKEN", "")
    r = requests.get(
        f"{TSRC}/chains",
        params={"symbol": SYMBOL, "expiration": exp, "greeks": "true"},
        headers=_headers(tok),
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("options", {}).get("option") or []


def tradier_px():
    tok = os.getenv("TRADIER_TOKEN", "")
    r = requests.get(
        "https://api.tradier.com/v1/markets/quotes",
        params={"symbols": SYMBOL},
        headers=_headers(tok),
        timeout=30,
    )
    r.raise_for_status()
    q = r.json().get("quotes", {}).get("quote", {})
    q = q[0] if isinstance(q, list) else q
    return float(q.get("last") or q.get("close") or 0)


def _yahoo_session():
    s = requests.Session()
    s.headers.update(UA)
    crumb = None
    try:
        s.get("https://fc.yahoo.com", timeout=15)
        r = s.get(CRUMB_URL, timeout=15)
        if r.ok and r.text and "crumb" not in r.text.lower():
            crumb = r.text.strip()
    except Exception:
        crumb = None
    return s, crumb


def yahoo(exp=None, sess=None, crumb=None):
    s = sess or requests.Session()
    if sess is None:
        s.headers.update(UA)
    p = {} if exp is None else {"date": exp}
    if crumb:
        p["crumb"] = crumb
    r = s.get(f"{YSRC}/{SYMBOL}", params=p, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()["optionChain"]["result"][0]


def cboe_chain(sess):
    r = sess.get(CBOE_URL, headers=UA, timeout=30)
    r.raise_for_status()
    j = r.json()
    return j.get("data", j)


def norm_t(o, exp, px, ts, src, side=None):
    g = o.get("greeks") or {}
    if side in ("call", "put"):
        typ = side
    else:
        opt = str(o.get("option_type") or o.get("contractType") or "").lower()
        if opt.startswith("c"):
            typ = "call"
        elif opt.startswith("p"):
            typ = "put"
        else:
            # Yahoo v7 legs have no type field: infer from contractSymbol (e.g. XLF261111C00053000)
            import re
            sym = str(o.get("symbol") or o.get("contractSymbol") or "")
            m = re.search(r"([CP])\d{8,}", sym)
            typ = "put" if m and m.group(1) == "P" else "call"
    iv = g.get("mid_iv") if isinstance(g, dict) else o.get("impliedVolatility")
    return {
        "symbol": o.get("symbol") or o.get("contractSymbol"),
        "expiration": exp,
        "strike": float(o.get("strike")),
        "type": typ,
        "bid": o.get("bid"),
        "ask": o.get("ask"),
        "last": o.get("last"),
        "volume": o.get("volume"),
        "oi": o.get("open_interest", o.get("openInterest")),
        "iv": iv,
        "underlying": px,
        "ts": ts,
        "source": src,
    }


def norm_cboe(o, exp, px, ts):
    op = str(o.get("option") or o.get("type") or "").upper()
    typ = "call" if op.startswith("C") else "put"
    sym = o.get("symbol") or o.get("option_symbol")
    return {
        "symbol": sym,
        "expiration": exp,
        "strike": _num(o.get("strike_price") if o.get("strike_price") is not None else o.get("strike")),
        "type": typ,
        "bid": _num(o.get("bid")),
        "ask": _num(o.get("ask")),
        "last": _num(o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last")),
        "volume": o.get("volume"),
        "oi": o.get("open_interest"),
        "iv": _num(o.get("iv") if o.get("iv") is not None else o.get("implied_volatility")),
        "underlying": px,
        "ts": ts,
        "source": "cboe",
    }


def pick(rows, px, n=10):
    # 만기별 ATM±n (근월2개 × 콜/풋 스냅샷 기준)
    if not px:
        return rows
    out = []
    exps = sorted({r["expiration"] for r in rows})
    for e in exps:
        sub = [r for r in rows if r["expiration"] == e]
        ks = sorted({r["strike"] for r in sub}, key=lambda s: abs(s - px))
        keep = set(ks[: 2 * n + 1])
        out += [r for r in sub if r["strike"] in keep]
    return out


def _dedup(rows):
    seen = set()
    out = []
    for r in rows:
        k = (r.get("expiration"), r.get("strike"), r.get("type"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def collect_cboe(ts, sess):
    d = cboe_chain(sess)
    px = _num(d.get("current_price") or d.get("close") or d.get("price"))
    opts = d.get("options") or []
    rows = []
    for grp in sorted(opts, key=lambda g: str(g.get("expiration_date", "")))[:2]:
        exp = str(grp.get("expiration_date", ""))[:10]
        for leg, code in (("calls", "C"), ("puts", "P")):
            for o in grp.get(leg) or []:
                o = dict(o)
                o.setdefault("option", code)
                try:
                    rows.append(norm_cboe(o, exp, px, ts))
                except Exception:
                    continue
    rows = [r for r in rows if r.get("strike") is not None]
    rows = pick(rows, px) if px else rows
    return _dedup(rows)


def collect_yahoo(ts):
    sess, crumb = _yahoo_session()
    try:
        j = yahoo(None, sess, crumb)
    except Exception:
        # crumb 없이 재시도 (구 Yahoo 호환)
        sess2 = requests.Session()
        sess2.headers.update(UA)
        j = yahoo(None, sess2, None)
        sess, crumb = sess2, None
    px = j["quote"]["regularMarketPrice"]
    today_et = datetime.now(ET).date()
    exps = []
    for e in sorted(j.get("expirationDates") or []):
        d_et = datetime.fromtimestamp(e, tz=timezone.utc).astimezone(ET).date()
        if d_et.isoformat() >= today_et.isoformat():
            exps.append(e)
        if len(exps) == 2:
            break
    if not exps:  # fallback: front 2 as-is
        exps = sorted(j.get("expirationDates") or [])[:2]
    first = (j.get("expirationDates") or [None])[0]
    rows = []
    for e in exps:
        d = j if e == first else yahoo(e, sess, crumb)
        exp = datetime.fromtimestamp(e, tz=timezone.utc).astimezone(ET).date().isoformat()
        opts = d.get("options", [{}])[0]
        for k, side in (("calls", "call"), ("puts", "put")):
            rows += [norm_t(o, exp, px, ts, "yahoo", side) for o in (opts.get(k) or [])]
    rows = pick(rows, px)
    return _dedup(rows)


def collect():
    ts = datetime.now(timezone.utc).isoformat()
    if os.getenv("TRADIER_TOKEN"):
        try:
            px = tradier_px()
            rows = []
            for e in tradier_exp():
                rows += [norm_t(o, e, px, ts, "tradier") for o in tradier_chain(e)]
            rows = pick(rows, px) if px else rows
            rows = _dedup(rows)
            if rows:
                return rows
        except Exception as ex:
            print(f"warn: tradier fail -> cboe ({ex})", file=sys.stderr)
    sess = requests.Session()
    sess.headers.update(UA)
    try:
        rows = collect_cboe(ts, sess)
        if rows:
            return rows
    except Exception as ex:
        print(f"warn: cboe fail -> yahoo ({ex})", file=sys.stderr)
    return collect_yahoo(ts)


def save(rows):
    day = datetime.now(ET).date().isoformat()
    p = OUT / f"{day}.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    seen = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                seen.add((d.get("expiration"), d.get("strike"), d.get("type")))
    new = [r for r in rows if (r["expiration"], r["strike"], r["type"]) not in seen]
    if new:
        with open(p, "a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r) + "\n")
    return len(new), len(rows)


def main():
    ap = argparse.ArgumentParser(description="XLF 옵션체인 수집 (Tradier 우선, Yahoo 폴백)")
    ap.add_argument("--loop", action="store_true", help="15분 간격 반복")
    a = ap.parse_args()
    while True:
        try:
            n, t = save(collect())
            print(f"{datetime.now(ET):%Y-%m-%d %H:%M} ET +{n}/{t}")
        except Exception as ex:
            print(f"warn: {ex}", file=sys.stderr)
        if not a.loop:
            break
        time.sleep(900)


if __name__ == "__main__":
    main()
