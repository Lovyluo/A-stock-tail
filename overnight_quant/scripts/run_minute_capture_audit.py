"""Explicit manual audit entry; default and validation never start workers."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from overnight_quant.data import minute_capture_audit as audit


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--network", action="store_true")
    mode.add_argument("--verify", type=Path)
    mode.add_argument("--validate-only", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-output", type=Path)
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--date")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--authorization-sha256")
    parser.add_argument("--expected-file-sha256")
    parser.add_argument("--replay-output", type=Path)
    args = parser.parse_args(argv)
    try:
        plan = audit.frozen_plan()
        if args.plan:
            plan = audit.read_anchored(args.plan, args.expected_plan_sha256)
            audit.check_plan(plan)
        if args.plan_output:
            if args.network or args.verify:
                raise ValueError("freeze_plan_separately_before_execution")
            audit.write(args.plan_output, plan)
        if args.verify:
            raw = args.verify.read_bytes()
            evidence = json.loads(raw.decode("utf-8-sig"))
            result = audit.verify(evidence, args.expected_file_sha256, sha256(raw).hexdigest())
            if args.replay_output:
                audit.write(args.replay_output, result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result["integrity_verified"] else 2
        if not args.network:
            result = audit.safe(status="MINUTE_CAPTURE_PLAN_VALIDATED_NOT_AUTHORIZED",
                                plan=plan, network_requests_made=0, child_processes_started=0)
        else:
            if not args.plan or not args.authorization or not args.date:
                raise ValueError("frozen_plan_and_manual_date_authorization_required")
            auth = audit.read_anchored(args.authorization, args.authorization_sha256)
            evidence, path = audit.capture(plan, args.date, authorization=auth)
            receipt = json.loads((path.parent / "seal-receipt.json").read_text(encoding="utf-8"))
            result = audit.safe(status=receipt["status"], evidence_path=str(path),
                                evidence_hash=evidence["evidence_hash"], summary=evidence["summary"],
                                cleanup_complete=receipt["cleanup_complete"],
                                residual_process_state=receipt["residual_process_state"])
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2 if result["status"] == "MINUTE_CAPTURE_AUDIT_FAILED" else 0
    except Exception as exc:
        print(json.dumps(audit.safe(status="MINUTE_CAPTURE_REQUEST_REJECTED", error=f"{type(exc).__name__}:{exc}"),
                         ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
