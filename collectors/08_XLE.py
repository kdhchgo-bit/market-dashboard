"""XLE (에너지업종) 5분봉 수집기.

원천: Yahoo Finance 공개 차트 (키 불필요)
  GET https://query1.finance.yahoo.com/v8/finance/chart/XLE?range=60d&interval=5m&includePrePost=false
  (query1 실패 시 query2로 재시도)
동작: 기본 1회 수집(최근 20거래일 백필, Yahoo 제공 범위 내) + --loop 지정 시 ET 정규장(평일 09:30-16:00) 5분마다 완성봉만 추가.
저장: Path(__file__).parent/"data"/"XLE"/YYYY-MM-DD.jsonl (일별 분할, 날짜=bar_open_utc 일자).
레코드: source/symbol/bar_open_utc/received_at_utc/o/h/l/c/v/n/vwap/notional_proxy/status. 중복(bar_open_utc) 저장 금지, 가짜값 생성 금지.
  - Yahoo가 제공하지 않는 n(체결건수)/vwap는 null 보존.
  - 거래대금 근삿값(c*v)은 별도 필드 notional_proxy로 표시.
사용: python 08_XLE.py [--loop]
의존성: 표준lib + requests.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

SYMBOL = "XLE"
TAG = "XLE"
SOURCE = "yahoo-chart"
INTERVAL = "5m"
RANGE = "60d"
URLS = [
    f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
    f"https://query2.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).parent / "data" / SYMBOL
_keys_cache: dict[str, set] = {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _floor5_utc(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return dt - timedelta(minutes=dt.minute % 5)


def _fetch_yahoo() -> list:
    """Yahoo 5분봉 원시 바 리스트 반환. 각 원소는 dict(t/o/h/l/c/v)."""
    params = {"range": RANGE, "interval": INTERVAL, "includePrePost": "false",
              "events": "div,splits"}
    last_err: Exception | None = None
    for url in URLS:
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
        except Exception as e:
            last_err = e
            continue
        if r.status_code != 200:
            print(f"yahoo http {r.status_code}: {r.text[:200]}", file=sys.stderr)
            last_err = RuntimeError(f"http {r.status_code}")
            continue
        try:
            js = r.json()
        except ValueError as e:
            last_err = e
            continue
        chart = js.get("chart") or {}
        if chart.get("error"):
            print(f"yahoo error: {chart['error']}", file=sys.stderr)
            last_err = RuntimeError(str(chart["error"]))
            continue
        res = (chart.get("result") or [None])[0]
        if not res:
            last_err = RuntimeError("empty result")
            continue
        ts = res.get("timestamp") or []
        quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        out: list = []
        for i, t in enumerate(ts):
            try:
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                lo = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = vols[i] if i < len(vols) else None
            except IndexError:
                continue
            out.append({"t": int(t), "o": o, "h": h, "l": lo, "c": c, "v": v})
        return out
    raise RuntimeError(f"yahoo fetch failed: {last_err}")


def _to_bars(raw: list, floor: datetime) -> list:
    """완성봉 + 정규장 + 최근 20거래일 필터. 반환 원소는 Alpaca 호환 dict(t/o/h/l/c/v)."""
    now = _now_utc()
    bars: list = []
    for b in raw:
        try:
            bt = datetime.fromtimestamp(int(b["t"]), tz=timezone.utc)
        except (ValueError, TypeError, KeyError):
            continue
        # 완성봉만: 5분 경계에 정렬된 바 + 종가가 현재 floor 이전
        if int(b["t"]) % 300 != 0:
            continue  # 진행 중 비정렬 스냅샷(Yahoo 현재가 행) 제외
        if bt >= floor:
            continue
        if bt + timedelta(minutes=5) > now:
            continue
        # 정규장만(ET 09:30-16:00, 평일)
        et = bt.astimezone(ET)
        if et.weekday() >= 5:
            continue
        if (et.hour, et.minute) < (9, 30) or (et.hour, et.minute) >= (16, 0):
            continue
        if b.get("o") is None or b.get("c") is None:
            continue  # null뿐인 행 스킵(가짜값 생성 금지)
        bars.append({
            "t": _iso_z(bt),
            "o": b.get("o"), "h": b.get("h"), "l": b.get("l"),
            "c": b.get("c"), "v": b.get("v"),
        })
    # 최근 20거래일 백필(제공 범위 내): 마지막 거래일 기준 20거래일 이전 컷
    if bars:
        days: list = sorted({x["t"][:10] for x in bars})
        keep = set(days[-20:]) if len(days) > 20 else set(days)
        bars = [x for x in bars if x["t"][:10] in keep]
    return sorted(bars, key=lambda x: x["t"])


def _keys_for(path: Path) -> set:
    k = str(path)
    if k not in _keys_cache:
        s: set = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    v = json.loads(line).get("bar_open_utc")
                except ValueError:
                    continue
                if v is None:
                    continue  # 과거 오류 행이 실측 저장을 막지 않게
                s.add(v)
        _keys_cache[k] = s
    return _keys_cache[k]


def _save(bars: list) -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    now = _iso_z(_now_utc())
    added = 0
    for b in bars:
        t = b.get("t")
        if not t or b.get("o") is None or b.get("c") is None:
            continue  # 가짜값 대신 스킵
        path = ROOT / f"{t[:10]}.jsonl"
        keys = _keys_for(path)
        if t in keys:
            continue
        c, v = b.get("c"), b.get("v")
        notion = (c * v) if isinstance(c, (int, float)) and isinstance(v, (int, float)) else None
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": t,
               "received_at_utc": now, "o": b.get("o"), "h": b.get("h"),
               "l": b.get("l"), "c": c, "v": v,
               "n": None, "vwap": None, "notional_proxy": notion, "status": "final"}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        keys.add(t)
        added += 1
    return added


def _collect_once() -> int:
    floor = _floor5_utc(_now_utc())
    raw = _fetch_yahoo()
    bars = _to_bars(raw, floor)
    n = _save(bars)
    lo = bars[0]["t"] if bars else "-"
    hi = bars[-1]["t"] if bars else "-"
    print(f"fetched={len(raw)} completed={len(bars)} added={n} range={lo}..{hi}")
    return n


def _in_session(et_now: datetime) -> bool:
    if et_now.weekday() >= 5:
        return False
    return (9, 30) <= (et_now.hour, et_now.minute) < (16, 0)


def _sleep_to_next() -> None:
    et_now = datetime.now(ET)
    sec = 300 - ((et_now.minute * 60 + et_now.second) % 300)
    time.sleep(sec + 30)


def _loop() -> None:
    print("loop: ET 정규장 5분 주기, 완성봉만")
    while True:
        try:
            if _in_session(datetime.now(ET)):
                _collect_once()
                _sleep_to_next()
            else:
                time.sleep(60)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
            time.sleep(60)


def main() -> None:
    ap = argparse.ArgumentParser(description="XLE 5분봉 수집 (Yahoo chart, 키 불필요)")
    ap.add_argument("--loop", action="store_true", help="ET 정규장 5분 주기 완성봉 수집")
    a = ap.parse_args()
    if a.loop:
        _loop()
    else:
        _collect_once()


if __name__ == "__main__":
    main()
