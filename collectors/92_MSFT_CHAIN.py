"""MSFT 옵션체인 수집기 (딜레이, 근월2개 ATM±10).

원천(우선순위):
 1) Tradier: GET https://api.tradier.com/v1/markets/options/expirations
    + /chains (인증 env: TRADIER_TOKEN 또는 TRADIER_API_KEY, Bearer)
 2) CBOE 딜레이: GET https://cdn.cboe.com/api/global/delayed_quotes/options/MSFT.json (키 불필요, 15분 딜레이)
 3) Yahoo 폴백(crumb): session https://fc.yahoo.com 쿠키 + /v1/test/getcrumb
    + https://query2.finance.yahoo.com/v7/finance/options/MSFT[?date=epoch&crumb=...]
심볼: MSFT. 대상: 만기 오름차순 앞 2개, 각 만기 ATM±10 행사가(콜+풋).
동작: 1회 수집 + --loop 시 15분(900초) 간격 반복.
저장: Path(__file__).parent/"data"/"MSFT_CHAIN"/YYYY-MM-DD.jsonl (ET 일자별, 스냅샷포함 중복금지).
레코드: source/symbol/expiry/strike/option_type/bid/ask/last/volume/open_interest/underlying/received_at_utc.
사용: python 92_MSFT_CHAIN.py [--loop]
의존: 표준lib + requests.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "MSFT"
ET = ZoneInfo("America/New_York")
BASE_DIR = Path(__file__).parent / "data" / "MSFT_CHAIN"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 30
LOOP_INTERVAL = 900
N_EXP = 2
WING = 10
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/MSFT.json"
YAHOO_URL = "https://query2.finance.yahoo.com/v7/finance/options/MSFT"


def _today_file():
    return BASE_DIR / f"{datetime.now(ET):%Y-%m-%d}.jsonl"


def _snap_key(d):
    # 스냅샷포함 dedup: 계약키 + 시세 스냅샷이 모두 같을 때만 중복으로 간주.
    # 같은 계약의 호가/거래량 갱신은 신규 스냅샷으로 추가 저장된다.
    return (str(d.get("expiry")), str(d.get("strike")), str(d.get("option_type")),
            str(d.get("bid")), str(d.get("ask")), str(d.get("last")),
            str(d.get("volume")), str(d.get("open_interest")))


def _seen(path):
    s = set()
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                s.add(_snap_key(d))
            except Exception:
                pass
    return s


def _tradier_key():
    return os.environ.get("TRADIER_TOKEN") or os.environ.get("TRADIER_API_KEY") or ""


def _tradier_collect():
    tok = _tradier_key()
    if not tok:
        raise RuntimeError("no tradier token")
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    r = requests.get("https://api.tradier.com/v1/markets/options/expirations",
                     headers=h, params={"symbol": SYMBOL, "includeAllRoots": "true"}, timeout=TIMEOUT)
    r.raise_for_status()
    exps = ((r.json().get("expirations") or {}).get("date")) or []
    if isinstance(exps, str):
        exps = [exps]
    exps = sorted(exps)[:N_EXP]
    q = requests.get("https://api.tradier.com/v1/markets/quotes", headers=h,
                     params={"symbols": SYMBOL, "greeks": "false"}, timeout=TIMEOUT)
    q.raise_for_status()
    qq = ((q.json().get("quotes") or {}).get("quote")) or {}
    if isinstance(qq, list):
        qq = qq[0] if qq else {}
    und = float(qq.get("last") or qq.get("close") or 0) or 0.0
    out = []
    for e in exps:
        c = requests.get("https://api.tradier.com/v1/markets/options/chains", headers=h,
                         params={"symbol": SYMBOL, "expiration": e, "greeks": "false"}, timeout=TIMEOUT)
        c.raise_for_status()
        opts = ((c.json().get("options") or {}).get("option")) or []
        if isinstance(opts, dict):
            opts = [opts]
        strikes = sorted({float(o["strike"]) for o in opts if o.get("strike") is not None})
        atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - und)) if strikes else 0
        keep = set(strikes[max(0, atm - WING):atm + WING + 1])
        for o in opts:
            try:
                k = float(o["strike"])
            except Exception:
                continue
            if k not in keep:
                continue
            out.append({"source": "tradier-delayed", "symbol": SYMBOL, "expiry": e,
                        "strike": k, "option_type": (o.get("option_type") or "").lower() or None,
                        "option_symbol": o.get("symbol"), "bid": o.get("bid"), "ask": o.get("ask"),
                        "last": o.get("last"), "volume": o.get("volume"),
                        "open_interest": o.get("open_interest"), "underlying": und})
    return out


def _parse_occ(opt):
    # OCC 심볼: ROOT + YYMMDD(6) + C/P(1) + strike*1000(8), 예 MSFT261111C00420000
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
    return e, k, ("call" if cp == "C" else "put")


def _cboe_collect():
    r = requests.get(CBOE_URL, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j) if isinstance(j, dict) else {}
    try:
        und = float(data.get("current_price") or data.get("close") or data.get("price") or 0) or 0.0
    except Exception:
        und = 0.0
    opts = data.get("options", []) or []
    # 만기별 그룹핑
    groups = {}
    for o in opts:
        e, k, typ = _parse_occ(o.get("option"))
        if e is None:
            continue
        groups.setdefault(e, []).append((k, typ, o))
    if not groups:
        raise ValueError("CBOE returned no contracts")
    out = []
    for e in sorted(groups.keys())[:N_EXP]:
        legs = groups[e]
        strikes = sorted({k for k, _, _ in legs})
        if not strikes or not und:
            keep = set(strikes)
        else:
            atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - und))
            keep = set(strikes[max(0, atm - WING):atm + WING + 1])
        for k, typ, o in legs:
            if k not in keep:
                continue
            last = o.get("last_trade_price")
            if last is None:
                last = o.get("last")
            iv = o.get("iv")
            if iv is None:
                iv = o.get("implied_volatility")
            out.append({"source": "cboe-delayed", "symbol": SYMBOL, "expiry": e,
                        "strike": k, "option_type": typ,
                        "option_symbol": o.get("option"), "bid": o.get("bid"), "ask": o.get("ask"),
                        "last": last, "volume": o.get("volume"),
                        "open_interest": o.get("open_interest"), "underlying": und})
    if not out:
        raise ValueError("CBOE ATM slice empty")
    return out


def _yahoo_crumb(sess):
    try:
        sess.get("https://fc.yahoo.com", headers=UA, timeout=TIMEOUT)
    except Exception:
        pass
    r = sess.get("https://query2.finance.yahoo.com/v1/test/getcrumb", headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    crumb = r.text.strip()
    if not crumb or "crumb" in crumb.lower() and len(crumb) > 100:
        raise ValueError("bad crumb")
    return crumb


def _yahoo_collect():
    sess = requests.Session()
    try:
        crumb = _yahoo_crumb(sess)
    except Exception as ex:
        raise RuntimeError(f"yahoo crumb fail: {ex}")
    params = {"crumb": crumb} if crumb else {}
    r0 = sess.get(YAHOO_URL, headers=UA, params=params, timeout=TIMEOUT)
    r0.raise_for_status()
    j = r0.json().get("optionChain", {}).get("result", [{}])[0]
    epochs = sorted(j.get("expirationDates") or [])[:N_EXP]
    if not epochs:
        raise ValueError("yahoo no expirations")
    und = float((j.get("quote") or {}).get("regularMarketPrice") or 0) or 0.0
    out = []
    for ep in epochs:
        e = datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%d")
        if ep == epochs[0] and (j.get("options") or [{}])[0].get("calls") is not None:
            d = j
        else:
            p = dict(params)
            p["date"] = ep
            d = sess.get(YAHOO_URL, headers=UA, params=p, timeout=TIMEOUT).json()["optionChain"]["result"][0]
        und = float((d.get("quote") or {}).get("regularMarketPrice") or und) or und
        for key, typ in (("calls", "call"), ("puts", "put")):
            chain = (d.get("options") or [{}])[0].get(key) or []
            strikes = sorted({float(c["strike"]) for c in chain if c.get("strike") is not None})
            atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - und)) if strikes else 0
            keep = set(strikes[max(0, atm - WING):atm + WING + 1])
            for c in chain:
                try:
                    k = float(c["strike"])
                except Exception:
                    continue
                if k not in keep:
                    continue
                out.append({"source": "yahoo-delayed", "symbol": SYMBOL, "expiry": e,
                            "strike": k, "option_type": typ,
                            "option_symbol": c.get("contractSymbol"), "bid": c.get("bid"),
                            "ask": c.get("ask"), "last": c.get("lastPrice"),
                            "volume": c.get("volume"), "open_interest": c.get("openInterest"),
                            "underlying": und})
    if not out:
        raise ValueError("yahoo ATM slice empty")
    return out


def collect_once():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = _tradier_collect()
    except Exception as ex1:
        print(f"tradier fail, cboe fallback: {ex1}", file=sys.stderr)
        try:
            rows = _cboe_collect()
        except Exception as ex2:
            print(f"cboe fail, yahoo(crumb) fallback: {ex2}", file=sys.stderr)
            rows = _yahoo_collect()
    path = _today_file()
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    seen = _seen(path)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            key = _snap_key(r)
            if key in seen:
                continue
            r["received_at_utc"] = now
            f.write(json.dumps(r) + "\n")
            seen.add(key)
            n += 1
    print(f"rows={len(rows)} saved={n} file={path.name}")
    return n


def main():
    ap = argparse.ArgumentParser(description="MSFT option chain (front-2, ATM+-10)")
    ap.add_argument("--loop", action="store_true", help="repeat every 15min")
    a = ap.parse_args()
    collect_once()
    while a.loop:
        time.sleep(LOOP_INTERVAL)
        try:
            collect_once()
        except Exception as ex:
            print("loop error:", ex, file=sys.stderr)


if __name__ == "__main__":
    main()
