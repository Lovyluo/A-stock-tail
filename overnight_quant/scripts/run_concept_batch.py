"""Explicit offline-first entry; live execution requires a separate authorization."""
import time

ENTRY_STARTED = time.perf_counter()

import argparse
import json
import os
import sys
from pathlib import Path

from overnight_quant.data.concept_batch_observation import (
    AUTH_VERSION, BUDGET, CONTRACT, ENDPOINT, LAUNCH_VERSION, PLAN, STATE, VERSION, load_batch, safe, write,
)
from overnight_quant.data.probe_worker_process import run_probe_worker_process


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--day")
    parser.add_argument("--approved-sha")
    parser.add_argument("--output")
    parser.add_argument("--batch-id")
    parser.add_argument("--fixture")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--authorization")
    parser.add_argument("--authorization-sha")
    parser.add_argument("--test-state")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--verify-dir")
    parser.add_argument("--returned-report")
    parser.add_argument("--report-sha")
    parser.add_argument("--confirmation-sha")
    parser.add_argument("--launcher-ticket", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.describe or args.verify_dir:
        if args.network or args.fixture or (args.describe and args.verify_dir):
            parser.error("offline inspection cannot execute capture")
        if args.describe:
            print(json.dumps(safe(status="DRAFT_NOT_AUTHORIZED", version=VERSION, contract_hash=CONTRACT,
                                  authorization_version=AUTH_VERSION,
                                  endpoint=ENDPOINT, plan=PLAN, budget=BUDGET), ensure_ascii=True, sort_keys=True))
            return 0
        try:
            result = load_batch(args.verify_dir, args.returned_report, args.report_sha, args.confirmation_sha)
            print(json.dumps(safe(status="VERIFIED_OFFLINE", observation=result), ensure_ascii=True, sort_keys=True))
            return 0
        except Exception as exc:
            print(json.dumps(safe(status="FAILED", error=f"{type(exc).__name__}:{exc}"), ensure_ascii=True))
            return 2
    if args.test_state and not args.fixture:
        parser.error("--test-state requires --fixture")
    params = {"day": args.day, "approved_sha": args.approved_sha, "output": args.output,
              "batch_id": args.batch_id, "fixture": args.fixture, "network": args.network,
              "authorization": args.authorization, "authorization_sha": args.authorization_sha}
    if args.test_state:
        params["state"] = args.test_state
    if not args.fixture and not args.network:
        result = safe(status="NETWORK_NOT_REQUESTED", requests_dispatched=0)
    elif args.fixture and args.network:
        result = safe(status="FAILED", error="choose_fixture_or_network", requests_dispatched=0)
    elif not args.launcher_ticket:
        result = safe(status="FAILED", error="versioned_launcher_required", requests_dispatched=0)
    else:
        try:
            from overnight_quant.data.minute_local_prepare_worker import parent_chain
            ticket = json.loads(Path(args.launcher_ticket).read_text(encoding="utf-8"))
            if (ticket.get("schema_version") != LAUNCH_VERSION or ticket.get("contract_hash") != CONTRACT
                    or ticket.get("nonce") != os.environ.get("CONCEPT_BATCH_LAUNCH_TOKEN")
                    or not ticket.get("nonce") or ticket.get("launcher_pid") not in parent_chain()[1:]
                    or ticket.get("batch_id") != args.batch_id or ticket.get("day") != args.day
                    or ticket.get("approved_sha") != args.approved_sha
                    or ticket.get("output") != str(Path(args.output).resolve())
                    or ticket.get("synthetic_test_only") is not bool(args.fixture)):
                raise ValueError("launcher_ticket_invalid")
        except Exception as exc:
            print(json.dumps(safe(status="FAILED", error=f"{type(exc).__name__}:{exc}", requests_dispatched=0), ensure_ascii=True))
            return 2
        started = ENTRY_STARTED
        outer = run_probe_worker_process({"mode": "capture", "contract_hash": CONTRACT, "capture_args": params},
            90000, bounded_cleanup_ms=2000, hard_deadline_monotonic=started + 90, retain_streams=True,
            worker_command=[sys.executable, "-m", "overnight_quant.scripts.run_concept_batch_worker"])
        elapsed = (time.perf_counter() - started) * 1000
        if outer.get("ok") and outer.get("cleanup_complete") and elapsed < 90000:
            result = {**outer["payload"], "supervisor_started_monotonic": started,
                      "supervisor_elapsed_ms": elapsed, "supervisor_cleanup_complete": True}
            if result.get("status") == "PASSED":
                result.update(status="EXECUTION_CANDIDATE", candidate_status="PASSED")
        else:
            result = safe(status="FAILED", error="batch_supervisor_failed_or_deadline", requests_dispatched=None,
                          request_count_status="UNKNOWN", supervisor_elapsed_ms=elapsed,
                          supervisor_cleanup_complete=outer.get("cleanup_complete"), process=outer)
            if not outer.get("cleanup_complete"):
                state = Path(args.test_state).resolve() if args.test_state else STATE.resolve()
                try:
                    write(state / "cleanup-incomplete.json", safe(status="SUPERVISOR_CLEANUP_UNCONFIRMED"))
                except Exception:
                    pass
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result.get("status") == "NETWORK_NOT_REQUESTED" else 3 if result.get("status") == "EXECUTION_CANDIDATE" else 2


if __name__ == "__main__":
    sys.exit(main())
