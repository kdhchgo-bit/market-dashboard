"""XLRE (부동산섹터) 5분봉 수집기.

원천: Yahoo Finance 공개 차트 (키 불필요)
  GET https://query1.finance.yahoo.com/v8/finance/chart/XLRE?interval=5m&period1={s}&period2={e}&includePrePost=false
  (query1 실패 시 query2.finance.yahoo.com으로 재시도)
인증: 없음 (공개 수신처, 로그인 세션 불필요).
동작: 기본 1회 수집(최근 20거래일, 제공 범위 내 백필) + --loop 지정 시 ET 정규장(평일 09:30-16:00) 5분마다 완성봉만 추가.
정규장 완성봉만 저장: ET 평일 09:30<=개장<16:00, bar_close_utc<=수신시각. VWAP 미제공=null, 거래대금 근삿값은 notional_est 별도 필드.
저장: Path(__file__).parent/"data"/"XLRE"/YYYY-MM-DD.jsonl (일별 분할, 날짜=bar_open_utc 일자).
레코드: source/symbol/bar_open_utc/received_at_utc/o/h/l/c/v/n/vwap/notional_est/status. 중복(bar_open_utc) 저장 금지, 가짜값 생성 금지.
사용: python 36_XLRE.py [--loop]
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

SYMBOL = "XLRE"
INTERVAL = "5m"
SOURCE = "yahoo-chart"
HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
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


def _start_20td(end: datetime) -> datetime:
    d = end.replace(hour=0, minute=0, second=0, microsecond=0)
    n = 0
    while n < 20:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


def _fetch(start: datetime, end: datetime) -> list:
    params = {
        "interval": INTERVAL,
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "includePrePost": "false",
    }
    js = None
    last_err = "no_host"
    for host in HOSTS:
        try:
            r = requests.get(f"{host}/v8/finance/chart/{SYMBOL}",
                             params=params, headers=UA, timeout=30)
        except Exception as e:
            last_err = f"http_error: {e}"
            continue
        if r.status_code != 200:
            last_err = f"http {r.status_code}: {r.text[:200]}"
            continue
        try:
            js = r.json()
        except ValueError:
            last_err = "http_error: bad_json"
            continue
        err = (js.get("chart") or {}).get("error")
        if err:
            last_err = f"api_error: {err}"
            js = None
            continue
        break
    if js is None:
        print(last_err, file=sys.stderr)
        raise RuntimeError(last_err)
    res = ((js.get("chart") or {}).get("result") or [None])[0]
    if not res:
        return []
    now = _now_utc()
    ts = res.get("timestamp") or []
    quote = (res.get("indicators") or {}).get("quote") or [{}]
    q = quote[0] if quote else {}
    out: list = []
    for i, t in enumerate(ts):
        try:
            open_dt = datetime.fromtimestamp(t, timezone.utc)
        except (ValueError, OSError, TypeError):
            continue
        if open_dt + timedelta(minutes=5) > now:  # 완성봉만
            continue
        et_open = open_dt.astimezone(ET)
        if et_open.weekday() >= 5:
            continue
        if not ((9, 30) <= (et_open.hour, et_open.minute) < (16, 0)):
            continue
        o = (q.get("open") or [None])[i] if i < len(q.get("open") or []) else None
        h = (q.get("high") or [None])[i] if i < len(q.get("high") or []) else None
        l = (q.get("low") or [None])[i] if i < len(q.get("low") or []) else None
        c = (q.get("close") or [None])[i] if i < len(q.get("close") or []) else None
        v = (q.get("volume") or [None])[i] if i < len(q.get("volume") or []) else None
        if o is None and c is None:
            continue  # 가짜값 대신 스킵
        out.append({"t": _iso_z(open_dt), "o": o, "h": h, "l": l, "c": c, "v": v})
    return out


def _keys_for(path: Path) -> set:
    k = str(path)
    if k not in _keys_cache:
        s: set = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                # 오류 행(None/무효 OHLC)은 키로 삼지 않아 뒤늦은 실측 저장을 막지 않는다.
                if r.get("bar_open_utc") and r.get("o") is not None and r.get("c") is not None:
                    s.add(r.get("bar_open_utc"))
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
        v = b.get("v")
        px = [x for x in (b.get("h"), b.get("l"), b.get("c")) if x is not None]
        notional = round(sum(px) / len(px) * v, 2) if px and isinstance(v, (int, float)) else None
        rec = {"source": SOURCE, "symbol": SYMBOL, "bar_open_utc": t,
               "received_at_utc": now, "o": b.get("o"), "h": b.get("h"),
               "l": b.get("l"), "c": b.get("c"), "v": v,
               "n": None, "vwap": None, "notional_est": notional, "status": "final"}
        # ponytail: append only, never rewrite history
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        keys.add(t)
        added += 1
    return added


def _collect_once() -> int:
    end = _floor5_utc(_now_utc())
    start = _start_20td(end)
    bars = _fetch(start, end)
    n = _save(bars)
    print(f"fetched={len(bars)} added={n} range={_iso_z(start)}..{_iso_z(end)}")
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
    ap = argparse.ArgumentParser(description="XLRE 5분봉 수집 (Yahoo chart, 키 불필요)")
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    if a.loop:
        _loop()
    else:
        _collect_once()


if __name__ == "__main__":
    main()
