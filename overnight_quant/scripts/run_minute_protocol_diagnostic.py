"""Explicitly opt-in single-stock diagnostic. Default starts no process or network."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from overnight_quant.data import minute_capture_audit as store
from overnight_quant.data import minute_protocol_contract as contract
from overnight_quant.data import minute_protocol_audit as audit
from overnight_quant.data import minute_protocol_execution as execution
from overnight_quant.data import minute_local_prepare_seal as sealing
from overnight_quant.data import minute_protocol_differential as differential
from overnight_quant.data import minute_protocol_differential_contract as differential_contract


def _differential(args):
    if args.target_at or args.seal_receipt or args.seal_completion:
        raise ValueError("differential_targets_and_leaf_seals_are_frozen_in_matrix")
    if args.verify:
        result = differential.verify(args.verify, args.expected_file_sha256)
        if args.replay_output:
            store.write(args.replay_output, result)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["integrity_verified"] and result["diagnostic_result"] == "DIFFERENTIAL_ALL_ITEMS_PARSED" else 2
    plan = differential_contract.frozen_plan()
    if args.plan:
        differential_contract.check_plan(store.read_anchored(args.plan, args.expected_plan_sha256))
    if args.network:
        if (args.plan_output or args.authorization_draft_output or not all((args.plan, args.date,
                                                                           args.authorization, args.authorization_sha256))):
            raise ValueError("differential_frozen_matrix_and_separate_single_date_approval_required")
        _, path = differential.capture(plan, args.date,
            authorization={"path": str(args.authorization.resolve()), "sha256": args.authorization_sha256},
            plan_ref={"path": str(args.plan.resolve()), "sha256": args.expected_plan_sha256})
        result = differential.verify(path, sha256(path.read_bytes()).hexdigest())
        result["evidence_path"] = str(path)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["integrity_verified"] and result["diagnostic_result"] == "DIFFERENTIAL_ALL_ITEMS_PARSED" else 2
    if args.plan_output:
        store.write(args.plan_output, plan)
        # Four fixed leaf plans, not an arbitrary endpoint/count interface.
        for key, *_ in differential_contract.EXPERIMENTS:
            profile = differential_contract.ExperimentProfile(key)
            store.write(args.plan_output.with_name("experiment-" + key + "-plan-v1.json"), profile.frozen_plan())
    if args.authorization_draft_output:
        store.write(args.authorization_draft_output, differential.draft(args.approved_code_sha))
        for key, *_ in differential_contract.EXPERIMENTS:
            store.write(args.authorization_draft_output.with_name("experiment-" + key + "-authorization-draft-v1.json"),
                audit.authorization_draft(args.approved_code_sha, profile=differential_contract.ExperimentProfile(key)))
    print(json.dumps(store.safe(status="DIFFERENTIAL_DRAFT_VALIDATED_NOT_AUTHORIZED", plan=plan,
        source_requests=0, worker_starts=0), sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--differential", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--network", action="store_true")
    mode.add_argument("--verify", type=Path)
    for name in ("plan", "plan-output", "authorization-draft-output", "authorization", "replay-output",
                 "seal-receipt", "seal-completion"):
        parser.add_argument("--" + name, type=Path)
    for name in ("date", "target-at", "approved-code-sha", "expected-plan-sha256", "authorization-sha256",
                 "expected-file-sha256", "expected-receipt-sha256", "expected-completion-sha256"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    try:
        if args.differential:
            return _differential(args)
        if args.verify:
            result = execution.verify_files(args.verify, args.expected_file_sha256,
                receipt_path=args.seal_receipt, expected_receipt_sha256=args.expected_receipt_sha256,
                completion_path=args.seal_completion, expected_completion_sha256=args.expected_completion_sha256)
            if args.replay_output:
                store.write(args.replay_output, result)
            print(json.dumps(result, sort_keys=True))
            return 0 if (result["evidence_integrity_verified"]
                         and result["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED") else 2
        plan = contract.frozen_plan()
        if args.plan:
            plan = store.read_anchored(args.plan, args.expected_plan_sha256)
            contract.check_plan(plan)
        if args.network:
            if args.plan_output or args.authorization_draft_output or not all((args.plan, args.date, args.target_at,
                                                                              args.authorization, args.authorization_sha256)):
                raise ValueError("frozen_plan_date_target_and_external_PM_authorization_required")
            reference = {"path": str(args.authorization.resolve()), "sha256": args.authorization_sha256}
            if execution.preflight.read(reference).get("plan_file_sha256") != args.expected_plan_sha256:
                raise ValueError("protocol_cli_plan_file_anchor_not_authorized")
            try:
                _, path = execution.capture(plan, args.date, args.target_at, authorization=reference)
            except sealing.SealWriteError as exc:
                result = (execution.verify_files(exc.evidence_path, sha256(exc.evidence_path.read_bytes()).hexdigest())
                          if exc.evidence_path.exists() else store.safe(diagnostic_result="PROTOCOL_DIAGNOSTIC_FAILED",
                              final_execution_result="PROTOCOL_DIAGNOSTIC_FAILED", seal_status="SEAL_UNCONFIRMED"))
                result.update(error=str(exc), evidence_path=str(exc.evidence_path))
                print(json.dumps(result, sort_keys=True))
                return 2
            anchors = {"evidence": sha256(path.read_bytes()).hexdigest(),
                "receipt": sha256((path.parent / sealing.RECEIPT_NAME).read_bytes()).hexdigest(),
                "completion": sha256((path.parent / sealing.COMPLETION_NAME).read_bytes()).hexdigest()}
            result = execution.verify_files(path, anchors["evidence"], receipt_path=path.parent / sealing.RECEIPT_NAME,
                expected_receipt_sha256=anchors["receipt"], completion_path=path.parent / sealing.COMPLETION_NAME,
                expected_completion_sha256=anchors["completion"])
            result.update(evidence_path=str(path), external_sha256=anchors)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED" else 2
        if args.plan_output:
            store.write(args.plan_output, plan)
        if args.authorization_draft_output:
            draft = audit.authorization_draft(args.approved_code_sha)
            draft.update(trade_date=args.date, target_at=args.target_at)
            plan_path = args.plan or args.plan_output
            if plan_path:
                draft["plan_file_sha256"] = sha256(plan_path.read_bytes()).hexdigest()
            store.write(args.authorization_draft_output, draft)
        print(json.dumps(store.safe(status="PROTOCOL_DIAGNOSTIC_DRAFT_VALIDATED_NOT_AUTHORIZED",
            plan=plan, source_requests=0, worker_starts=0,
            real_execution_entry="EXPLICIT_NETWORK_REQUIRES_SEPARATE_PM_APPROVAL"), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps(store.safe(status="PROTOCOL_DIAGNOSTIC_REJECTED", error=f"{type(exc).__name__}:{exc}",
            diagnostic_result="PROTOCOL_DIAGNOSTIC_FAILED", final_execution_result="PROTOCOL_DIAGNOSTIC_FAILED"), sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
