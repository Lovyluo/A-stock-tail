"""Opt-in passive trace of the pinned tdxpy Python socket/parser boundary."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from hashlib import sha256
import json
import struct
import time

MAX_INTERACTIONS = 4
MAX_RECV_CALLS = 32
MAX_BODY_BYTES = 65535
MAX_SEND_BYTES = 4096
MAX_OUTPUT_BYTES = 1048576
FAILURE_OUTPUT_RESERVE = 32768


class TraceLimitError(RuntimeError):
    pass


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


class BoundedEmitter:
    def __init__(self, write, *, deadline, monotonic=time.perf_counter):
        self.write, self.deadline, self.monotonic = write, deadline, monotonic
        self.bytes_written = 0
        self.bytes_accounted = 0
        self.failed = False

    def __call__(self, value, *, failure=False):
        raw = json_bytes(value)
        limit = MAX_OUTPUT_BYTES if failure else MAX_OUTPUT_BYTES - FAILURE_OUTPUT_RESERVE
        if self.bytes_accounted + len(raw) > limit:
            self.failed = True
            raise TraceLimitError("protocol_total_output_limit")
        if not failure and self.monotonic() >= self.deadline():
            self.failed = True
            raise TimeoutError("protocol_output_work_deadline")
        try:
            self.bytes_accounted += len(raw)
            written = self.write(raw)
            if written != len(raw):
                raise OSError("protocol_output_short_write")
            self.bytes_written += written
        except BaseException:
            self.failed = True
            raise
        if not failure and self.monotonic() >= self.deadline():
            self.failed = True
            raise TimeoutError("protocol_output_completed_after_work_deadline")


class ProtocolTrace:
    def __init__(self, *, deadline, emit=None, monotonic=time.perf_counter):
        self.deadline, self.monotonic = deadline, monotonic
        self.emit = emit
        self.interactions = []
        self.failure = None
        self.socket = None
        self.retained_bytes = 0
        self.hooks_restored = False

    def _reject(self, reason):
        self.failure = reason
        raise TraceLimitError(reason)

    def _time(self):
        if self.monotonic() >= self.deadline:
            self.failure = "protocol_trace_work_deadline"
            raise TimeoutError(self.failure)
        return self.monotonic()

    def _blob(self, value, cap):
        raw = bytes(memoryview(value)[:cap])
        self.retained_bytes += len(raw)
        return {"returned_length": len(value), "captured_length": len(raw),
                "capture_complete": len(raw) == len(value),
                "captured_sha256": sha256(raw).hexdigest(),
                "base64": base64.b64encode(raw).decode("ascii")}

    def _publish(self, operation, group, record):
        if self.emit:
            try:
                self.emit({"kind": "protocol_io", "interaction": group["index"],
                           "operation": operation, "record": record})
            except BaseException:
                self.failure = "protocol_trace_output_failed"
                raise
        self._time()

    def _group(self, sock):
        if self.socket is not sock or not self.interactions:
            self._reject("protocol_unattributed_socket_or_recv")
        return self.interactions[-1]

    def send(self, original, sock, *args, **kwargs):
        self._time()
        if self.socket is None:
            self.socket = sock
        if self.socket is not sock:
            self._reject("protocol_second_socket_forbidden")
        if len(self.interactions) >= MAX_INTERACTIONS:
            self._reject("protocol_interaction_limit")
        raw = args[0] if args else kwargs["data"]
        record = {"started_monotonic": self._time(), "requested_length": len(raw),
                  "positional_flags": list(args[1:]), "keyword_flags": {k: v for k, v in kwargs.items() if k != "data"},
                  "data": self._blob(raw, MAX_SEND_BYTES)}
        group = {"index": len(self.interactions), "send": record, "recv": [], "parser_inputs": []}
        self.interactions.append(group)
        if not record["data"]["capture_complete"]:
            self._publish("send_not_dispatched", group, record)
            self._reject("protocol_send_material_limit")
        self._publish("send_started", group, dict(record))
        try:
            result = original(sock, *args, **kwargs)
        except BaseException as exc:
            record.update(completed_monotonic=self.monotonic(), exception_type=type(exc).__name__,
                          exception=str(exc)[:512], returned_length=None)
            self._publish("send_result", group, {k: v for k, v in record.items() if k != "data"})
            raise
        record.update(completed_monotonic=self.monotonic(), returned_length=result,
                      exception_type=None, exception=None)
        self._publish("send_result", group, {k: v for k, v in record.items() if k != "data"})
        return result

    def recv(self, original, sock, *args, **kwargs):
        group = self._group(sock)
        self._time()
        if len(group["recv"]) >= MAX_RECV_CALLS:
            self._reject("protocol_recv_metadata_limit")
        requested = args[0] if args else kwargs["bufsize"]
        record = {"started_monotonic": self._time(), "requested_length": requested,
                  "call_index": len(group["recv"]),
                  "positional_flags": list(args[1:]), "keyword_flags": {k: v for k, v in kwargs.items() if k != "bufsize"}}
        group["recv"].append(record)
        self._publish("recv_started", group, dict(record))
        try:
            result = original(sock, *args, **kwargs)
        except BaseException as exc:
            record.update(completed_monotonic=self.monotonic(), returned_length=None, eof=None,
                          exception_type=type(exc).__name__, exception=str(exc)[:512], data=None)
            self._publish("recv_result", group, record)
            raise
        record.update(completed_monotonic=self.monotonic(), returned_length=len(result), eof=len(result) == 0,
                      exception_type=None, exception=None)
        retained = sum(x.get("data", {}).get("captured_length", 0) for x in group["recv"] if x.get("data"))
        record["data"] = self._blob(result, max(0, MAX_BODY_BYTES + 16 - retained))
        self._publish("recv_result", group, record)
        if not record["data"]["capture_complete"]:
            self._reject("protocol_received_material_limit")
        return result

    def parser_input(self, original, parser, value):
        group = self._group(parser.client)
        self._time()
        if group["parser_inputs"]:
            self._reject("protocol_duplicate_parser_input")
        record = {"at_monotonic": self.monotonic(), "data": self._blob(value, MAX_BODY_BYTES),
                  "boundary": "tdxpy_GetSecurityBarsCmd_parseResponse_input"}
        group["parser_inputs"].append(record)
        self._publish("parser_input", group, record)
        if not record["data"]["capture_complete"]:
            self._reject("protocol_parser_material_limit")
        return original(parser, value)

    @contextmanager
    def hooks(self, socket_class, parser_class):
        originals = {name: (name in socket_class.__dict__, getattr(socket_class, name)) for name in ("send", "recv")}
        parser_original = parser_class.parseResponse
        socket_class.send = lambda sock, *a, **k: self.send(originals["send"][1], sock, *a, **k)
        socket_class.recv = lambda sock, *a, **k: self.recv(originals["recv"][1], sock, *a, **k)
        parser_class.parseResponse = lambda parser, value: self.parser_input(parser_original, parser, value)
        try:
            yield self
        finally:
            parser_class.parseResponse = parser_original
            for name, (owned, method) in originals.items():
                if owned:
                    setattr(socket_class, name, method)
                else:
                    delattr(socket_class, name)
            self.hooks_restored = True

    def summary(self):
        groups = []
        for group in self.interactions:
            reads = group["recv"]
            first = reads[0] if reads else {}
            header_blob = first.get("data") or {}
            header = base64.b64decode(header_blob.get("base64", ""))
            known = (first.get("requested_length") == 16 and first.get("returned_length") == 16
                     and header_blob.get("capture_complete") is True)
            raw_body = b"".join(base64.b64decode((r.get("data") or {}).get("base64", "")) for r in reads[1:]) if known else None
            declared_zip = declared_unzip = None
            if known:
                _, _, _, declared_zip, declared_unzip = struct.unpack("<IIIHH", header)
            inputs = group["parser_inputs"]
            groups.append({"index": group["index"], "attribution": "local_send_recv_order_only_wire_command_identity_UNKNOWN",
                "header_recv_indices": [0] if reads else [], "header_captured_length": len(header),
                "header_complete": known, "header_sha256": sha256(header).hexdigest() if reads else None,
                "declared_zip_length": declared_zip, "declared_unzip_length": declared_unzip,
                "body_classification": ("compressed_as_declared" if declared_zip != declared_unzip else "uncompressed_as_declared") if known else "UNKNOWN",
                "received_body_length": len(raw_body) if raw_body is not None else None,
                "received_body_sha256": sha256(raw_body).hexdigest() if raw_body is not None else None,
                "body_capture_complete": known and all((r.get("data") or {}).get("capture_complete") is True for r in reads[1:]),
                "body_matches_declared_length": known and len(raw_body) == declared_zip,
                "recv_count": len(reads), "parser_input_length": inputs[0]["data"]["returned_length"] if inputs else None,
                "parser_input_sha256": inputs[0]["data"]["captured_sha256"] if inputs else None})
        return {"schema": "mootdx_protocol_trace_v1", "complete": self.failure is None,
                "failure": self.failure, "hooks_restored": self.hooks_restored,
                "retained_material_bytes": self.retained_bytes, "interactions": groups,
                "limits": {"interactions": MAX_INTERACTIONS, "recv_per_interaction": MAX_RECV_CALLS,
                           "body_bytes": MAX_BODY_BYTES, "send_bytes": MAX_SEND_BYTES, "total_output_bytes": MAX_OUTPUT_BYTES}}
