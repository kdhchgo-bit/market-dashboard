"""NVDA 옵션 체인 수집기 (딜레이, 근월2개 ATM±10).

원천1(우선) Tradier delayed:
  GET https://api.tradier.com/v1/markets/options/expirations?symbol=NVDA
  + GET https://api.tradier.com/v1/markets/options/chains
  + GET https://api.tradier.com/v1/markets/quotes?symbols=NVDA (ATM 기준가).
  인증: env TRADIER_TOKEN.
원천2 CBOE delayed:
  GET https://cdn.cboe.com/api/global/delayed_quotes/options/NVDA.json
  (키 없음, 15분 지연. 429/차단 시 원천3으로 폴백).
원천3(폴백) Yahoo delayed (crumb 필요):
  GET https://query2.finance.yahoo.com/v7/finance/options/NVDA?crumb=...
  (세션 쿠키: https://fc.yahoo.com 선행 요청 +
   https://query1.finance.yahoo.com/v1/test/getcrumb).
용법: python 91_NVDA_CHAIN.py (1회) / python 91_NVDA_CHAIN.py --loop (15분 폴링).
저장: <this_dir>/data/NVDA_CHAIN/YYYY-MM-DD.jsonl
  (계약스키마 동일, dedup=계약(만기|행사가|타입, 배치내 1건)
   +스냅샷(계약|bid|ask|last|volume|open_interest, 파일내 중복금지)).
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

SYMBOL = "NVDA"
OUT = Path(__file__).parent / "data" / "NVDA_CHAIN"
N_EXP = 2
WING = 10
UA = {"User-Agent": "Mozilla/5.0"}
T_EXP = "https://api.tradier.com/v1/markets/options/expirations"
T_CHAIN = "https://api.tradier.com/v1/markets/options/chains"
T_QUOTE = "https://api.tradier.com/v1/markets/quotes"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/NVDA.json"
Y_URL = "https://query2.finance.yahoo.com/v7/finance/options/NVDA"
Y_FC = "https://fc.yahoo.com"
Y_CRUMB = "https://query1.finance.yahoo.com/v1/test/getcrumb"


def utcnow():
    return datetime.now(timezone.utc)


def theaders():
    t = os.environ.get("TRADIER_TOKEN", "")
    if not t:
        return None
    return {"Authorization": f"Bearer {t}", "Accept": "application/json"}


def atm_filter(strikes, px, n=10):
    s = sorted(strikes)
    i = min(range(len(s)), key=lambda k: abs(s[k] - px))
    return set(s[max(0, i - n):i + n + 1])


def norm(source, px, exp, strike, otype, osym, bid, ask, last, vol, oi, iv):
    now = utcnow().isoformat().replace("+00:00", "Z")
    return {
        "source": source, "symbol": SYMBOL, "underlying_price": px,
        "expiration": exp, "strike": strike, "option_type": otype,
        "option_symbol": osym, "bid": bid, "ask": ask, "last": last,
        "volume": vol, "open_interest": oi, "implied_volatility": iv,
        "received_at_utc": now, "status": "delayed",
    }


def tradier_snapshot(h):
    r = requests.get(
        T_EXP,
        headers=h,
        params={"symbol": SYMBOL, "includeAllRoots": "true", "strikes": "false"},
        timeout=30,
    )
    r.raise_for_status()
    exps = (r.json().get("expirations") or {}).get("date") or []
    if isinstance(exps, str):
        exps = [exps]
    exps = sorted(exps)[:N_EXP]
    q = requests.get(
        T_QUOTE, headers=h, params={"symbols": SYMBOL, "greeks": "false"}, timeout=30
    )
    q.raise_for_status()
    qq = q.json().get("quotes", {}).get("quote", {})
    if isinstance(qq, list):
        qq = qq[0]
    px = float(qq.get("last") or qq.get("close") or 0)
    out = []
    for exp in exps:
        c = requests.get(
            T_CHAIN,
            headers=h,
            params={"symbol": SYMBOL, "expiration": exp, "greeks": "false"},
            timeout=30,
        )
        c.raise_for_status()
        opts = ((c.json().get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        keep = atm_filter([float(o["strike"]) for o in opts], px, WING)
        for o in opts:
            st = float(o.get("strike"))
            if st not in keep:
                continue
            out.append(norm(
                "tradier-delayed", px, exp, st,
                str(o.get("option_type", "")).lower(),
                o.get("symbol"), o.get("bid"), o.get("ask"), o.get("last"),
                o.get("volume"), o.get("open_interest"),
                (o.get("greeks") or {}).get("mid_iv") if o.get("greeks") else None,
            ))
    return out


def parse_cboe_symbol(sym):
    m = re.match(r"^(.+?)(\d{6})([CP])(\d{8})$", sym or "")
    if not m:
        return None
    _, ymd, cp, strike8 = m.groups()
    exp = f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}"
    return exp, int(strike8) / 1000.0, ("call" if cp == "C" else "put")


def cboe_snapshot():
    r = requests.get(CBOE_URL, headers=UA, timeout=30)
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or {}
    published = payload.get("timestamp")
    if not published:
        raise ValueError("Cboe snapshot timestamp missing")
    stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    px = float(data.get("current_price") or data.get("close") or 0)
    opts = data.get("options") or []
    by_exp = {}
    for o in opts:
        p = parse_cboe_symbol(o.get("option"))
        if not p:
            continue
        by_exp.setdefault(p[0], []).append((p, o))
    if not by_exp:
        raise RuntimeError("cboe empty")
    out = []
    for exp in sorted(by_exp)[:N_EXP]:
        items = by_exp[exp]
        keep = atm_filter([p[1] for p, _ in items], px, WING)
        for (pexp, strike, otype), o in items:
            if strike not in keep:
                continue
            out.append(norm(
                "cboe-delayed", px, pexp, strike, otype,
                o.get("option"), o.get("bid"), o.get("ask"),
                o.get("last_trade_price"), o.get("volume"),
                o.get("open_interest"), o.get("iv"),
            ))
    if not out:
        raise RuntimeError("cboe empty")
    for contract in out:
        contract["snapshot_at_utc"] = stamp.astimezone(timezone.utc).isoformat()
    return out


def yahoo_snapshot():
    s = requests.Session()
    s.headers.update(UA)
    s.get(Y_FC, timeout=20)
    crumb = s.get(Y_CRUMB, timeout=20).text.strip()
    if not crumb or "<" in crumb:
        raise RuntimeError("yahoo crumb empty")

    def get(url, params=None):
        p = dict(params or {})
        p["crumb"] = crumb
        r = s.get(url, params=p, timeout=30)
        if r.status_code == 401:
            nc = s.get(Y_CRUMB, timeout=20).text.strip()
            if not nc or "<" in nc:
                raise RuntimeError("yahoo crumb refresh failed")
            p["crumb"] = nc
            r = s.get(url, params=p, timeout=30)
        r.raise_for_status()
        return r

    r = get(Y_URL)
    res = (r.json().get("optionChain") or {}).get("result") or []
    if not res:
        raise RuntimeError("yahoo empty")
    res = res[0]
    px = float((res.get("quote") or {}).get("regularMarketPrice") or 0)
    dates = sorted(res.get("expirationDates") or [])[:N_EXP]
    out = []

    def add_chain(node, exp_epoch):
        exp = datetime.fromtimestamp(exp_epoch, tz=timezone.utc).date().isoformat()
        raw_calls = node.get("calls") or []
        raw_puts = node.get("puts") or []
        keep = atm_filter(
            [float(x["strike"]) for x in raw_calls + raw_puts], px, WING
        )
        for x in raw_calls:
            if float(x["strike"]) in keep:
                out.append(norm(
                    "yahoo-delayed", px, exp, float(x["strike"]), "call",
                    x.get("contractSymbol"), x.get("bid"), x.get("ask"),
                    x.get("lastPrice"), x.get("volume"), x.get("openInterest"),
                    x.get("impliedVolatility"),
                ))
        for x in raw_puts:
            if float(x["strike"]) in keep:
                out.append(norm(
                    "yahoo-delayed", px, exp, float(x["strike"]), "put",
                    x.get("contractSymbol"), x.get("bid"), x.get("ask"),
                    x.get("lastPrice"), x.get("volume"), x.get("openInterest"),
                    x.get("impliedVolatility"),
                ))

    add_chain(res.get("options", [{}])[0], dates[0])
    for d in dates[1:]:
        rr = get(Y_URL, params={"date": d})
        rs = (rr.json().get("optionChain") or {}).get("result") or []
        if rs and rs[0].get("options"):
            add_chain(rs[0]["options"][0], d)
    return out


def snap_key(d):
    return (d.get("snapshot_at_utc"), d.get("expiration"), d.get("strike"), d.get("option_type"),
            d.get("bid"), d.get("ask"), d.get("last"),
            d.get("volume"), d.get("open_interest"))


def seen_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                s.add(snap_key(json.loads(line)))
            except Exception:
                pass
    return s


def save(contracts):
    if not contracts:
        return 0
    uniq = []
    seen_batch = set()
    for c in contracts:
        k = (c["expiration"], c["strike"], c["option_type"])
        if k in seen_batch:
            continue
        seen_batch.add(k)
        uniq.append(c)
    path = OUT / f"{utcnow().date().isoformat()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = seen_keys(path)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for c in uniq:
            k = snap_key(c)
            if k in seen:
                continue
            seen.add(k)
            f.write(json.dumps(c) + "\n")
            n += 1
    return n


def once():
    h = theaders()
    if h:
        try:
            cs = tradier_snapshot(h)
            print(f"tradier contracts={len(cs)} new={save(cs)}")
            return
        except Exception as e:
            print(f"tradier fail, cboe fallback: {e}", file=sys.stderr)
    try:
        cs = cboe_snapshot()
        print(f"cboe contracts={len(cs)} new={save(cs)}")
        return
    except Exception as e:
        print(f"cboe fail, yahoo fallback: {e}", file=sys.stderr)
    cs = yahoo_snapshot()
    print(f"yahoo contracts={len(cs)} new={save(cs)}")


def loop():
    while True:
        try:
            once()
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
        time.sleep(900)


def main():
    ap = argparse.ArgumentParser(description="NVDA option chain (front-2, ATM+-10)")
    ap.add_argument("--loop", action="store_true", help="repeat every 15min")
    a = ap.parse_args()
    if a.loop:
        loop()
    else:
        once()


if __name__ == "__main__":
    main()
