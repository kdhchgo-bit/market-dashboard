"""META option chain collector (delayed, front 2 expiries ATM+-10).

Tradier first (env TRADIER_TOKEN), CBOE delayed JSON second, Yahoo (crumb) fallback.
One-shot + --loop 15min.
Save: data/META_CHAIN/YYYY-MM-DD.jsonl (dedup by contract+snapshot).
Usage: python 96_META_CHAIN.py [--loop] [--strikes N] [--symbol META]
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT = Path(__file__).parent / "data" / "META_CHAIN"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/META.json"


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _now():
    return dt.datetime.now(dt.timezone.utc)


def yahoo_session(sym):
    s = requests.Session()
    s.headers.update(UA)
    for u in ("https://fc.yahoo.com", f"https://finance.yahoo.com/quote/{sym}"):
        try:
            s.get(u, timeout=15)
            break
        except Exception:
            continue
    crumb = None
    for u in ("https://query1.finance.yahoo.com/v1/test/getcrumb",
              "https://query2.finance.yahoo.com/v1/test/getcrumb"):
        try:
            r = s.get(u, timeout=15)
            if r.ok and r.text and len(r.text.strip()) < 100:
                crumb = r.text.strip()
                break
        except Exception:
            continue
    return s, crumb


def yahoo_chain(sym):
    s, crumb = yahoo_session(sym)
    base = f"https://query2.finance.yahoo.com/v7/finance/options/{sym}"

    def _get(params=None):
        p = dict(params or {})
        if crumb:
            p["crumb"] = crumb
        r = s.get(base, params=p, timeout=20)
        r.raise_for_status()
        return r.json()

    ts = _now().isoformat(timespec="seconds")
    expir = _get()
    res = expir["optionChain"]["result"][0]
    und = float(res["quote"]["regularMarketPrice"])
    out = []
    for exp_ts in res["expirationDates"][:2]:
        d = _get({"date": exp_ts})
        chain = d["optionChain"]["result"][0]["options"][0]
        exp = dt.datetime.fromtimestamp(
            chain["expirationDate"], dt.timezone.utc
        ).date().isoformat()
        for side in ("calls", "puts"):
            for c in chain.get(side, []):
                out.append(
                    {
                        "ts": ts,
                        "symbol": sym,
                        "expiry": exp,
                        "strike": float(c["strike"]),
                        "right": "C" if side == "calls" else "P",
                        "contract": c.get("contractSymbol"),
                        "bid": c.get("bid"),
                        "ask": c.get("ask"),
                        "last": c.get("lastPrice"),
                        "volume": c.get("volume"),
                        "oi": c.get("openInterest"),
                        "iv": c.get("impliedVolatility"),
                        "underlying": und,
                        "source": "yahoo",
                    }
                )
    if not out:
        raise ValueError("yahoo returned no contracts")
    return out


def cboe_chain(sym):
    ts = _now().isoformat(timespec="seconds")
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    j = r.json()
    data = j.get("data", j) if isinstance(j, dict) else {}
    if not isinstance(data, dict):
        raise ValueError("cboe bad payload")
    und = None
    for k in ("current_price", "close", "price", "underlying_price"):
        v = _num(data.get(k))
        if v is not None:
            und = v
            break
    opts = data.get("options", [])
    out = []
    if opts and isinstance(opts[0], dict) and any(
        k in opts[0] for k in ("calls", "puts", "expiration_date")
    ):
        groups = sorted(opts, key=lambda g: str(g.get("expiration_date", "")))[:2]
        for grp in groups:
            exp = str(grp.get("expiration_date", ""))[:10]
            if not exp:
                continue
            for side, right in (("calls", "C"), ("puts", "P")):
                for o in grp.get(side) or []:
                    strike = _num(o.get("strike_price") if o.get("strike_price") is not None else o.get("strike"))
                    if strike is None:
                        continue
                    out.append(
                        {
                            "ts": ts,
                            "symbol": sym,
                            "expiry": exp,
                            "strike": strike,
                            "right": right,
                            "contract": o.get("option_symbol") or o.get("symbol") or o.get("contract_symbol"),
                            "bid": _num(o.get("bid")),
                            "ask": _num(o.get("ask")),
                            "last": _num(o.get("last_trade_price") if o.get("last_trade_price") is not None else o.get("last")),
                            "volume": o.get("volume"),
                            "oi": o.get("open_interest") if o.get("open_interest") is not None else o.get("openInterest"),
                            "iv": _num(o.get("iv") if o.get("iv") is not None else o.get("implied_volatility")),
                            "underlying": und,
                            "source": "cboe",
                        }
                    )
    else:
        tmp = []
        for o in opts:
            if not isinstance(o, dict):
                continue
            t = str(o.get("option_type") or o.get("type") or "C").upper()
            tmp.append(
                {
                    "expiry": str(o.get("expiration_date") or o.get("expiry") or "")[:10],
                    "strike": _num(o.get("strike_price") if o.get("strike_price") is not None else o.get("strike")),
                    "right": "C" if t.startswith("C") else "P",
                    "contract": o.get("option_symbol") or o.get("symbol"),
                    "bid": _num(o.get("bid")),
                    "ask": _num(o.get("ask")),
                    "last": _num(o.get("last")),
                    "volume": o.get("volume"),
                    "oi": o.get("open_interest"),
                    "iv": _num(o.get("iv") if o.get("iv") is not None else o.get("implied_volatility")),
                }
            )
        tmp = [x for x in tmp if x["expiry"] and x["strike"] is not None]
        for exp in sorted({x["expiry"] for x in tmp})[:2]:
            for x in tmp:
                if x["expiry"] != exp:
                    continue
                out.append(
                    {
                        "ts": ts, "symbol": sym, "expiry": x["expiry"], "strike": x["strike"],
                        "right": x["right"], "contract": x["contract"], "bid": x["bid"],
                        "ask": x["ask"], "last": x["last"], "volume": x["volume"],
                        "oi": x["oi"], "iv": x["iv"], "underlying": und, "source": "cboe",
                    }
                )
    if not out:
        raise ValueError("cboe returned no contracts")
    if und is None:
        px = sorted(x["strike"] for x in out)[len(out) // 2]
        for x in out:
            x["underlying"] = px
    return out


def tradier_chain(sym, token):
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    ts = _now().isoformat(timespec="seconds")
    e = requests.get("https://api.tradier.com/v1/markets/options/expirations",
                     params={"symbol": sym}, headers=h, timeout=20)
    e.raise_for_status()
    dates = e.json()["expirations"]["date"]
    if isinstance(dates, str):
        dates = [dates]
    out = []
    q = requests.get("https://api.tradier.com/v1/markets/quotes",
                     params={"symbols": sym}, headers=h, timeout=20)
    q.raise_for_status()
    qq = q.json()["quotes"]["quote"]
    und = float(qq["last"] if isinstance(qq, dict) else qq[0]["last"])
    for exp in dates[:2]:
        c = requests.get(
            "https://api.tradier.com/v1/markets/options/chains",
            params={"symbol": sym, "expiration": exp},
            headers=h,
            timeout=20,
        )
        c.raise_for_status()
        opts = c.json()["options"]["option"]
        for o in opts:
            out.append(
                {
                    "ts": ts,
                    "symbol": sym,
                    "expiry": exp,
                    "strike": float(o["strike"]),
                    "right": "C" if o["option_type"] == "call" else "P",
                    "contract": o.get("symbol"),
                    "bid": o.get("bid"),
                    "ask": o.get("ask"),
                    "last": o.get("last"),
                    "volume": o.get("volume"),
                    "oi": o.get("open_interest"),
                    "iv": (o.get("greeks") or {}).get("mid_iv"),
                    "underlying": und,
                    "source": "tradier",
                }
            )
    if not out:
        raise ValueError("tradier returned no contracts")
    return out


def atm_filter(rows, n):
    if not rows:
        return rows
    und = rows[0]["underlying"]
    if und is None:
        strikes_all = sorted(r["strike"] for r in rows)
        und = strikes_all[len(strikes_all) // 2]
    exps = sorted({r["expiry"] for r in rows})[:2]
    keep = []
    for exp in exps:
        grp = [r for r in rows if r["expiry"] == exp]
        uniq = sorted({r["strike"] for r in grp})
        i = min(range(len(uniq)), key=lambda k: abs(uniq[k] - und))
        want = set(uniq[max(0, i - n):i + n + 1])
        keep += [r for r in grp if r["strike"] in want]
    return keep


def save(rows):
    OUT.mkdir(parents=True, exist_ok=True)
    now = _now()
    fp = OUT / f"{now.date().isoformat()}.jsonl"
    snap = now.replace(second=0, microsecond=0).isoformat(timespec="seconds")
    for r in rows:
        r.setdefault("snapshot_utc", snap)
    seen = set()
    if fp.exists():
        for ln in fp.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(ln)
                seen.add((d.get("contract"), d.get("snapshot_utc") or d.get("ts")))
                seen.add((d.get("expiry"), d.get("strike"), d.get("right"),
                          d.get("snapshot_utc") or d.get("ts")))
            except Exception:
                pass
    new = []
    for r in rows:
        k1 = (r.get("contract"), r.get("snapshot_utc") or r.get("ts"))
        k2 = (r.get("expiry"), r.get("strike"), r.get("right"),
              r.get("snapshot_utc") or r.get("ts"))
        if k1 in seen or k2 in seen:
            continue
        seen.add(k1)
        seen.add(k2)
        new.append(r)
    if new:
        with open(fp, "a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return fp, len(new), len(rows)


def collect(sym, n):
    tok = os.getenv("TRADIER_TOKEN", "")
    errs = []
    rows, src = [], ""
    if tok:
        try:
            rows = tradier_chain(sym, tok)
            src = "tradier"
        except Exception as ex:
            errs.append(f"tradier:{ex}")
    if not rows:
        try:
            rows = cboe_chain(sym)
            src = "cboe"
        except Exception as ex:
            errs.append(f"cboe:{ex}")
    if not rows:
        try:
            rows = yahoo_chain(sym)
            src = "yahoo"
        except Exception as ex:
            errs.append(f"yahoo:{ex}")
            raise RuntimeError("all sources failed: " + " | ".join(errs))
    if errs:
        print(f"[warn] {'; '.join(errs)} -> {src} fallback", file=sys.stderr)
    rows = atm_filter(rows, n)
    fp, nw, tot = save(rows)
    print(f"{sym} {src}: {nw} new / {tot} rows -> {fp}")
    return nw


def main():
    ap = argparse.ArgumentParser(description="META option chain collector")
    ap.add_argument("--loop", action="store_true", help="repeat every 15 min")
    ap.add_argument("--strikes", type=int, default=10, help="ATM strikes per side")
    ap.add_argument("--symbol", default="META", help="underlying symbol")
    a = ap.parse_args()
    collect(a.symbol, a.strikes)
    while a.loop:
        time.sleep(15 * 60)
        try:
            collect(a.symbol, a.strikes)
        except Exception as ex:
            print(f"[loop-err] {ex}", file=sys.stderr)


if __name__ == "__main__":
    main()
