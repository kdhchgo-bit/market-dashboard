"""VIX 옵션체인 스냅샷(딜레이, 근월 2개 ATM+-10).

Usage: python 77_VIX_OPTIONS.py [--loop] [--interval 900]
  1회: CBOE 우선 수집 후 일자파일에 계약당 1줄 저장 | --loop: 15분마다 반복
  원천(primary): https://cdn.cboe.com/api/global/delayed_quotes/options/VIX.json
  폴백: Yahoo crumb 플로우(query2/query1 v7 finance options, fc.yahoo.com 쿠키+getcrumb)
  dedup키: (collected_at_utc, expiration, strike, type, contract) — 스냅샷시각 포함
  out: data/VIX_OPTIONS/YYYY-MM-DD.jsonl (일자=UTC 기준, 동일 스키마)
  docs: CBOE delayed quotes API / Yahoo v7 finance options (키 불필요)
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

UNDERLYING = "VIX"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/VIX.json"
YAHOO_HOSTS = (
    "https://query2.finance.yahoo.com",
    "https://query1.finance.yahoo.com",
)
YAHOO_OPT_PATH = "/v7/finance/options/%5EVIX"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_FC_URL = "https://fc.yahoo.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36", "Accept": "application/json, text/plain, */*"}
OUT_DIR = Path(__file__).parent / "data" / "VIX_OPTIONS"


def to_float(value, default=None):
    try:
        return float(value) if value is not None and str(value).strip() != "" else default
    except (ValueError, TypeError, AttributeError):
        return default


def normalize_expiration(value):
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d")
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(int(float(value)), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return text


def fetch_cboe():
    response = requests.get(CBOE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json().get("data", {})
    price = to_float(data.get("current_price") or data.get("price") or data.get("close"))
    options = data.get("options", []) or []
    rows = []
    for item in options:
        raw_type = str(item.get("option_type", item.get("type", ""))).upper()
        if raw_type.startswith("C"):
            option_type = "C"
        elif raw_type.startswith("P"):
            option_type = "P"
        else:
            continue
        rows.append({
            "expiration": normalize_expiration(item.get("expiration") or item.get("expiration_date")),
            "strike": to_float(item.get("strike_price") or item.get("strike")),
            "type": option_type,
            "bid": to_float(item.get("bid")),
            "ask": to_float(item.get("ask")),
            "last": to_float(item.get("last_trade_price") or item.get("last")),
            "volume": item.get("volume"),
            "open_interest": item.get("open_interest"),
            "iv": to_float(item.get("implied_volatility") or item.get("iv")),
            "contract": str(item.get("option") or item.get("symbol") or ""),
        })
    if price is None:
        underlying_prices = [
            item.get("underlying_price")
            for item in options
            if to_float(item.get("underlying_price")) is not None
        ]
        price = to_float(underlying_prices[0]) if underlying_prices else None
    return price, rows, "cboe"


def _yahoo_session_with_crumb():
    session = requests.Session()
    session.headers.update(HEADERS)
    crumb = ""
    try:
        session.get(YAHOO_FC_URL, timeout=20)
        response = session.get(YAHOO_CRUMB_URL, timeout=20)
        if response.ok and response.text and "crumb" not in response.text.lower():
            crumb = response.text.strip()
    except Exception:
        crumb = ""
    return session, crumb


def fetch_yahoo_result(session=None, crumb="", timestamp=None):
    session = session or requests.Session()
    if not session.headers.get("User-Agent"):
        session.headers.update(HEADERS)
    params = {}
    if crumb:
        params["crumb"] = crumb
    if timestamp is not None:
        params["date"] = int(timestamp)
    last_exc = None
    for host in YAHOO_HOSTS:
        url = host + YAHOO_OPT_PATH
        try:
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()["optionChain"]["result"][0]
        except Exception as exc:
            last_exc = exc
    raise RuntimeError(f"yahoo_options_failed: {last_exc}")


def fetch_yahoo():
    session, crumb = _yahoo_session_with_crumb()
    first = fetch_yahoo_result(session, crumb)
    price = to_float((first.get("quote") or {}).get("regularMarketPrice"))
    expirations = sorted(first.get("expirationDates", []))[:2]
    rows = []
    for timestamp in expirations:
        first_expiry = (first.get("options") or [{}])[0].get("expirationDate")
        result = first if timestamp == first_expiry else fetch_yahoo_result(session, crumb, timestamp)
        if not result.get("options"):
            result = fetch_yahoo_result(session, crumb, timestamp)
        expiry = normalize_expiration(timestamp)
        legs = (result.get("options") or [{}])[0]
        for key, option_type in (("calls", "C"), ("puts", "P")):
            for contract in legs.get(key) or []:
                rows.append({
                    "expiration": expiry,
                    "strike": to_float(contract.get("strike")),
                    "type": option_type,
                    "bid": to_float(contract.get("bid")),
                    "ask": to_float(contract.get("ask")),
                    "last": to_float(contract.get("lastPrice")),
                    "volume": contract.get("volume"),
                    "open_interest": contract.get("openInterest"),
                    "iv": to_float(contract.get("impliedVolatility")),
                    "contract": str(contract.get("contractSymbol") or ""),
                })
    return price, rows, "yahoo"


def select_front_two_atm(price, rows):
    expirations = sorted(
        {item["expiration"] for item in rows if item["expiration"] and item["strike"] is not None}
    )
    selected = []
    for expiry in expirations[:2]:
        strikes = sorted({item["strike"] for item in rows if item["expiration"] == expiry})
        if price is not None:
            atm_index = min(range(len(strikes)), key=lambda j: abs(strikes[j] - price))
        else:
            atm_index = 0
        keep = set(strikes[max(0, atm_index - 10):atm_index + 11])
        selected += [item for item in rows if item["expiration"] == expiry and item["strike"] in keep]
    return selected


def save_rows(price, rows, source):
    now = datetime.now(timezone.utc)
    collected_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = OUT_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"
    seen = set()
    if filepath.exists():
        for line in filepath.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                seen.add((
                    record.get("collected_at_utc"),
                    record.get("expiration"),
                    record.get("strike"),
                    record.get("type"),
                    record.get("contract"),
                ))
            except Exception:
                pass
    count = 0
    with filepath.open("a", encoding="utf-8") as handle:
        for item in sorted(
            rows, key=lambda z: (z["expiration"], z["strike"] or 0, z["type"])
        ):
            key = (collected_at, item["expiration"], item["strike"], item["type"], item["contract"])
            if key in seen:
                continue
            handle.write(json.dumps({
                "source": source,
                "underlying": UNDERLYING,
                "is_delayed": True,
                "collected_at_utc": collected_at,
                "underlying_price": price,
                "expiration": item["expiration"],
                "strike": item["strike"],
                "type": item["type"],
                "bid": item["bid"],
                "ask": item["ask"],
                "last": item["last"],
                "volume": item["volume"],
                "open_interest": item["open_interest"],
                "iv": item["iv"],
                "contract": item["contract"],
            }) + "\n")
            seen.add(key)
            count += 1
    return count, str(filepath)


def collect_once():
    try:
        price, rows, source = fetch_cboe()
    except Exception as exc:
        print(f"cboe fail {exc} -> yahoo fallback")
        price, rows, source = fetch_yahoo()
    rows = [item for item in rows if item["strike"] is not None and item["expiration"]]
    if not rows:
        raise RuntimeError("empty_chain")
    selected = select_front_two_atm(price, rows) or rows
    return save_rows(price, selected, source)


def main():
    parser = argparse.ArgumentParser(description="VIX 옵션체인 스냅샷(딜레이, 근월2개 ATM+-10)")
    parser.add_argument("--loop", action="store_true", help="15분마다 반복")
    parser.add_argument("--interval", type=int, default=900, help="반복 간격(초)")
    args = parser.parse_args()
    if not args.loop:
        count, filepath = collect_once()
        print(f"once saved={count} file={filepath}")
        return
    while True:
        try:
            count, filepath = collect_once()
            print(f"loop saved={count} file={filepath}")
        except Exception as exc:
            print(f"loop error {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
