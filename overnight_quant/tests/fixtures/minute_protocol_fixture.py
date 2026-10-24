"""Synthetic protocol bytes and a real-child fixture. No external connection."""
from collections import deque
from datetime import datetime
import os
from pathlib import Path
import struct
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def body():
    day = datetime(2026, 10, 24)
    packed = ((day.year - 2004) << 11) + day.month * 100 + day.day
    result = bytearray(struct.pack("<H", 12))
    for index, minute in enumerate(range(39, 51)):
        result.extend(struct.pack("<HH", packed, 14 * 60 + minute))
        number, encoded = (10000 if index == 0 else 0), bytearray()
        encoded.append(number & 0x3f)
        number >>= 6
        while number:
            encoded[-1] |= 0x80
            encoded.append(number & 0x7f)
            number >>= 7
        result.extend(encoded + b"\x00\x00\x00" + struct.pack("<II", 0x41000000, 0x42000000))
    return bytes(result)


def cases():
    raw, packed = body(), zlib.compress(body())
    header = lambda zip_size, unzip_size, prefix=(0, 0, 0): struct.pack("<IIIHH", *prefix, zip_size, unzip_size)
    return {
        "full": [header(len(raw), len(raw)), raw],
        "split_body": [header(len(raw), len(raw)), raw[:1], raw[1:7], raw[7:]],
        "split_header": [header(len(raw), len(raw))[:8], header(len(raw), len(raw))[8:], raw],
        "short": [header(2, 2), b"\x20\x03"],
        "long_declaration_eof": [header(len(raw)+10, len(raw)+10), raw, b""],
        "compressed": [header(len(packed), len(raw)), packed],
        "wrong_unzip": [header(len(packed), len(raw)+1), packed],
        "unknown_header_prefix": [header(len(raw), len(raw), (0xffffffff, 0x12345678, 0xaabbccdd)), raw],
        "overread": [header(len(raw), len(raw)), raw[:-2], raw[-2:]+header(1, 1)+b"\x01"],
    }


class SegmentSocket:
    def __init__(self, chunks):
        self.chunks, self.calls = deque(chunks), []
        for key in ("send_pkg_num", "recv_pkg_num", "send_pkg_bytes", "recv_pkg_bytes", "last_api_send_bytes", "last_api_recv_bytes"):
            setattr(self, key, 0)
        self.first_pkg_send_time = None

    def send(self, packet, *flags):
        self.calls.append(("send", bytes(packet), flags))
        return len(packet)

    def recv(self, count, *flags):
        if len(self.calls) >= 100:
            raise AssertionError("finite fixture call limit")
        raw = self.chunks.popleft() if self.chunks else b""
        if len(raw) > count:
            self.chunks.appendleft(raw[count:])
            raw = raw[:count]
        self.calls.append(("recv", count, raw, flags))
        return raw


class NativeSocket:
    mode = "complete"

    def __init__(self, *args):
        self.buffer, self.sent, self._closed = bytearray(), [], False
        for key in ("send_pkg_num", "recv_pkg_num", "send_pkg_bytes", "recv_pkg_bytes", "last_api_send_bytes", "last_api_recv_bytes"):
            setattr(self, key, 0)
        self.first_pkg_send_time = None

    def connect(self, address):
        sys.audit("socket.connect", self, address)

    def settimeout(self, value):
        self.timeout = value

    def send(self, raw):
        self.sent.append(bytes(raw))
        if len(self.sent) == 4 and self.mode == "timeout":
            time.sleep(30)
        if len(self.sent) == 4 and self.mode == "flood":
            sys.stderr.buffer.write(b"X" * (2 * 1048576))
            sys.stderr.buffer.flush()
        value = b"\x01" if len(self.sent) <= 3 else (b"\x20\x03" if self.mode == "short" else body())
        if len(self.sent) == 4 and self.mode == "large":
            value += b"\x00" * (65535-len(value))
        self.buffer = bytearray(struct.pack("<IIIHH", 0, 0, 0, len(value), len(value)) + value)
        return len(raw)

    def recv(self, count):
        raw = bytes(self.buffer[:count])
        del self.buffer[:count]
        return raw

    def shutdown(self, how):
        pass

    def close(self):
        self._closed = True


def child():
    from overnight_quant.data import minute_protocol_worker as worker
    NativeSocket.mode = sys.argv[1]
    def configure(task, guard):
        def external_guard(event, args):
            if event == "socket.connect" and not isinstance(args[0], NativeSocket):
                raise AssertionError("fixture_external_network_forbidden")
        sys.addaudithook(external_guard)
        if "launch" in task:
            # Exercise the same launch bindings and deep preflight validation
            # with an explicit synthetic clock/scope, never a production bypass.
            from datetime import timedelta
            import json
            from overnight_quant.data import minute_protocol_execution as execution
            from overnight_quant.data.minute_capture_preflight import stamp
            root = Path(task["launch"]["path"]).parents[5]
            auth = execution._launch_materials(task, root)
            actual = stamp(task["scheduled_at"]) + timedelta(seconds=time.perf_counter() - task["target_monotonic"])
            execution.authorize(auth, trade_date=task["trade_date"], target_at=task["scheduled_at"],
                code_sha=task["code_sha"], root=root, current=actual, test_only=True, during_preparation=True, check_git=False)
            print(json.dumps({"kind": "fixture_launch_validated", "test_only": True,
                "worker_cwd": os.getcwd(), "execution_worker_version": worker.contract.WORKER_VERSION}), flush=True)
        import mootdx.config as config
        import tdxpy.base_socket_client as sockets
        config.CONF = task["config"]["path"]
        def forbidden(*a, **k):
            raise AssertionError("fixture_discovery_forbidden")
        config.bestip = forbidden
        sockets.TrafficStatSocket = NativeSocket
    return worker.main(_test={"configure": configure})


if __name__ == "__main__":
    raise SystemExit(child())
