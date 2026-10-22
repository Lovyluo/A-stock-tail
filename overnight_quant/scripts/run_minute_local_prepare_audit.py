"""Candidate v2 manual audit. Default/validate-only start no worker or source request."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from overnight_quant.data import minute_capture_audit as legacy
from overnight_quant.data import minute_local_prepare_audit as audit
from overnight_quant.data import minute_local_prepare_contract as contract


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--network", action="store_true")
    modes.add_argument("--verify", type=Path)
    for name in ("plan", "plan-output", "authorization", "replay-output"):
        parser.add_argument("--" + name, type=Path)
    for name in ("date", "expected-plan-sha256", "authorization-sha256", "expected-file-sha256"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    try:
        plan = contract.frozen_plan()
        if args.plan:
            plan = legacy.read_anchored(args.plan, args.expected_plan_sha256)
            contract.check_plan(plan)
        if args.plan_output:
            if args.network or args.verify:
                raise ValueError("freeze_prepared_plan_separately")
            legacy.write(args.plan_output, plan)
        if args.verify:
            raw = args.verify.read_bytes()
            result = audit.verify(json.loads(raw), args.expected_file_sha256, sha256(raw).hexdigest())
            if args.replay_output:
                legacy.write(args.replay_output, result)
        elif args.network:
            if not args.plan or not args.authorization or not args.date:
                raise ValueError("new_plan_and_date_specific_v3_authorization_required")
            auth = legacy.read_anchored(args.authorization, args.authorization_sha256)
            evidence, path = audit.capture(plan, args.date, authorization=auth)
            receipt = json.loads((path.parent / "seal-receipt-v2.json").read_bytes())
            result = legacy.safe(status=receipt["status"], evidence_path=str(path),
                                 evidence_hash=evidence["evidence_hash"], summary=evidence["summary"])
        else:
            result = legacy.safe(status="PREPARED_MINUTE_PLAN_VALIDATED_NOT_AUTHORIZED", plan=plan,
                                 network_requests_made=0, child_processes_started=0)
        print(json.dumps(result, sort_keys=True))
        return 2 if result.get("integrity_verified") is False or result["status"] == "MINUTE_CAPTURE_AUDIT_FAILED" else 0
    except Exception as exc:
        print(json.dumps(legacy.safe(status="PREPARED_MINUTE_REQUEST_REJECTED", error=f"{type(exc).__name__}:{exc}"), sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
