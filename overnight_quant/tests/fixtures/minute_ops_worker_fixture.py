"""Test-only installed-client exercise; all external socket operations are denied."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import struct
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
task = json.loads(sys.stdin.read())
mode, config_path = sys.argv[1:3]
counts = {"external_network_attempts": 0, "connect": 0, "handshake": 0,
          "bars": 0, "discovery": 0}


def emit(value):
    print(json.dumps({"test_only": True, **value}, sort_keys=True), flush=True)


def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg"}:
        counts["external_network_attempts"] += 1
        raise AssertionError("external network forbidden in minute fixture")


sys.addaudithook(deny_network)
emit({"kind": "fixture_network_guard_installed", "counts": dict(counts)})
import_started = time.perf_counter()
from overnight_quant.data import minute_capture_worker as worker
from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
import tdxpy.base_socket_client as base_socket
import tdxpy.hq as hq
import mootdx.config as config
import mootdx.quotes as quotes
emit({"kind": "fixture_stage", "stage": "cold_dependency_imports",
      "elapsed_ms": (time.perf_counter() - import_started) * 1000,
      "module_files": {"parser": sys.modules[GetSecurityBarsCmd.__module__].__file__,
                       "socket": base_socket.__file__, "quotes": quotes.__file__},
      "clock": time.get_clock_info("perf_counter").implementation})


def positive_varint(value):
    result = bytearray([value & 0x3f])
    value >>= 6
    while value:
        result[-1] |= 0x80
        result.append(value & 0x7f)
        value >>= 7
    return bytes(result)


def bars_body():
    day = datetime.fromisoformat(task["trade_date"])
    packed_day = ((day.year - 2004) << 11) + day.month * 100 + day.day
    result = bytearray(struct.pack("<H", 12))
    for index, minute in enumerate(range(39, 51)):
        result.extend(struct.pack("<HH", packed_day, 14 * 60 + minute))
        result.extend(positive_varint(10000 if index == 0 else 0))
        result.extend(b"\x00\x00\x00")
        result.extend(struct.pack("<II", 0x41000000, 0x42000000))
    return bytes(result)


body = b"\x20\x03" if mode in {"malformed", "slow_close", "slow_exit"} else bars_body()


class SocketFixture:
    def __init__(self, *args):
        self.buffer = bytearray()
        self.sent = 0
        self._closed = False
        for name in ("send_pkg_num", "recv_pkg_num", "send_pkg_bytes", "recv_pkg_bytes",
                     "last_api_send_bytes", "last_api_recv_bytes"):
            setattr(self, name, 0)
        self.first_pkg_send_time = self.first_pkg_recv_time = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        assert address == ("59.36.5.11", 7709)
        counts["connect"] += 1

    def send(self, data):
        self.sent += 1
        kind = "handshake" if self.sent <= 3 else "bars"
        counts[kind] += 1
        raw = b"\x01" if kind == "handshake" else body
        self.buffer = bytearray(struct.pack("<IIIHH", 0, 0, 0, len(raw), len(raw)) + raw)
        return len(data)

    def recv(self, count):
        raw = bytes(self.buffer[:count])
        del self.buffer[:count]
        return raw

    def shutdown(self, how):
        if mode == "slow_close":
            emit({"kind": "fixture_close_block_started", "counts": dict(counts)})
            time.sleep(30)

    def close(self):
        self._closed = True


def forbidden_discovery(*args, **kwargs):
    counts["discovery"] += 1
    raise AssertionError("test trapped implicit vendor server discovery")


base_socket.TrafficStatSocket = SocketFixture
config.CONF = config_path
config.bestip = forbidden_discovery
original_connect, original_setup = hq.TdxHq_API.connect, hq.TdxHq_API.setup


def measured_connect(self, *args, **kwargs):
    started = time.perf_counter()
    try:
        return original_connect(self, *args, **kwargs)
    finally:
        emit({"kind": "fixture_stage", "stage": "connect_including_handshakes",
              "elapsed_ms": (time.perf_counter() - started) * 1000, "counts": dict(counts)})


def measured_setup(self):
    started = time.perf_counter()
    try:
        return original_setup(self)
    finally:
        emit({"kind": "fixture_stage", "stage": "three_fixture_handshakes",
              "elapsed_ms": (time.perf_counter() - started) * 1000, "counts": dict(counts)})


hq.TdxHq_API.connect, hq.TdxHq_API.setup = measured_connect, measured_setup
factory = quotes.Quotes.factory


def measured_factory(**kwargs):
    started = time.perf_counter()
    try:
        return factory(**kwargs)
    finally:
        emit({"kind": "fixture_stage", "stage": "factory_config_connect_setup",
              "elapsed_ms": (time.perf_counter() - started) * 1000, "flags": kwargs,
              "counts": dict(counts)})


quotes.Quotes.factory = measured_factory
if mode == "imports":
    emit({"ok": True, "payload": {"test_only": True, "counts": counts}})
    raise SystemExit(0)

try:
    value = worker.capture_wave(task)
    emit({"ok": True, "payload": {"test_only": True, "counts": counts,
                                  "capture": value}})
except Exception as exc:
    original = getattr(exc, "original_exception", None)
    emit({"ok": False, "error_code": "TEST_ONLY_NATIVE_CLIENT_FAILED",
          "error": str(exc), "counts": counts,
          "exception_type": type(exc).__name__,
          "original_exception_type": type(original).__name__ if original else None,
          "original_exception_message": str(original) if original else None,
          "original_frames": [frame.name for frame in traceback.extract_tb(original.__traceback__)]
                             if original else []})
    if mode == "slow_exit":
        # Distinguish a terminal JSON line from actual OS process/pipe exit.
        time.sleep(30)
    raise SystemExit(2)
