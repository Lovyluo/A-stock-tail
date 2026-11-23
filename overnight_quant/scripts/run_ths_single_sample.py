"""Default zero-worker/zero-network description; explicit one-response capture."""
import time

ENTRY_STARTED = time.perf_counter()

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from overnight_quant.data.ths_single_sample import STATE, capture, request_sample, safe, verify, write_once
from overnight_quant.data.probe_worker_process import run_probe_worker_process


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--target-date")
    parser.add_argument("--approved-sha")
    parser.add_argument("--output")
    parser.add_argument("--batch-id")
    parser.add_argument("--approval")
    parser.add_argument("--approval-sha")
    parser.add_argument("--fixture")
    parser.add_argument("--test-state")
    parser.add_argument("--verify-dir")
    parser.add_argument("--completion-sha")
    parser.add_argument("--returned-result")
    parser.add_argument("--returned-sha")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--controller", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--launcher-start", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker or args.controller:
        from overnight_quant.data.probe_worker_process import MAX_BOUNDED_WORKER_INPUT_BYTES
        try:
            raw = sys.stdin.buffer.read(MAX_BOUNDED_WORKER_INPUT_BYTES + 1)
            if len(raw) > MAX_BOUNDED_WORKER_INPUT_BYTES:
                raise ValueError("worker_input_byte_limit")
            task = json.loads(raw.decode("utf-8", "strict"))
            if args.controller:
                from overnight_quant.data.minute_local_prepare_worker import parent_chain
                nonce = task.pop("supervisor_nonce", None)
                supervisor = task.pop("supervisor_pid", None)
                if (not isinstance(nonce, str) or len(nonce) != 32
                        or nonce != os.environ.get("THS_SAMPLE_SUPERVISOR_NONCE")
                        or supervisor not in parent_chain()[1:3]):
                    raise ValueError("supervising_entry_required")
                result = capture(**task)
                print(json.dumps({"ok": True, "payload": result}, ensure_ascii=True))
                return 0
            if task.get("fixture"):
                import socket
                def denied(*args, **kwargs):
                    raise RuntimeError("fixture_network_forbidden")
                socket.socket = socket.getaddrinfo = socket.create_connection = denied
            result = request_sample(task)
            print(json.dumps({"ok": True, "payload": {"status": result["status"]}}, ensure_ascii=True))
            return 0
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}:{exc}"}, ensure_ascii=True))
            return 2
    try:
        if args.verify_dir:
            if args.execute or args.fixture:
                raise ValueError("verify_cannot_execute")
            result = verify(args.verify_dir, args.completion_sha, args.returned_result, args.returned_sha)
            code = 0 if result["capture_execution_result"] == "PASSED" else 2
        else:
            if args.test_state and (not args.fixture or args.execute):
                raise ValueError("state_override_test_only")
            if (args.execute or args.fixture) and (args.launcher_start is None
                    or not 0 <= ENTRY_STARTED - args.launcher_start < 1):
                raise ValueError("versioned_ths_launcher_required")
            started = args.launcher_start if args.launcher_start is not None else ENTRY_STARTED
            extra = {"state": args.test_state} if args.test_state else {}
            params = dict(target=args.target_date, head=args.approved_sha, output=args.output,
                batch=args.batch_id, fixture=args.fixture, execute=args.execute,
                approval=args.approval, approval_sha=args.approval_sha, started=started, **extra)
            if not args.execute and not args.fixture:
                result = capture(**params)
            else:
                # Existing supervisor bounds controller writes and its entire worker tree.
                nonce = uuid4().hex
                old_nonce = os.environ.get("THS_SAMPLE_SUPERVISOR_NONCE")
                os.environ["THS_SAMPLE_SUPERVISOR_NONCE"] = nonce
                try:
                    outer = run_probe_worker_process({**params, "supervisor_nonce": nonce, "supervisor_pid": os.getpid()},
                        10000, bounded_cleanup_ms=1000, hard_deadline_monotonic=started + 9,
                        retain_streams=True, strict_utf8=True,
                        worker_command=[sys.executable, "-m", "overnight_quant.scripts.run_ths_single_sample", "--controller"])
                finally:
                    if old_nonce is None:
                        os.environ.pop("THS_SAMPLE_SUPERVISOR_NONCE", None)
                    else:
                        os.environ["THS_SAMPLE_SUPERVISOR_NONCE"] = old_nonce
                result = outer["payload"] if outer.get("ok") else safe(status="FAILED",
                    error="controller_deadline_or_failure", actual_http_requests=0 if args.fixture else None)
                result["supervisor_cleanup_complete"] = outer.get("cleanup_complete") is True
                if not result["supervisor_cleanup_complete"]:
                    state = Path(args.test_state) if args.fixture and args.test_state else STATE
                    try:
                        write_once(state / "cleanup-incomplete.json", {"reason": "supervisor_cleanup_unknown"})
                    except OSError:
                        pass
                result["final_check_elapsed_ms"] = (time.perf_counter() - started) * 1000
                if not outer.get("ok") or not result["supervisor_cleanup_complete"] or result["final_check_elapsed_ms"] >= 10000:
                    result["status"] = "FAILED"
            code = 0 if result["status"] in ("NETWORK_NOT_REQUESTED", "SAMPLE_PARSED") else 2
    except Exception as exc:
        result = safe(status="FAILED", error=f"{type(exc).__name__}:{exc}", actual_http_requests=0, requests_attempted=0)
        code = 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
