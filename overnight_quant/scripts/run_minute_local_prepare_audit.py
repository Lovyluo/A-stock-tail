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
from overnight_quant.data import minute_local_prepare_seal as sealing


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--network", action="store_true")
    modes.add_argument("--verify", type=Path)
    for name in ("plan", "plan-output", "authorization", "replay-output", "seal-receipt", "seal-completion"):
        parser.add_argument("--" + name, type=Path)
    for name in ("date", "expected-plan-sha256", "authorization-sha256", "expected-file-sha256",
                 "expected-receipt-sha256", "expected-completion-sha256"):
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
            result = audit.verify_files(args.verify, args.expected_file_sha256,
                receipt_path=args.seal_receipt, expected_receipt_sha256=args.expected_receipt_sha256,
                completion_path=args.seal_completion, expected_completion_sha256=args.expected_completion_sha256)
            if args.replay_output:
                legacy.write(args.replay_output, result)
        elif args.network:
            if not args.plan or not args.authorization or not args.date:
                raise ValueError("new_plan_and_date_specific_v3_authorization_required")
            auth = legacy.read_anchored(args.authorization, args.authorization_sha256)
            try:
                evidence, path = audit.capture(plan, args.date, authorization=auth)
            except sealing.SealWriteError as exc:
                # A file may exist despite an exception. Never upgrade an
                # unconfirmed write by discovering and auto-anchoring it.
                if exc.evidence_path.exists():
                    result = audit.verify_files(exc.evidence_path, sha256(exc.evidence_path.read_bytes()).hexdigest())
                else:
                    result = legacy.safe(status="PREPARED_MINUTE_REQUEST_REJECTED", seal_status="SEAL_UNCONFIRMED",
                                         final_execution_result=sealing.FAILED)
                result.update(error=str(exc), evidence_path=str(exc.evidence_path))
                print(json.dumps(result, sort_keys=True))
                return 2
            references = {name: sha256((path.parent / filename).read_bytes()).hexdigest()
                for name, filename in (("receipt", sealing.RECEIPT_NAME), ("completion", sealing.COMPLETION_NAME))}
            result = audit.verify_files(path, sha256(path.read_bytes()).hexdigest(),
                receipt_path=path.parent / sealing.RECEIPT_NAME, expected_receipt_sha256=references["receipt"],
                completion_path=path.parent / sealing.COMPLETION_NAME, expected_completion_sha256=references["completion"])
            result.update(evidence_path=str(path), seal_receipt_sha256=references["receipt"],
                          seal_completion_sha256=references["completion"])
        else:
            result = legacy.safe(status="PREPARED_MINUTE_PLAN_VALIDATED_NOT_AUTHORIZED", plan=plan,
                                 network_requests_made=0, child_processes_started=0)
        print(json.dumps(result, sort_keys=True))
        return 2 if (result.get("integrity_verified") is False
                     or result.get("final_execution_result") == sealing.FAILED) else 0
    except Exception as exc:
        print(json.dumps(legacy.safe(status="PREPARED_MINUTE_REQUEST_REJECTED", error=f"{type(exc).__name__}:{exc}"), sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
