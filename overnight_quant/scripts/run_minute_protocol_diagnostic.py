"""Freeze/review/replay a protocol diagnostic draft. No live execution mode."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from overnight_quant.data import minute_capture_audit as store
from overnight_quant.data import minute_protocol_contract as contract
from overnight_quant.data import minute_protocol_audit as audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--plan-output", type=Path)
    parser.add_argument("--authorization-draft-output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--expected-file-sha256")
    parser.add_argument("--replay-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.verify:
            result = audit.verify(store.read_anchored(args.verify, args.expected_file_sha256))
            if args.replay_output:
                store.write(args.replay_output, result)
            print(json.dumps(result, sort_keys=True))
            return 0 if (result["evidence_integrity_verified"]
                         and result["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED") else 2
        plan = contract.frozen_plan()
        if args.plan_output:
            store.write(args.plan_output, plan)
        if args.authorization_draft_output:
            store.write(args.authorization_draft_output, audit.authorization_draft())
        print(json.dumps(store.safe(status="PROTOCOL_DIAGNOSTIC_DRAFT_VALIDATED_NOT_AUTHORIZED",
            plan=plan, source_requests=0, worker_starts=0, real_execution_entry="NOT_ENABLED_PENDING_SEPARATE_PM_REVIEW"), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps(store.safe(status="PROTOCOL_DIAGNOSTIC_REJECTED", error=f"{type(exc).__name__}:{exc}"), sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
