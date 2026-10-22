"""Real worker/installed parser; scripted local TCP object, never an external socket."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from overnight_quant.data import minute_local_prepare_worker as worker

MODE = sys.argv[1] if len(sys.argv) > 1 else "complete"
DIRECTORY = Path(sys.argv[2]) if len(sys.argv) > 2 else None
TASK = None
COUNTS = {"external_network_attempts": 0, "connect": 0, "handshake": 0, "bars": 0}


def varint(value):
    result = bytearray([value & 0x3f])
    value >>= 6
    while value:
        result[-1] |= 0x80
        result.append(value & 0x7f)
        value >>= 7
    return bytes(result)


def body():
    day = datetime.fromisoformat(TASK["trade_date"])
    packed = ((day.year - 2004) << 11) + day.month * 100 + day.day
    result = bytearray(struct.pack("<H", 12))
    for index, minute in enumerate(range(39, 51)):
        result.extend(struct.pack("<HH", packed, 14 * 60 + minute))
        result.extend(varint(10000 if index == 0 else 0) + b"\x00\x00\x00")
        result.extend(struct.pack("<II", 0x41000000, 0x42000000))
    return bytes(result)


class SocketFixture:
    def __init__(self, *args):
        self.buffer, self.sent, self._closed = bytearray(), 0, False
        for key in ("send_pkg_num", "recv_pkg_num", "send_pkg_bytes", "recv_pkg_bytes",
                    "last_api_send_bytes", "last_api_recv_bytes"):
            setattr(self, key, 0)
        self.first_pkg_send_time = self.first_pkg_recv_time = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        sys.audit("socket.connect", self, address)
        COUNTS["connect"] += 1
        worker.emit({"kind": "fixture_connect", "monotonic": time.perf_counter(), "test_only": True})
        if DIRECTORY:
            (DIRECTORY / "connected").write_text(str(os.getpid()))
        time.sleep(.015)

    def send(self, raw):
        self.sent += 1
        kind = "handshake" if self.sent <= 3 else "bars"
        COUNTS[kind] += 1
        if kind == "bars" and MODE in {"work_timeout", "parent_death"}:
            if DIRECTORY:
                (DIRECTORY / "bars-blocked").write_text(str(os.getpid()))
            time.sleep(30)
        value = b"\x01" if kind == "handshake" else body()
        self.buffer = bytearray(struct.pack("<IIIHH", 0, 0, 0, len(value), len(value)) + value)
        time.sleep(.001)
        return len(raw)

    def recv(self, count):
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value

    def shutdown(self, how):
        pass

    def close(self):
        self._closed = True


def configure(task, guard):
    global TASK
    TASK = task
    if DIRECTORY:
        (DIRECTORY / "worker.pid").write_text(str(os.getpid()))

    def external_guard(event, args):
        if event == "socket.connect" and not isinstance(args[0], SocketFixture):
            COUNTS["external_network_attempts"] += 1
            raise AssertionError("external network forbidden by fixture")

    sys.addaudithook(external_guard)
    import mootdx.config as config
    import tdxpy.base_socket_client as base
    config.CONF = task["config"]["path"]
    base.TrafficStatSocket = SocketFixture
    # Explicit synthetic wall-time axis, monotonic deadlines are never changed.
    worker.native.now = lambda: (datetime.fromisoformat(task["scheduled_at"])
        + timedelta(seconds=time.perf_counter() - task["target_monotonic"])).isoformat()
    if MODE == "swallowed_network":
        import socket
        try:
            socket.getaddrinfo("test-only.invalid", 80)
        except RuntimeError:
            pass


def before_ready(task, guard):
    if MODE == "ready_timeout":
        time.sleep(30)


def ready(value):
    if MODE == "wrong_ready":
        value["batch_id"] = "wrong-synthetic-batch"
    return value


def after_capture(task, guard):
    if MODE == "orphan_pipe":
        # Only the test teardown scenario may create a local descendant after
        # capture. Production preparation never grants a helper-process permit.
        # Audit hooks cannot be removed. Only this isolated fixture permits a
        # local helper, after capture, to test inherited-pipe cleanup.
        permit["helpers"] = True
        child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        if DIRECTORY:
            (DIRECTORY / "descendant.pid").write_text(str(child.pid))
        worker.emit({"ok": True, "payload": {"test_only": True}})
        os._exit(0)
    worker.emit({"kind": "fixture_counts", "counts": dict(COUNTS), "test_only": True})


permit = {"helpers": False}
original_audit = worker.NetworkBoundary.audit


def fixture_audit(self, event, args):
    if permit["helpers"] and event == "subprocess.Popen":
        return
    return original_audit(self, event, args)


worker.NetworkBoundary.audit = fixture_audit
raise SystemExit(worker.main(_test={"configure": configure, "before_ready": before_ready,
                                  "ready": ready, "after_capture": after_capture}))
