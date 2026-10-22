from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable


WORKER_TIMEOUT_ERROR = "REQUEST_DEADLINE_EXCEEDED"
WORKER_OUTPUT_ERROR = "WORKER_OUTPUT_INVALID"
WORKER_PROCESS_ERROR = "WORKER_PROCESS_FAILED"
WORKER_CLEANUP_ERROR = "WORKER_CLEANUP_INCOMPLETE"


def run_probe_worker_process(
    task: dict[str, Any],
    deadline_ms: int,
    *,
    python_executable: str | None = None,
    worker_command: list[str] | None = None,
    monotonic: Callable[[], float] | None = None,
    termination_grace_seconds: float = 0.5,
    include_startup_in_deadline: bool = False,
    retain_streams: bool = False,
    bounded_cleanup_ms: int | None = None,
    hard_deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Run one provider request in a killable child process."""
    if bounded_cleanup_ms is not None:
        return _run_bounded_worker(
            task, deadline_ms, worker_command=worker_command,
            python_executable=python_executable, monotonic=monotonic or time.perf_counter,
            cleanup_ms=bounded_cleanup_ms, retain_streams=retain_streams,
            hard_deadline=hard_deadline_monotonic,
        )
    runtime_monotonic = monotonic or time.monotonic
    command = list(worker_command or [
        python_executable or sys.executable,
        "-m",
        "overnight_quant.data.minute_probe_worker",
    ])
    creationflags = (
        subprocess.CREATE_NO_WINDOW
        if os.name == "nt"
        else 0
    )
    started = runtime_monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    worker_terminated = False
    startup_ms = None
    try:
        remaining = max(1, int(deadline_ms)) / 1000.0
        if include_startup_in_deadline:
            startup_ms = (runtime_monotonic() - started) * 1000
            remaining -= startup_ms / 1000
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, 0)
        stdout, stderr = process.communicate(
            json.dumps(task, ensure_ascii=False),
            timeout=remaining,
        )
    except subprocess.TimeoutExpired:
        worker_terminated = True
        cleanup_started = runtime_monotonic() if retain_streams else None
        _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(
                timeout=max(0.05, float(termination_grace_seconds))
            )
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        elapsed_ms = round((runtime_monotonic() - started) * 1000, 3)
        return {
            "ok": False,
            "error_code": WORKER_TIMEOUT_ERROR,
            "error": WORKER_TIMEOUT_ERROR,
            "request_timed_out": True,
            "worker_terminated": worker_terminated,
            "elapsed_ms": elapsed_ms,
            "returncode": process.returncode,
            **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
            **({"process_startup_ms": startup_ms,
                "cleanup_ms": elapsed_ms - (cleanup_started - started) * 1000}
               if retain_streams else {}),
        }

    elapsed_ms = round(
        (runtime_monotonic() - started) * 1000,
        3,
    )
    payload = _decode_worker_output(stdout)
    diagnostics = ({"process_startup_ms": startup_ms, "cleanup_ms": 0.0,
                    "io_and_normal_exit_ms": elapsed_ms - (startup_ms or 0.0)} if retain_streams else {})
    if payload is None:
        return {
            "ok": False,
            "error_code": WORKER_OUTPUT_ERROR,
            "error": _bounded_error(stderr or stdout),
            "request_timed_out": False,
            "worker_terminated": False,
            "elapsed_ms": elapsed_ms,
            "returncode": process.returncode,
            **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
            **diagnostics,
        }
    if process.returncode != 0 or payload.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(
                payload.get("error_code") or WORKER_PROCESS_ERROR
            ),
            "error": str(
                payload.get("error")
                or _bounded_error(stderr)
                or WORKER_PROCESS_ERROR
            ),
            "request_timed_out": False,
            "worker_terminated": False,
            "elapsed_ms": elapsed_ms,
            "returncode": process.returncode,
            **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
            **diagnostics,
        }
    return {
        "ok": True,
        "payload": payload.get("payload") or {},
        "error_code": "",
        "error": "",
        "request_timed_out": False,
        "worker_terminated": False,
        "elapsed_ms": elapsed_ms,
        "returncode": process.returncode,
        **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
        **diagnostics,
    }


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
    else:
        process.terminate()


def _decode_worker_output(value: str) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text, *reversed(text.splitlines())]
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _bounded_error(value: str, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


class _ProcessScope:
    """Track descendants even after the root exits; never shell out to taskkill."""

    def __init__(self, process, *, kill_on_parent_close=False):
        self.process = process
        self.handle = None
        self.kind = "posix_process_group"
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            self.ctypes = ctypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            class Accounting(ctypes.Structure):
                _fields_ = [(name, ctypes.c_int64) for name in (
                    "total_user", "total_kernel", "period_user", "period_kernel")]
                _fields_ += [(name, wintypes.DWORD) for name in (
                    "page_faults", "total_processes", "active_processes", "terminated_processes")]
            self.Accounting = Accounting
            signatures = {
                "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
                "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
                "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
                "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
                "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            }
            for name, (arguments, result) in signatures.items():
                function = getattr(self.kernel, name)
                function.argtypes, function.restype = arguments, result
            self.handle = self.kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            if kill_on_parent_close:
                class BasicLimits(ctypes.Structure):
                    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
                class IoCounters(ctypes.Structure):
                    _fields_ = [(name, ctypes.c_uint64) for name in (
                        "read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
                class ExtendedLimits(ctypes.Structure):
                    _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
                limits = ExtendedLimits()
                limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                setter = self.kernel.SetInformationJobObject
                setter.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
                setter.restype = wintypes.BOOL
                if not setter(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                    error = ctypes.WinError(ctypes.get_last_error())
                    self.close()
                    raise error
            if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error
            self.kind = "windows_job"

    def empty(self):
        if os.name == "nt":
            values = self.Accounting()
            if not self.kernel.QueryInformationJobObject(self.handle, 1, self.ctypes.byref(values),
                                                         self.ctypes.sizeof(values), None):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            return values.active_processes == 0
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            return True
        return False

    def terminate(self):
        if os.name == "nt":
            if not self.kernel.TerminateJobObject(self.handle, 1):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
        else:
            import signal
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def contains(self, pid):
        """Verify the actual interpreter belongs to this tracked job, not just its launcher."""
        if os.name != "nt":
            return os.getpgid(pid) == self.process.pid
        from ctypes import wintypes
        opener = self.kernel.OpenProcess
        opener.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        opener.restype = wintypes.HANDLE
        member = self.kernel.IsProcessInJob
        member.argtypes = [wintypes.HANDLE, wintypes.HANDLE, self.ctypes.POINTER(wintypes.BOOL)]
        member.restype = wintypes.BOOL
        handle = opener(0x1000, False, pid)
        if not handle:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            result = wintypes.BOOL()
            if not member(handle, self.handle, self.ctypes.byref(result)):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            return bool(result.value)
        finally:
            self.kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise self.ctypes.WinError(self.ctypes.get_last_error())


class _PipeDrain:
    """One caller, raw unbuffered handles, no blocked communicate/reader threads."""

    def __init__(self, process):
        self.streams = {"stdout": process.stdout, "stderr": process.stderr}
        self.chunks = {name: [] for name in self.streams}
        self.eof = {name: False for name in self.streams}
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            self.ctypes, self.msvcrt = ctypes, msvcrt
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            self.kernel.PeekNamedPipe.restype = wintypes.BOOL
        else:
            for stream in self.streams.values():
                os.set_blocking(stream.fileno(), False)

    def step(self):
        received = False
        for name, stream in self.streams.items():
            if self.eof[name]:
                continue
            size = 65536
            if os.name == "nt":
                available = self.ctypes.c_ulong()
                handle = self.msvcrt.get_osfhandle(stream.fileno())
                if not self.kernel.PeekNamedPipe(handle, None, 0, None,
                                                self.ctypes.byref(available), None):
                    error = self.ctypes.get_last_error()
                    if error in (109, 232):  # broken/closed pipe, including inherited handles
                        self.eof[name] = True
                        continue
                    raise self.ctypes.WinError(error)
                if not available.value:
                    continue
                size = min(size, available.value)
            try:
                data = os.read(stream.fileno(), size)
            except BlockingIOError:
                continue
            if data:
                self.chunks[name].append(data)
                received = True
            else:
                self.eof[name] = True
        return received

    def text(self, name):
        return b"".join(self.chunks[name]).decode("utf-8", errors="replace")


def _resume_suspended_primary(process):
    """Resume a new-path root only after its Job owns all future descendants."""
    import ctypes
    from ctypes import wintypes
    class ThreadEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("tid", wintypes.DWORD),
                    ("owner", wintypes.DWORD), ("base_priority", ctypes.c_long),
                    ("delta_priority", ctypes.c_long), ("flags", wintypes.DWORD)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Thread32First", "Thread32Next"):
        function = getattr(kernel, name)
        function.argtypes, function.restype = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)], wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes, kernel.ResumeThread.restype = [wintypes.HANDLE], wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry, ids = ThreadEntry(), []
        entry.size = ctypes.sizeof(entry)
        more = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.owner == process.pid:
                ids.append(entry.tid)
            more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        if len(ids) != 1:
            raise ValueError("suspended_primary_thread_identity_invalid")
        handle = kernel.OpenThread(0x2, False, ids[0])
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if kernel.ResumeThread(handle) != 1:
                raise ValueError("suspended_primary_resume_count_invalid")
        finally:
            kernel.CloseHandle(handle)
    finally:
        kernel.CloseHandle(snapshot)


def _bounded_cleanup(process, scope, drain, *, deadline, monotonic):
    """One absolute cleanup deadline shared by termination, draining and close."""
    errors, timings = [], {}
    tree_empty = pipes_closed = close_complete = False
    terminated = False
    started = monotonic()
    try:
        if process is not None:
            tree_empty = scope.empty() if scope is not None else False
            if (process.poll() is None or not tree_empty) and monotonic() < deadline:
                terminated = True
                scope.terminate() if scope is not None else process.kill()
    except Exception as exc:
        errors.append(f"terminate:{type(exc).__name__}:{exc}")
    timings["termination_ms"] = (monotonic() - started) * 1000
    drain_started = monotonic()
    try:
        while process is not None and monotonic() < deadline:
            received = drain.step() if drain is not None else False
            root_exited = process.poll() is not None
            tree_empty = scope.empty() if scope is not None else False
            if root_exited and tree_empty and drain is not None and all(drain.eof.values()):
                break
            if not received:
                time.sleep(min(.002, max(0, deadline - monotonic())))
    except Exception as exc:
        errors.append(f"drain_or_exit:{type(exc).__name__}:{exc}")
    timings["drain_and_exit_ms"] = (monotonic() - drain_started) * 1000
    close_started = monotonic()
    try:
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            pipes_closed = True
        if scope is not None:
            scope.close()
        close_complete = True
    except Exception as exc:
        errors.append(f"close:{type(exc).__name__}:{exc}")
    timings["pipe_and_scope_close_ms"] = (monotonic() - close_started) * 1000
    complete = close_complete and (process is None or (process.poll() is not None and tree_empty
        and drain is not None and all(drain.eof.values()) and pipes_closed))
    return {"cleanup_complete": complete and monotonic() <= deadline,
            "worker_terminated": terminated, "tree_empty_observed": tree_empty,
            "pipes_closed": pipes_closed, "diagnostics": errors, **timings}


def _run_bounded_worker(task, deadline_ms, *, worker_command, python_executable,
                        monotonic, cleanup_ms, retain_streams, hard_deadline):
    started = monotonic()
    deadline = started + max(0, deadline_ms) / 1000
    if hard_deadline is not None:
        deadline = min(deadline, hard_deadline)
    reserve = max(0, cleanup_ms) / 1000
    io_deadline = deadline - reserve
    process = scope = drain = None
    errors, timings = [], {}
    timed_out = terminated = False
    tree_empty = pipes_closed = False
    io_completed = started
    cleanup_complete = close_complete = False
    cleanup_deadline = deadline
    stage = "prepare"
    try:
        value = json.dumps(task, ensure_ascii=False).encode("utf-8")
        # Fits one empty anonymous input pipe; no communicate writer can get stuck.
        if len(value) > 4096:
            raise ValueError("bounded_worker_input_exceeds_4096_bytes")
        if monotonic() >= io_deadline:
            raise subprocess.TimeoutExpired(worker_command, 0)
        stage = "startup"
        process = subprocess.Popen(
            list(worker_command or [python_executable or sys.executable, "-m",
                                    "overnight_quant.data.minute_probe_worker"]),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        # Our worker reads stdin first: establish its descendant scope before release.
        scope = _ProcessScope(process)
        drain = _PipeDrain(process)
        timings["process_startup_ms"] = (monotonic() - started) * 1000
        if monotonic() >= io_deadline:
            raise subprocess.TimeoutExpired(worker_command, 0)
        process.stdin.write(value)
        process.stdin.close()
        stage = "read_and_normal_exit"
        while monotonic() < io_deadline:
            received = drain.step()
            if process.poll() is not None and all(drain.eof.values()) and scope.empty():
                break
            if not received:
                time.sleep(min(.002, max(0, io_deadline - monotonic())))
        else:
            raise subprocess.TimeoutExpired(worker_command, max(0, io_deadline - started))
    except subprocess.TimeoutExpired:
        timed_out = True
    except Exception as exc:
        errors.append(f"{stage}:{type(exc).__name__}:{exc}")
    finally:
        io_completed = monotonic()
        # One shared cleanup deadline, not a fresh reserve for each operation.
        cleanup_deadline = min(deadline, io_completed + reserve)
        timings["io_and_startup_ms"] = (io_completed - started) * 1000
        cleaned = _bounded_cleanup(process, scope, drain, deadline=cleanup_deadline, monotonic=monotonic)
        terminated = cleaned["worker_terminated"]
        tree_empty, pipes_closed = cleaned["tree_empty_observed"], cleaned["pipes_closed"]
        cleanup_complete = cleaned["cleanup_complete"]
        errors.extend(cleaned["diagnostics"])
        timings.update({key: value for key, value in cleaned.items() if key.endswith("_ms")})
        timings["cleanup_ms"] = (monotonic() - io_completed) * 1000
    stdout, stderr = (drain.text("stdout"), drain.text("stderr")) if drain else ("", "")
    decode_started = monotonic()
    payload = _decode_worker_output(stdout) if cleanup_complete and not timed_out and not errors else None
    elapsed_ms = (monotonic() - started) * 1000
    timings["decode_ms"] = (monotonic() - decode_started) * 1000
    cleanup_finished = monotonic()
    timings["cleanup_ms"] = (cleanup_finished - io_completed) * 1000
    cleanup_complete = cleanup_complete and cleanup_finished < cleanup_deadline
    over_budget = monotonic() > deadline
    ok = (cleanup_complete and not timed_out and not over_budget and not errors
          and payload is not None and process is not None and process.returncode == 0
          and payload.get("ok") is True)
    code = (WORKER_CLEANUP_ERROR if not cleanup_complete else WORKER_TIMEOUT_ERROR if timed_out or over_budget
            else WORKER_PROCESS_ERROR if errors or (process is not None and process.returncode != 0)
            else WORKER_OUTPUT_ERROR if payload is None else str(payload.get("error_code") or WORKER_PROCESS_ERROR))
    return {
        "ok": ok, "payload": (payload.get("payload") or {}) if ok else {},
        "error_code": "" if ok else code,
        "error": "" if ok else (";".join(errors) or (payload or {}).get("error") or code),
        "request_timed_out": timed_out or over_budget, "worker_terminated": terminated,
        "returncode": process.poll() if process is not None else None,
        "pid": process.pid if process is not None else None,
        "cleanup_complete": cleanup_complete, "cleanup_budget_exhausted": cleanup_finished >= cleanup_deadline,
        "tree_empty_observed": tree_empty if scope is not None else None,
        "tree_tracking": scope.kind if scope is not None else "UNCONFIRMED",
        "stdout_eof": drain.eof["stdout"] if drain else False,
        "stderr_eof": drain.eof["stderr"] if drain else False,
        "pipes_closed": pipes_closed, "stage": stage, "diagnostics": errors,
        "elapsed_ms": round(elapsed_ms, 3), **timings,
        "hard_deadline_monotonic": deadline, "io_deadline_monotonic": io_deadline,
        "cleanup_deadline_monotonic": cleanup_deadline,
        **({"stdout": stdout, "stderr": stderr} if retain_streams else {}),
    }
