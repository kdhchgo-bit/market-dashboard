"""GUI 소유의 수집기 실행 관리자. 원본 수집기와 JSONL 판정은 분리한다."""
import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone


HERE = Path(__file__).resolve().parent
LOG_LIMIT = 256 * 1024
OK = {"ok", "scheduled", "updated", "oi_unavailable", "delayed", "new", "confirmed"}
ESSENTIAL = {5: 0, 19: 1, 27: 2, 28: 3, 29: 4, 74: 5}
ORIGINAL_TIME = ("snapshot_at_utc", "snapshot_ts", "snapshot_utc", "snapshot", "quote_utc",
                 "quote_time_utc", "quote_time", "ts_utc", "ts", "timestamp")


def utc(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def present(row, names):
    return next((row[key] for key in names if row.get(key) is not None and row.get(key) != ""), None)


def numeric(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (ValueError, TypeError):
        return False


def row_result(row, route):
    if not isinstance(row, dict):
        return "error"
    status = str(row.get("status") or "").lower()
    if "key" in status and any(word in status for word in ("missing", "required", "no_key")):
        return "key"
    if status in {"closed", "market_closed", "holiday"}:
        return "closed"
    kind = route["kind"]
    bar_status = kind in {"CoinBar", "UsBar", "FxBar"} and status in {"final", "completed"}
    if status and status not in OK and not bar_status and not (kind == "CoinBar" and status == "ok_oi_null"):
        return "error"
    if kind in {"UsBar", "CoinBar", "FxBar"}:
        close = present(row, ("c", "close"))
        if not numeric(close) or float(close) <= 0:
            return "error"
        return "valid" if present(row, ("bar_open_utc", "t", "open_time_iso", "open_time", "open_ts", "open_ms", "open_time_ms", "ts")) else "time"
    if kind in {"UsChain", "CoinChain"}:
        if not numeric(present(row, ("strike", "strike_price"))):
            return "error"
        if present(row, ("option_type", "type", "right", "put_call", "cp", "side")) is None:
            return "error"
        if not numeric(present(row, ("bid", "bid_price", "best_bid_price"))) or not numeric(present(row, ("ask", "ask_price", "best_ask_price"))):
            return "error"
        observed = present(row, ORIGINAL_TIME)
        if route["no"] == 98:
            observed = row.get("snapshot_at_utc")
        if not observed and route["no"] in {101, 102}:
            observed = present(row, ("received_at_utc", "collected_at", "collected_at_utc", "fetched_at"))
        return "valid" if observed else "time"
    if kind == "Calendar":
        return "valid" if present(row, ("release_date", "expiration_date", "date")) else "error"
    if kind == "News":
        return "valid" if row.get("title") else "error"
    if not numeric(present(row, ("value", "rate", "ratio", "close", "c"))):
        return "error"
    return "valid" if present(row, ("obs_time", "obs_date", "date", "selectedDate", "bar_open_utc")) else "time"


def file_snapshot(route):
    result = {}
    folder = HERE / "data" / route["tag"]
    if not folder.is_dir():
        return result
    for path in folder.glob("*.jsonl"):
        try:
            stat = path.stat()
            with path.open("rb") as source:
                prefix = source.read(min(128, stat.st_size))
            result[str(path)] = (stat.st_size, stat.st_mtime_ns, prefix)
        except OSError:
            continue
    return result


def count_stream(source, route, counts):
    for raw in source:
        if not raw.endswith(b"\n"):
            continue  # 수집기가 아직 쓰는 중인 마지막 행은 확정하지 않는다.
        try:
            result = row_result(json.loads(raw.decode("utf-8-sig")), route)
        except (ValueError, UnicodeError):
            result = "error"
        counts[result] += 1


def collected_rows(route, before):
    counts = dict.fromkeys(("valid", "error", "key", "closed", "time"), 0)
    after = file_snapshot(route)
    for filename, info in after.items():
        previous = before.get(filename)
        if previous and info[:2] == previous[:2]:
            continue
        offset = 0
        if previous and info[0] > previous[0] and info[2][:len(previous[2])] == previous[2]:
            offset = previous[0]
        try:
            with open(filename, "rb") as source:
                source.seek(offset)
                count_stream(source, route, counts)
        except OSError:
            counts["error"] += 1
    # 기존 자료는 최신 파일의 제한된 실제 표본으로만 확인한다. 전체 행수로 표현하지 않는다.
    existing = 0
    for filename, info in sorted(before.items(), key=lambda item: item[1][1], reverse=True)[:3]:
        try:
            with open(filename, "rb") as source:
                offset = max(0, info[0] - 128 * 1024)
                source.seek(offset)
                if offset:
                    source.readline()
                sample = dict.fromkeys(counts, 0)
                count_stream(source, route, sample)
                existing += sample["valid"]
        except OSError:
            pass
    return counts, existing


class Capture(threading.Thread):
    def __init__(self, process, path, header):
        super().__init__(daemon=True)
        self.process, self.path = process, path
        self.tail = bytearray()
        self.header = header.encode("utf-8")

    def append(self, content):
        with self.path.open("ab") as log:
            log.write(content)
        if self.path.stat().st_size > LOG_LIMIT:
            with self.path.open("r+b") as log:
                log.seek(-LOG_LIMIT, os.SEEK_END)
                recent = log.read(LOG_LIMIT)
                log.seek(0)
                log.write(recent)
                log.truncate()

    def run(self):
        try:
            self.append(self.header)
            while True:
                content = os.read(self.process.stdout.fileno(), 4096)
                if not content:
                    break
                self.tail.extend(content)
                del self.tail[:-32768]
                self.append(content)
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.process.stdout.close()
            except OSError:
                pass


class ParentProcess:
    def __init__(self, pid):
        self.pid = pid
        self.handle = None
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
            self.kernel.OpenProcess.restype = ctypes.c_void_p
            self.kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            self.kernel.WaitForSingleObject.restype = ctypes.c_uint32
            self.kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)

    def alive(self):
        if os.name == "nt":
            return bool(self.handle) and self.kernel.WaitForSingleObject(self.handle, 0) == 258
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)


class Host:
    def __init__(self, args):
        self.state_path = Path(args.state).resolve()
        self.control_path = Path(args.control).resolve()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir = self.state_path.parent / "collectors"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if "work" in self.state_path.parts and "삭제" in self.state_path.parts:
            self.temp_path = self.state_path.with_name(self.state_path.name + ".tmp")
        else:
            scratch = self.state_path.parent / "work" / "삭제"
            scratch.mkdir(parents=True, exist_ok=True)
            self.temp_path = scratch / (self.state_path.name + ".tmp")
        self.parent = ParentProcess(args.parent_pid)
        self.routes = json.loads((HERE / "collector_routes.json").read_text(encoding="utf-8-sig"))
        self.rows = {}
        for route in self.routes:
            route.update(attempts=0, initialized=False, due=0.0, force=False)
            self.rows[route["no"]] = {"no": route["no"], "file": route["file"], "state": "대기",
                "message": "첫 수집 대기", "last_run_utc": None, "next_run_utc": utc()}
        self.active = {}
        self.completed = set()
        self.changed = 0
        self.running = True
        self.last_control = None

    def publish(self):
        payload = {"updated_utc": utc(), "running": self.running, "active": len(self.active),
            "completed": len(self.completed), "total": len(self.routes), "changed": self.changed,
            "rows": [self.rows[route["no"]] for route in self.routes]}
        try:
            self.temp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(self.temp_path, self.state_path)
        except OSError:
            pass  # GUI의 짧은 상태 읽기와 겹치면 다음 tick에 다시 게시한다.

    def control(self):
        try:
            command = json.loads(self.control_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return
        identifier = command.get("id")
        if not identifier or identifier == self.last_control:
            return
        self.last_control = identifier
        if command.get("command") == "stop":
            self.running = False
        elif command.get("command") == "collect_now":
            for route in self.routes:
                route["due"] = 0.0
                route["force"] = route["no"] in self.active
                if route["no"] not in self.active:
                    self.rows[route["no"]]["next_run_utc"] = utc()

    def start(self, route):
        number = route["no"]
        row = self.rows[number]
        row.update(state="수집 중", message="초기 이력 수집" if not route["initialized"] else "추가 자료 수집",
                   last_run_utc=utc(), next_run_utc=None)
        before = file_snapshot(route)
        flags = route["args_repeat"] if route["initialized"] else route["args_first"]
        script = str(HERE / route["file"])
        arguments = [script] + flags
        day = utc()[:10]
        if route["kind"] == "CoinBar" and route.get("history_day") != day:
            arguments = [str(HERE / "spot_history.py"), route["tag"]] + arguments
        if route["kind"] == "UsBar" and not route["initialized"] and flags == ["--days", "20"]:
            arguments = [script, "--days", "23"]
        environment = os.environ.copy()
        environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        # 파이썬/HTTP 라이브러리의 중간 캐시와 임시 파일도 삭제 폴더에 둔다.
        environment["TEMP"] = environment["TMP"] = str(self.temp_path.parent)
        try:
            process = subprocess.Popen([sys.executable, "-B", "-u"] + arguments,
                cwd=str(HERE), env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            route["attempts"] += 1
            route["due"] = time.time() + route["interval"]
            row.update(state="실패", message="수집기 프로세스 시작 실패", next_run_utc=utc(route["due"]))
            self.completed.add(number)
            self.changed += 1
            return
        capture = Capture(process, self.log_dir / ("%02d.log" % number), "\n[" + utc() + "] " + route["file"] + "\n")
        capture.start()
        route["force"] = False
        self.active[number] = {"route": route, "process": process, "capture": capture,
                               "before": before, "started": time.monotonic(), "timeout": False}

    def finish(self, job):
        route, process, capture = job["route"], job["process"], job["capture"]
        capture.join(timeout=1.0)
        facts, existing = collected_rows(route, job["before"])
        tail = capture.tail.decode("utf-8", errors="replace").lower()
        if route["kind"] == "CoinBar" and process.returncode == 0 and "completed historical spot bars" in tail:
            route["history_day"] = utc()[:10]
        key_needed = facts["key"] or any(word in tail for word in ("missing_key", "missing key", "api key required", "api_key is required", "fred_api_key not", "fred_api_key is not"))
        errors = facts["error"] + facts["time"]
        if job["timeout"]:
            state, message = "시간 초과", "제한 %d초 초과 · 다음 주기 재시도" % route["timeout"]
        elif key_needed:
            state, message = "키 필요", "원천 API 키 설정 필요"
        elif process.returncode != 0:
            state, message = "실패", "수집기 종료코드 %s" % process.returncode
        elif facts["valid"]:
            state, message = "수신", "갱신 유효 %d행" % facts["valid"]
            if errors:
                message += " · 제외 %d행" % errors
        elif facts["time"]:
            state, message = "실패", "원본 관측시각 확인 대기"
        elif errors or any(word in tail for word in ("http_error", "connectionerror", "sslerror", "max retries", "rate_limited")):
            state, message = "실패", "HTTP·원천 오류 또는 유효 필드 누락"
        elif facts["closed"] or any(word in tail for word in ("market closed", "market_closed", "휴장")):
            state, message = "변경 없음", "휴장·정규장 대기"
        elif existing:
            state, message = "변경 없음", "신규 없음 · 기존 유효 표본 %d행" % existing
        else:
            state, message = "실패", "유효 저장 행 없음"
        if state in {"시간 초과", "실패", "키 필요"} and facts["valid"]:
            message += " · 저장 유효 %d행" % facts["valid"]
        if state in {"수신", "변경 없음"} and (facts["valid"] or existing):
            route["initialized"] = True
        route["attempts"] += 1
        route["due"] = time.time() if route["force"] else time.time() + route["interval"]
        route["force"] = False
        self.rows[route["no"]].update(state=state, message=message, next_run_utc=utc(route["due"]))
        self.completed.add(route["no"])
        self.changed += 1

    def reap(self):
        for number, job in list(self.active.items()):
            process = job["process"]
            if process.poll() is None and time.monotonic() - job["started"] >= job["route"]["timeout"]:
                job["timeout"] = True
                try:
                    process.kill()
                except OSError:
                    pass
            if process.poll() is not None:
                self.finish(job)
                del self.active[number]

    def stop(self):
        for job in self.active.values():
            if job["process"].poll() is None:
                try:
                    job["process"].terminate()
                except OSError:
                    pass
        for job in self.active.values():
            process = job["process"]
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass  # C# 부모의 JobObject가 남은 프로세스까지 종료한다.
            job["capture"].join(timeout=0.5)
            self.rows[job["route"]["no"]].update(state="대기", message="수집 중지", next_run_utc=None)
        self.active.clear()
        self.running = False
        self.publish()
        self.parent.close()

    def run(self):
        self.publish()
        last_publish = 0.0
        try:
            while self.running and self.parent.alive():
                self.control()
                if not self.running:
                    break
                self.reap()
                due = [route for route in self.routes if route["no"] not in self.active and route["due"] <= time.time()]
                # 첫 수집을 아직 시작하지 않은 원천이 정기 재수집에 밀리지 않게 한다.
                due.sort(key=lambda route: (route["attempts"] > 0, ESSENTIAL.get(route["no"],
                    (100 if route["no"] <= 70 else 1000) + route["no"]), route["due"]))
                for route in due[:max(0, 4 - len(self.active))]:
                    self.start(route)
                if time.monotonic() - last_publish >= 1:
                    self.publish()
                    last_publish = time.monotonic()
                time.sleep(0.25)
        finally:
            self.stop()


def main():
    parser = argparse.ArgumentParser(description="Market Dashboard collector process host")
    parser.add_argument("--state", required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    if args.parent_pid <= 0 or not Path(args.state).is_absolute() or not Path(args.control).is_absolute():
        parser.error("absolute state/control paths and a positive parent PID are required")
    host = Host(args)
    def interrupt(_signum, _frame):
        host.running = False
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    host.run()


if __name__ == "__main__":
    main()
