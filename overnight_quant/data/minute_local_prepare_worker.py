"""Single-wave candidate worker. Imports/config/parser only before one GO + EOF."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time

from . import minute_local_prepare_contract as contract
from . import minute_capture_worker as native


class NetworkBoundary:
    """Pinned Python socket path only, not a sandbox for arbitrary native code."""
    def __init__(self):
        self.released = False
        self.attempts = []
        self.allowed_connections = 0

    def audit(self, event, args):
        dns = event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
                        "socket.getnameinfo"}
        network = dns or event in {"socket.connect", "socket.sendto", "socket.sendmsg"}
        helper = event in {"subprocess.Popen", "os.system", "os.posix_spawn"}
        if not network and not helper:
            return
        approved = (self.released and event == "socket.connect" and len(args) > 1
                    and args[1] == (native.ENDPOINT["host"], native.ENDPOINT["port"])
                    and self.allowed_connections == 0)
        if approved:
            self.allowed_connections += 1
            return
        # Increment before raising: callers swallowing the exception cannot pass.
        self.attempts.append({"event": event, "before_go": not self.released,
                              "at": native.now(), "monotonic": time.perf_counter()})
        raise RuntimeError("prepared_network_or_helper_boundary_violation:" + event)

    def assert_clean(self):
        if self.attempts:
            raise ValueError("prepared_boundary_attempt_recorded_even_if_swallowed")


def emit(value):
    print(native.encoded({"execution_worker_version": contract.WORKER_VERSION, **value}), flush=True)


class ProtocolInput:
    """Bounded raw input polling; no buffered readline or background reader thread."""
    def __init__(self):
        if os.name != "nt":
            raise ValueError("prepared_protocol_requires_windows_pipe")
        import ctypes
        import msvcrt
        from ctypes import wintypes
        self.ctypes = ctypes
        self.fd = sys.stdin.fileno()
        self.handle = msvcrt.get_osfhandle(self.fd)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.kernel.PeekNamedPipe.restype = wintypes.BOOL
        self.buffer, self.eof = bytearray(), False

    def step(self, deadline):
        if time.perf_counter() >= deadline:
            raise TimeoutError("prepared_protocol_absolute_input_deadline")
        available = self.ctypes.c_ulong()
        if not self.kernel.PeekNamedPipe(self.handle, None, 0, None, self.ctypes.byref(available), None):
            error = self.ctypes.get_last_error()
            if error in (109, 232):
                self.eof = True
                return
            raise self.ctypes.WinError(error)
        if available.value:
            self.buffer.extend(os.read(self.fd, min(4097, available.value)))
            if len(self.buffer) > 4096:
                raise ValueError("prepared_protocol_input_exceeds_4096_bytes")
        else:
            time.sleep(min(.001, max(0, deadline - time.perf_counter())))

    def line(self, deadline):
        while b"\n" not in self.buffer:
            if self.eof:
                raise ValueError("prepared_protocol_line_or_parent_eof_invalid")
            self.step(deadline)
        raw, rest = self.buffer.split(b"\n", 1)
        self.buffer = bytearray(rest)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("prepared_protocol_object_required")
        return value

    def require_eof(self, deadline):
        while not self.eof:
            if self.buffer:
                raise ValueError("prepared_duplicate_GO_or_message")
            self.step(deadline)
        if self.buffer:
            raise ValueError("prepared_duplicate_GO_or_message")


def parent_chain():
    """Windows venv redirectors can sit between Popen and the actual interpreter."""
    if os.name != "nt":
        return [os.getpid(), os.getppid()]
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", ctypes.c_long), ("flags", wintypes.DWORD),
            ("exe", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes, function.restype = [wintypes.HANDLE, ctypes.POINTER(Entry)], wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        row = Entry()
        row.size = ctypes.sizeof(row)
        parents = {}
        more = kernel.Process32FirstW(handle, ctypes.byref(row))
        while more:
            parents[row.pid] = row.parent
            more = kernel.Process32NextW(handle, ctypes.byref(row))
        chain = [os.getpid()]
        for _ in range(4):
            if chain[-1] not in parents or parents[chain[-1]] in chain:
                break
            chain.append(parents[chain[-1]])
        return chain
    finally:
        kernel.CloseHandle(handle)


def prepare_local(task, guard, *, emit_fn=None, codes=None, parser_requests=None):
    output = emit_fn or emit
    from importlib.metadata import version
    with native.client_stage(output, "local_dependency_imports"):
        actual = {package: version(package) for package in contract.DEPENDENCIES}
        if actual != contract.DEPENDENCIES:
            raise ValueError("prepared_dependency_version_mismatch")
        from mootdx import config
        from mootdx.quotes import Quotes  # Import only; factory connects and is forbidden here.
        from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
    with native.client_stage(output, "local_config_and_parser_validation"):
        if Path(config.CONF).resolve() != Path(task["config"]["path"]).resolve():
            raise ValueError("prepared_config_path_mismatch")
        raw = Path(config.CONF).read_bytes()
        if sha256(raw).hexdigest() != task["config"]["sha256"]:
            raise ValueError("prepared_config_external_sha256_mismatch")
        value = json.loads(raw)
        if (not isinstance(value, dict) or not isinstance(value.get("SERVER"), dict)
                or not isinstance(value["SERVER"].get("HQ"), list) or not value["SERVER"]["HQ"]):
            raise ValueError("prepared_local_config_invalid_no_discovery")
        requests = parser_requests if parser_requests is not None else [
            {"symbol": code, "frequency": "1m", "start": 0, "offset": 800}
            for code in (native.CODES if codes is None else codes)]
        for request in requests:
            code = request["symbol"]
            parser = GetSecurityBarsCmd(None)
            parser.setParams(8, 1 if code.startswith("6") else 0, code, request["start"], request["offset"])
    guard.assert_clean()
    return actual


@contextmanager
def connection_stages(*, emit_fn=None):
    output = emit_fn or emit
    from tdxpy.hq import TdxHq_API
    original_connect, original_setup = TdxHq_API.connect, TdxHq_API.setup

    def connect(self, *args, **kwargs):
        with native.client_stage(output, "connect_including_handshakes"):
            return original_connect(self, *args, **kwargs)

    def setup(self):
        with native.client_stage(output, "three_handshakes"):
            return original_setup(self)

    TdxHq_API.connect, TdxHq_API.setup = connect, setup
    try:
        yield
    finally:
        TdxHq_API.connect, TdxHq_API.setup = original_connect, original_setup


def main(*, _test=None):
    guard = NetworkBoundary()
    # Installed before any mootdx/tdxpy import, including fixture configuration.
    sys.addaudithook(guard.audit)
    task = None
    try:
        input_stream = ProtocolInput()
        task = input_stream.line(time.perf_counter() + 3.5)
        contract.check_task(task)
        ancestry = parent_chain()
        if (task["parent_pid"] not in ancestry[1:] or task.get("launch_root_pid") not in ancestry
                or ancestry.index(task["launch_root_pid"]) >= ancestry.index(task["parent_pid"])):
            raise ValueError("prepared_parent_identity_mismatch")
        if task.get("test_only", False) is not bool(_test):
            raise ValueError("prepared_test_scope_mismatch")
        if contract.worker_file_sha256() != task["worker_sha256"]:
            raise ValueError("prepared_worker_file_identity_mismatch")
        if time.perf_counter() >= task["preparation_work_deadline"]:
            raise TimeoutError("preparation_work_deadline_exhausted")
        if _test:
            _test["configure"](task, guard)
        versions = prepare_local(task, guard)
        if _test and _test.get("before_ready"):
            _test["before_ready"](task, guard)
        guard.assert_clean()
        ready_at = time.perf_counter()
        if ready_at >= task["preparation_work_deadline"]:
            raise TimeoutError("preparation_ready_deadline_exhausted")
        ready = {"kind": "READY_LOCAL_ONLY", "task_hash": native.digest(task),
                 "worker_version": contract.WORKER_VERSION, "pid": os.getpid(),
                 "dependencies": versions, "config": task["config"], "code_sha": task["code_sha"],
                 "batch_id": task["batch_id"], "wave": task["wave"], "plan_hash": task["plan_hash"],
                 "ready_monotonic": ready_at, "network_attempts_before_go": len(guard.attempts)}
        ready["parent_chain"] = ancestry
        if _test and _test.get("ready"):
            ready = _test["ready"](ready)
        emit(ready)
        message = input_stream.line(task["io_deadline_monotonic"])
        received = time.perf_counter()
        contract.check_go(message, task, received)
        # Parent closes input after exactly one GO. A second message is rejected
        # before connecting; no open-input wait can outlive the parent's budget.
        input_stream.require_eof(task["io_deadline_monotonic"])
        if time.perf_counter() >= task["io_deadline_monotonic"]:
            raise TimeoutError("prepared_GO_no_remaining_work_budget")
        guard.assert_clean()
        guard.released = True
        emit({"kind": "GO_ACCEPTED", "task_hash": native.digest(task), "monotonic": time.perf_counter(),
              "received_monotonic": received, "sent_monotonic": message["sent_monotonic"],
              "network_attempts_before_go": len(guard.attempts)})

        def tracked(value):
            if value.get("kind") == "client_config" and value.get("raw_hash") != task["config"]["sha256"]:
                raise ValueError("prepared_config_changed_before_connect")
            guard.assert_clean()
            emit(value)

        native_task = {**task, "worker_version": native.VERSION,
                       "parent_started_at": task["scheduled_at"],
                       "parent_started_monotonic": task["target_monotonic"]}
        with connection_stages():
            captured = native.capture_wave(native_task,
                client_factory=lambda: native.NativeClient(emit=tracked), emit=tracked)
        guard.assert_clean()
        if _test and _test.get("after_capture"):
            _test["after_capture"](task, guard)
        emit({"ok": True, "payload": {"capture": captured, "test_only": bool(_test),
              "boundary_attempts": guard.attempts, "allowed_connections": guard.allowed_connections}})
        return 0
    except Exception as exc:
        emit({"ok": False, "error_code": "PREPARED_MINUTE_WORKER_FAILED",
              "error": f"{type(exc).__name__}:{exc}", "boundary_attempts": guard.attempts,
              "exception_chain": native.exception_chain(exc), "test_only": bool(_test)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
