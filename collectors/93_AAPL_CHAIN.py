"""AAPL 옵션 체인 수집기 (딜레이, 근월2개 ATM±10).
우선: Tradier (env TRADIER_TOKEN) — expirations→chains 2개→ATM±10.
차선: CBOE delayed (https://cdn.cboe.com/api/global/delayed_quotes/options/AAPL.json, 15분 딜레이).
폴백: Yahoo Finance v7 options (crumb+쿠키 방식, 15분 딜레이).
용법: python 93_AAPL_CHAIN.py (1회) / python 93_AAPL_CHAIN.py --loop (15분 반복).
저장: <this_dir>/data/AAPL_CHAIN/YYYY-MM-DD.jsonl (당일 스냅샷포함 중복금지).
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

SYMBOL = "AAPL"
OUT = Path(__file__).parent / "data" / "AAPL_CHAIN"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 30
LOOP_INTERVAL = 900
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/AAPL.json"
YH1 = "https://query1.finance.yahoo.com/v7/finance/options"
YH2 = "https://query2.finance.yahoo.com/v7/finance/options"


def utcnow():
    return datetime.now(timezone.utc)


def atm_window(strikes, px, n=10):
    s = sorted(set(strikes))
    i = min(range(len(s)), key=lambda k: abs(s[k] - px))
    return set(s[max(0, i - n):i + n + 1])


def tradier():
    tok = os.environ.get("TRADIER_TOKEN", "")
    if not tok:
        return None
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    try:
        r = requests.get("https://api.tradier.com/v1/markets/quotes",
                         headers=h, params={"symbols": SYMBOL}, timeout=TIMEOUT)
        r.raise_for_status()
        q = r.json()["quotes"]["quote"]
        if isinstance(q, list):
            q = q[0]
        px = float(q.get("last") or 0)
        r = requests.get("https://api.tradier.com/v1/markets/options/expirations",
                         headers=h, params={"symbol": SYMBOL}, timeout=TIMEOUT)
        r.raise_for_status()
        exps = r.json()["expirations"]["date"] or []
        exps = [exps] if isinstance(exps, str) else list(exps)[:2]
        opts = []
        for e in exps:
            rr = requests.get("https://api.tradier.com/v1/markets/options/chains",
                              headers=h, params={"symbol": SYMBOL, "expiration": e},
                              timeout=TIMEOUT)
            rr.raise_for_status()
            o = (rr.json().get("options") or {}).get("option") or []
            o = [o] if isinstance(o, dict) else o
            for c in o:
                opts.append({
                    "exp": c.get("expiration_date"),
                    "strike": float(c.get("strike")),
                    "type": "C" if str(c.get("option_type")) == "call" else "P",
                    "bid": c.get("bid"), "ask": c.get("ask"), "last": c.get("last"),
                    "vol": c.get("volume"), "oi": c.get("open_interest"),
                })
        keep = atm_window([o["strike"] for o in opts], px)
        return px, [o for o in opts if o["strike"] in keep], "tradier-delayed"
    except Exception as e:
        print(f"tradier fail: {e}", file=sys.stderr)
        return None


def _parse_occ(opt):
    # OCC 심볼: ROOT + YYMMDD(6) + C/P(1) + strike*1000(8), 예 AAPL261016C00350000
    s = str(opt or "").strip()
    m = re.match(r"^(.+?)(\d{6})([CP])(\d{8})$", s)
    if not m:
        return None, None, None
    ymd, cp, sk = m.group(2), m.group(3), m.group(4)
    e = f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}"
    try:
        k = int(sk) / 1000.0
    except Exception:
        return None, None, None
    return e, k, ("C" if cp == "C" else "P")


def cboe():
    try:
        last = None
        r = None
        for _ in range(3):
            try:
                r = requests.get(CBOE_URL, headers=UA, timeout=TIMEOUT)
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
            print(f"cboe fail: {last}", file=sys.stderr)
            return None
        j = r.json()
        data = j.get("data", j) if isinstance(j, dict) else {}
        try:
            px = float(data.get("current_price") or data.get("close") or data.get("price") or 0)
        except Exception:
            px = 0.0
        raw = data.get("options", []) or []
        groups = {}
        for o in raw:
            e, k, t = _parse_occ(o.get("option"))
            if e is None:
                continue
            groups.setdefault(e, []).append((k, t, o))
        if not groups:
            raise ValueError("CBOE returned no contracts")
        out = []
        for e in sorted(groups.keys())[:2]:
            legs = groups[e]
            strikes = sorted({k for k, _, _ in legs})
            if not strikes or not px:
                keep = set(strikes)
            else:
                keep = atm_window(strikes, px)
            for k, t, o in legs:
                if k not in keep:
                    continue
                last_px = o.get("last_trade_price")
                if last_px is None:
                    last_px = o.get("last")
                out.append({
                    "exp": e, "strike": float(k), "type": t,
                    "bid": o.get("bid"), "ask": o.get("ask"),
                    "last": last_px, "vol": o.get("volume"),
                    "oi": o.get("open_interest"),
                })
        if not out:
            raise ValueError("CBOE ATM slice empty")
        return px, out, "cboe-delayed"
    except Exception as e:
        print(f"cboe fail: {e}", file=sys.stderr)
        return None


def _yahoo_crumb(sess):
    try:
        sess.get("https://fc.yahoo.com", headers=UA, timeout=TIMEOUT)
    except Exception:
        pass
    for u in ("https://query1.finance.yahoo.com/v1/test/getcrumb",
              "https://query2.finance.yahoo.com/v1/test/getcrumb"):
        try:
            r = sess.get(u, headers=UA, timeout=TIMEOUT)
            t = (r.text or "").strip()
            if r.status_code == 200 and t and "<" not in t and "crumb" not in t.lower():
                return t
        except Exception:
            continue
    return ""


def _yahoo_get(sess, crumb, date=None):
    p = {"crumb": crumb} if crumb else {}
    if date is not None:
        p["date"] = date
    last = None
    for base in (YH1, YH2):
        try:
            r = sess.get(f"{base}/{SYMBOL}", headers=UA, params=p, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()["optionChain"]["result"][0]
        except Exception as e:
            last = e
    raise RuntimeError(f"yahoo failed: {last}")


def yahoo():
    try:
        sess = requests.Session()
        sess.headers.update(UA)
        crumb = _yahoo_crumb(sess)
        res = _yahoo_get(sess, crumb)
        px = float(res["quote"]["regularMarketPrice"])
        dates = sorted(res.get("expirationDates") or [])[:2]
        out = []
        for idx, d in enumerate(dates):
            if idx == 0 and (res.get("options") or [{}])[0].get("calls") is not None:
                rr0 = res
            else:
                rr0 = _yahoo_get(sess, crumb, d)
            ch = (rr0.get("options") or [{}])[0]
            exp = datetime.fromtimestamp(d, tz=timezone.utc).strftime("%Y-%m-%d")
            chains = []
            for side, t in (("calls", "C"), ("puts", "P")):
                for c in ch.get(side) or []:
                    chains.append((c, t))
            keep = atm_window([float(c.get("strike")) for c, _ in chains], px)
            for c, t in chains:
                try:
                    k = float(c.get("strike"))
                except Exception:
                    continue
                if k not in keep:
                    continue
                out.append({
                    "exp": exp, "strike": k,
                    "type": t, "bid": c.get("bid"), "ask": c.get("ask"),
                    "last": c.get("lastPrice"), "vol": c.get("volume"),
                    "oi": c.get("openInterest"),
                })
        if not out:
            raise ValueError("yahoo ATM slice empty")
        return px, out, "yahoo-delayed"
    except Exception as e:
        print(f"yahoo fail: {e}", file=sys.stderr)
        return None


def snap_key(j):
    # 스냅샷포함 dedup: 계약키 + 호가/거래량 스냅샷이 모두 같을 때만 중복.
    # 같은 계약의 호가 갱신은 신규 스냅샷으로 추가 저장된다.
    return (f"{j.get('exp')}|{j.get('strike')}|{j.get('type')}|"
            f"{j.get('bid')}|{j.get('ask')}|{j.get('last')}|"
            f"{j.get('vol')}|{j.get('oi')}")


def seen_keys(path):
    s = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                j = json.loads(line)
                s.add(snap_key(j))
            except Exception:
                pass
    return s


def once():
    got = tradier() or cboe() or yahoo()
    if not got:
        print("all sources failed", file=sys.stderr)
        return 0
    px, opts, src = got
    now = utcnow().isoformat().replace("+00:00", "Z")
    day = utcnow().strftime("%Y-%m-%d")
    path = OUT / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = seen_keys(path)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for o in sorted(opts, key=lambda x: (x["exp"], x["strike"], x["type"])):
            k = snap_key(o)
            if k in seen:
                continue
            rec = {"source": src, "symbol": SYMBOL, "underlying": px,
                   "received_at_utc": now, **o}
            f.write(json.dumps(rec) + "\n")
            seen.add(k)
            n += 1
    print(f"src={src} px={px} scanned={len(opts)} new={n} file={path}")
    return n


def main():
    ap = argparse.ArgumentParser(description="AAPL 옵션 체인 수집기 (Tradier 우선, CBOE/Yahoo 폴백)")
    ap.add_argument("--loop", action="store_true", help="15분마다 반복")
    a = ap.parse_args()
    if not a.loop:
        once()
        return
    while True:
        try:
            once()
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
