"""VIX 5분봉 수집 (Yahoo 우선, Cboe 일봉 폴백, 키 불필요).
Usage: python 69_VIX.py [--loop]
  1회: 스냅샷+히스토리 조회 후 완성봉 저장 | --loop: ET정규장만 5분마다 반복
  원천(primary): https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=5m (5분봉 OHLCV)
  폴백(fallback): https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv (Cboe VIX 공식 일봉)
    -> 폴백 시 일봉으로 저장하고 5분봉 상태는 bar_open_utc=None 행으로 별도 기록
  out: data/VIX/YYYY-MM-DD.jsonl (일자=ET 기준)
  docs: https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=5m (Yahoo 공개 차트, 키 불필요)
"""
import argparse as A, csv as C, json as J, time as T
from datetime import datetime as D, timedelta as TD, timezone as TZ
from pathlib import Path as P
import requests as R

SYM = "VIX"
YAHOO_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=5m",
    "https://query2.finance.yahoo.com/v8/finance/chart/%5EVIX?range=5d&interval=5m",
]
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
UA = {"User-Agent": "Mozilla/5.0 (collector; vix-5min)"}
OUT = P(__file__).parent / "data" / SYM


def et_now():
    u = D.now(TZ.utc)
    y = u.year
    s = (D(y, 3, 1, tzinfo=TZ.utc) + TD(days=(6 - D(y, 3, 1, tzinfo=TZ.utc).weekday()) % 7 + 7)).replace(hour=7)
    e = (D(y, 11, 1, tzinfo=TZ.utc) + TD(days=(6 - D(y, 11, 1, tzinfo=TZ.utc).weekday()) % 7)).replace(hour=6)
    return u, u + (TD(hours=-4) if s <= u < e else TD(hours=-5))


def et_is_dst(dt_utc):
    y = dt_utc.year
    s = (D(y, 3, 1, tzinfo=TZ.utc) + TD(days=(6 - D(y, 3, 1, tzinfo=TZ.utc).weekday()) % 7 + 7)).replace(hour=7)
    e = (D(y, 11, 1, tzinfo=TZ.utc) + TD(days=(6 - D(y, 11, 1, tzinfo=TZ.utc).weekday()) % 7)).replace(hour=6)
    return s <= dt_utc < e


def et_of(dt_utc):
    return dt_utc + (-TD(hours=4) if et_is_dst(dt_utc) else -TD(hours=5))


def _is_closed(et):
    return et.weekday() >= 5 or not (TD(hours=9, minutes=30) <= TD(hours=et.hour, minutes=et.minute) < TD(hours=16))


def fetch_yahoo():
    """Yahoo 5m -> [(open_utc, o,h,l,c,v)]. 미완성봉 제외. 예외 시 raise."""
    last_err = None
    for url in YAHOO_URLS:
        try:
            r = R.get(url, headers=UA, timeout=30)
            if r.status_code == 429:
                last_err = RuntimeError("rate_limited")
                continue
            r.raise_for_status()
            j = r.json()
            res = (j.get("chart", {}).get("result") or [None])[0]
            if not res:
                err = (j.get("chart", {}).get("error") or {})
                last_err = RuntimeError(str(err.get("description") or "empty_result"))
                continue
            ts = res.get("timestamp") or []
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            opens = q.get("open") or []
            highs = q.get("high") or []
            lows = q.get("low") or []
            closes = q.get("close") or []
            vols = q.get("volume") or []
            u, _ = et_now()
            rows = []
            for i, t in enumerate(ts):
                try:
                    op = D.fromtimestamp(int(t), tz=TZ.utc)
                except Exception:
                    continue
                if op + TD(minutes=5) > u:  # 완성봉만
                    continue
                c = closes[i] if i < len(closes) else None
                if c is None:
                    continue
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                l = lows[i] if i < len(lows) else None
                v = vols[i] if i < len(vols) else 0
                try:
                    v = int(v) if v is not None else 0
                except (ValueError, TypeError):
                    v = 0
                rows.append((op,
                               float(o) if o is not None else None,
                               float(h) if h is not None else None,
                               float(l) if l is not None else None,
                               float(c), v))
            if rows:
                return rows
            last_err = RuntimeError("empty_bars")
        except RuntimeError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    if isinstance(last_err, RuntimeError) and str(last_err) == "rate_limited":
        raise RuntimeError("rate_limited")
    raise RuntimeError("http_error" if last_err is None else str(last_err))


def fetch_cboe_daily():
    """Cboe VIX 공식 일봉 CSV -> [(day_open_utc, o,h,l,c)]. DATE=MM/DD/YYYY."""
    r = R.get(CBOE_URL, headers=UA, timeout=30)
    if r.status_code == 429:
        raise RuntimeError("rate_limited")
    r.raise_for_status()
    lines = [x for x in r.text.splitlines() if x.strip()]
    if not lines:
        raise RuntimeError("empty_csv")
    rd = C.DictReader(lines)
    out = []
    for row in rd:
        try:
            ds = (row.get("DATE") or "").strip()
            dt = D.strptime(ds, "%m/%d/%Y").replace(tzinfo=TZ.utc)
            def fn(k):
                try:
                    s = (row.get(k) or "").strip()
                    return float(s) if s else None
                except ValueError:
                    return None
            o, h, l, c = fn("OPEN"), fn("HIGH"), fn("LOW"), fn("CLOSE")
            if c is None and o is None:
                continue
            out.append((dt, o, h, l, c))
        except (ValueError, TypeError, KeyError):
            continue
    if not out:
        raise RuntimeError("empty_bars")
    return out


def fetch_all():
    """(bars, status, kind) — kind='5m'|'daily'. Yahoo 우선, 실패 시 Cboe 일봉."""
    try:
        rows = fetch_yahoo()
        if rows:
            return rows, "ok", "5m"
    except RuntimeError as e:
        if str(e) == "rate_limited":
            return [], "rate_limited", "5m"
    except Exception:
        pass
    try:
        d = fetch_cboe_daily()
        return (d, "daily_fallback", "daily") if d else ([], "http_error", "daily")
    except RuntimeError as e:
        if str(e) == "rate_limited":
            return [], "rate_limited", "daily"
        return [], "http_error", "daily"
    except Exception:
        return [], "http_error", "daily"


def _seen_opens(fp):
    seen = set()
    if fp.exists():
        for ln in fp.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(J.loads(ln).get("bar_open_utc"))
            except Exception:
                pass
    return seen


def _write_status_row(fp, source, recv, st):
    with fp.open("a", encoding="utf-8") as f:
        f.write(J.dumps({"source": source, "symbol": SYM, "bar_open_utc": None,
               "bar_close_utc": None, "received_at_utc": recv, "o": None,
               "h": None, "l": None, "c": None, "v": None, "n": None,
               "vwap": None, "status": st}) + "\n")


def save_bars(bars, status="ok", source="yahoo", kind="5m"):
    u, et = et_now()
    recv = u.strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.mkdir(parents=True, exist_ok=True)
    done, grp = 0, {}
    if kind == "daily":
        # Cboe 일봉: bar_open=일자 00:00Z, bar_close=익일 00:00Z 로 일봉 저장
        for op, o, h, l, c in bars:
            day = op.strftime("%Y-%m-%d")
            grp.setdefault(day, []).append((op, op + TD(days=1), o, h, l, c, 0))
        for day, lst in sorted(grp.items()):
            fp = OUT / f"{day}.jsonl"
            seen = _seen_opens(fp)
            with fp.open("a", encoding="utf-8") as f:
                for op, cl, o, h, l, c, v in sorted(lst):
                    k = op.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if k in seen:  # 중복금지 (과거 오류행의 None은 실측 저장 차단 안 함)
                        continue
                    f.write(J.dumps({"source": source, "symbol": SYM, "bar_open_utc": k,
                           "bar_close_utc": cl.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "received_at_utc": recv, "o": o, "h": h, "l": l, "c": c,
                           "v": v, "n": None, "vwap": None, "status": "daily"}) + "\n")
                    seen.add(k)
                    done += 1
        # 5분봉 상태 별도 기록 (일봉 대체 시)
        _write_status_row(OUT / f"{et.strftime('%Y-%m-%d')}.jsonl", source, recv,
                          "5m_unavailable_daily_saved" if done else status)
        return done
    for op, o, h, l, c, v in bars:
        cl = op + TD(minutes=5)
        if cl > u:  # 완성봉만
            continue
        ex = et_of(op)
        if ex.weekday() >= 5 or not (TD(hours=9, minutes=30) <= TD(hours=ex.hour, minutes=ex.minute) < TD(hours=16)):
            continue  # ET 정규장봉만
        grp.setdefault(ex.strftime("%Y-%m-%d"), []).append((op, cl, o, h, l, c, v))
    for day, lst in grp.items():
        fp = OUT / f"{day}.jsonl"
        seen = _seen_opens(fp)
        with fp.open("a", encoding="utf-8") as f:
            for op, cl, o, h, l, c, v in sorted(lst):
                k = op.strftime("%Y-%m-%dT%H:%M:%SZ")
                if k in seen:  # 중복금지
                    continue
                f.write(J.dumps({"source": source, "symbol": SYM, "bar_open_utc": k,
                       "bar_close_utc": cl.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "received_at_utc": recv, "o": o, "h": h, "l": l, "c": c,
                       "v": v, "n": None, "vwap": None, "status": status}) + "\n")
                seen.add(k)
                done += 1
    if status != "ok" or not done:
        st = status if status != "ok" else ("closed" if _is_closed(et) else "delayed")
        _write_status_row(OUT / f"{et.strftime('%Y-%m-%d')}.jsonl", source, recv, st)
    return done


def main():
    ap = A.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    a = ap.parse_args()
    if not a.loop:
        b, st, kind = fetch_all()
        src = "yahoo" if kind == "5m" else "cboe"
        print(f"once status={st} saved={save_bars(b, st if st != 'ok' else 'ok', src, kind)} source={src} kind={kind}")
        return
    while True:
        u, et = et_now()
        if _is_closed(et):
            print(f"closed {et:%Y-%m-%d %H:%M} ET skip")
            T.sleep(300)
            continue
        b, st, kind = fetch_all()
        src = "yahoo" if kind == "5m" else "cboe"
        print(f"loop {et:%H:%M} ET status={st} saved={save_bars(b, st if st != 'ok' else 'ok', src, kind)} source={src} kind={kind}")
        T.sleep(300)


if __name__ == "__main__":
    main()
