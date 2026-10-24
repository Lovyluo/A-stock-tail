"""Independent opt-in single-bars protocol worker; no four-wave capture route."""
import os
import sys
import time

from . import minute_capture_worker as native
from . import minute_local_prepare_worker as prepared
from . import minute_protocol_contract as contract
from .minute_protocol_trace import BoundedEmitter, ProtocolTrace


def main(*, _test=None):
    guard = prepared.NetworkBoundary()
    sys.addaudithook(guard.audit)
    task, trace = None, None
    deadline = [time.perf_counter() + 3.5]
    def write(raw):
        count = sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        return count

    writer = BoundedEmitter(write, deadline=lambda: deadline[0])

    def emit(value, *, failure=False):
        writer({"execution_worker_version": contract.WORKER_VERSION, **value}, failure=failure)

    try:
        stream = prepared.ProtocolInput()
        task = stream.line(deadline[0])
        contract.check_task(task)
        ancestry = prepared.parent_chain()
        if (task["parent_pid"] not in ancestry[1:] or task.get("launch_root_pid") not in ancestry
                or ancestry.index(task["launch_root_pid"]) >= ancestry.index(task["parent_pid"])):
            raise ValueError("protocol_diagnostic_parent_invalid")
        if task.get("test_only", False) is not bool(_test):
            raise ValueError("protocol_diagnostic_test_scope_mismatch")
        if contract.worker_file_sha256() != task["worker_sha256"]:
            raise ValueError("protocol_diagnostic_worker_sha_mismatch")
        permitted = None
        if not _test:
            from .minute_protocol_execution import verify_worker_launch
            permitted = verify_worker_launch(task)
        deadline[0] = task["preparation_work_deadline"]
        if _test:
            _test["configure"](task, guard)
        versions = prepared.prepare_local(task, guard, emit_fn=emit, codes=contract.CODES)
        guard.assert_clean()
        emit({"kind": "READY_LOCAL_ONLY", "worker_version": contract.WORKER_VERSION,
            "task_hash": native.digest(task), "pid": os.getpid(),
            "dependencies": versions, "config": task["config"], "code_sha": task["code_sha"],
            "batch_id": task["batch_id"], "wave": 0, "plan_hash": task["plan_hash"],
            "ready_monotonic": time.perf_counter(), "network_attempts_before_go": len(guard.attempts),
            "parent_chain": ancestry})
        message = stream.line(task["io_deadline_monotonic"])
        received = time.perf_counter()
        contract.check_go(message, task, received)
        stream.require_eof(task["io_deadline_monotonic"])
        guard.assert_clean()
        if permitted is not None:
            from .minute_capture_preflight import fresh_at, now
            fresh_at(permitted, now())
        deadline[0] = task["io_deadline_monotonic"]
        guard.released = True
        emit({"kind": "GO_ACCEPTED", "task_hash": native.digest(task), "monotonic": time.perf_counter(),
              "sent_monotonic": message["sent_monotonic"], "received_monotonic": received,
              "network_attempts_before_go": len(guard.attempts)})
        import tdxpy.base_socket_client as sockets
        from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
        trace = ProtocolTrace(deadline=deadline[0], emit=emit)
        with trace.hooks(sockets.TrafficStatSocket, GetSecurityBarsCmd), prepared.connection_stages(emit_fn=emit):
            client = None
            try:
                client = native.NativeClient(emit=emit)
                frame = client.bars(**contract.frozen_plan()["request"])
                if frame is None or frame.empty or len(frame) > 800:
                    raise ValueError("protocol_diagnostic_empty_or_oversized_rows")
                row_count = len(frame)
            finally:
                if client is not None:
                    client.close()
        guard.assert_clean()
        if len(trace.interactions) != 4 or len(trace.interactions[-1]["parser_inputs"]) != 1:
            raise ValueError("protocol_diagnostic_interaction_scope_incomplete")
        emit({"ok": True, "payload": {"test_only": bool(_test), "row_count": row_count,
            "trace_summary": trace.summary(), "output_bytes_before_terminal": writer.bytes_written,
            "boundary_attempts": guard.attempts, "allowed_connections": guard.allowed_connections,
            "qualification": "0/3", "data_ready": False, "hard_gate_authorized": False,
            "records": [], "research_ranking": [], "candidates": [], "tickets": [], "orders": []}})
        return 0
    except Exception as exc:
        try:
            emit({"ok": False, "error_code": "PROTOCOL_DIAGNOSTIC_WORKER_FAILED",
                "error": f"{type(exc).__name__}:{exc}", "exception_chain": native.exception_chain(exc),
                "trace_summary": trace.summary() if trace else None,
                "output_incomplete": writer.failed, "output_bytes_before_terminal": writer.bytes_written,
                "boundary_attempts": guard.attempts, "test_only": bool(_test)}, failure=True)
        except Exception:
            # A broken/limited output channel cannot become a successful receipt.
            return 3
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
