"""Single-node raw audit capture. No registry, strategy or finality conversion."""
from __future__ import annotations

import base64
from datetime import date, datetime, timezone, timedelta
from hashlib import sha256
import json
import math
import sys
import time

CODES = ("000001", "000333", "600000", "600519", "601318")
ENDPOINT = {"host": "59.36.5.11", "port": 7709,
            "id": "mootdx_locked@59.36.5.11:7709"}
SOURCE_VERSION = "mootdx_0.11.7_tdx_std_bars_1m_v2026-07-31"
VERSION = "mootdx_native_minute_capture_worker_v1"
CN = timezone(timedelta(hours=8))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def now():
    return datetime.now(CN).isoformat(timespec="microseconds")


def json_value(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"nonfinite_float": repr(value)}
    if isinstance(value, (datetime, date)):
        return {"native_python_type": type(value).__name__, "isoformat": value.isoformat()}
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if callable(getattr(value, "item", None)):
        return json_value(value.item())
    return {"native_python_type": f"{type(value).__module__}.{type(value).__name__}",
            "representation": repr(value)}


class NativeClient:
    def __init__(self):
        from importlib.metadata import version
        if version("mootdx") != "0.11.7" or version("tdxpy") != "0.2.7":
            raise ValueError("native_client_version_mismatch")
        from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
        from mootdx.quotes import Quotes
        self.bodies = []
        self.body_times = []
        self.parser = GetSecurityBarsCmd
        self.original = GetSecurityBarsCmd.parseResponse
        owner, original = self, self.original

        def capture(parser, body):
            owner.bodies.append(bytes(body))
            owner.body_times.append({"parser_received_at": now(), "parser_received_monotonic": time.perf_counter()})
            return original(parser, body)

        self.parser.parseResponse = capture
        try:
            self.client = Quotes.factory(market="std", server=(ENDPOINT["host"], ENDPOINT["port"]),
                                         bestip=False, heartbeat=False, auto_retry=False,
                                         raise_exception=True, timeout=2.0)
        except BaseException:
            self.parser.parseResponse = self.original
            raise

    def bars(self, **request):
        self.bodies.clear()
        self.body_times.clear()
        return self.client.bars(**request)

    def close(self):
        try:
            self.client.close()
        finally:
            self.parser.parseResponse = self.original


def capture_wave(task, *, client_factory=NativeClient, emit=None):
    emit = emit or (lambda value: print(encoded(value), flush=True))
    if (task.get("worker_version") != VERSION or task.get("codes") != list(CODES)
            or task.get("endpoint") != ENDPOINT or task.get("source_version") != SOURCE_VERSION):
        raise ValueError("fixed_capture_identity_mismatch")
    date.fromisoformat(task["trade_date"])
    deadline = task["io_deadline_monotonic"]
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("capture_deadline_invalid")
    client, called = None, 0
    emit({"kind": "wave_started", "at": now(), "worker_version": VERSION,
          "budget_clock": "time.perf_counter"})
    try:
        if time.perf_counter() >= deadline:
            raise TimeoutError("wave_budget_exhausted_before_client")
        client = client_factory()
        emit({"kind": "client_ready", "at": now(), "monotonic": time.perf_counter()})
        for code in CODES:
            if time.perf_counter() >= deadline:
                raise TimeoutError("wave_budget_exhausted_before_dispatch")
            request = {"symbol": code, "frequency": "1m", "start": 0, "offset": 800}
            receipt = {"kind": "request_started", "request_id": f"{task['wave']}:{code}",
                       "wave": task["wave"], "code": code, "request": request,
                       "request_hash": digest({"request": request, "endpoint": ENDPOINT}),
                       "request_started_at": now(), "call_monotonic": time.perf_counter(),
                       "remaining_io_ms": max(0, (deadline - time.perf_counter()) * 1000)}
            emit(receipt)
            called += 1
            start = time.perf_counter()
            try:
                frame = client.bars(**request)
                completed, read_end = now(), time.perf_counter()
                bodies = list(client.bodies)
                if not bodies:
                    raise ValueError("native_parser_response_body_missing")
                rows = json_value(frame.to_dict(orient="records"))
                if not rows or len(rows) > 800:
                    raise ValueError("native_response_empty_or_exceeds_one_page")
                response = {**receipt, "kind": "response", "response_read_completed_at": completed,
                            "response_read_monotonic": read_end,
                            "elapsed_ms": (read_end - start) * 1000, "native_rows": rows,
                            "native_rows_hash": digest(rows), "native_row_count": len(rows),
                            "wire_bodies": [{"base64": base64.b64encode(b).decode("ascii"),
                                             "sha256": sha256(b).hexdigest(), "size": len(b),
                                             **getattr(client, "body_times", [{}] * len(bodies))[i]}
                                            for i, b in enumerate(bodies)],
                            "raw_boundary": "tdxpy_parser_input_decompressed_response_body",
                            "source_version": SOURCE_VERSION,
                            "native_timestamp_precision": "minute",
                            "client_representation": "mootdx_dataframe_all_columns_v1"}
                emit(response)
                if read_end > deadline:
                    raise TimeoutError("wave_io_deadline_exceeded")
            except Exception as exc:
                emit({**receipt, "kind": "request_failed", "completed_at": now(),
                      "error_type": type(exc).__name__, "error": str(exc),
                      "wire_bodies": [{"base64": base64.b64encode(b).decode("ascii"),
                                       "sha256": sha256(b).hexdigest(), "size": len(b)}
                                      for b in client.bodies]})
                raise
        return {"network_requests_made": called, "completed_at": now(), "worker_version": VERSION}
    finally:
        if client is not None:
            client.close()


def main():
    try:
        task = json.loads(sys.stdin.read())
        payload = capture_wave(task)
        print(encoded({"ok": True, "payload": payload}), flush=True)
        return 0
    except Exception as exc:
        print(encoded({"ok": False, "error_code": "MINUTE_CAPTURE_WORKER_FAILED",
                       "error": f"{type(exc).__name__}:{exc}"}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
