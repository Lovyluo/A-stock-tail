"""Four predeclared independent experiments using the existing one-shot capture."""
from datetime import datetime
from contextvars import ContextVar
from hashlib import sha256
import json
import math
import os
from pathlib import Path

from . import minute_capture_audit as store
from . import minute_capture_preflight as pre
from . import minute_protocol_audit as audit
from . import minute_protocol_execution as single
from . import minute_protocol_differential_contract as contract
from . import minute_local_prepare_seal as seal

_ACTIVE_INTENT = ContextVar("current_closed_differential_matrix", default=None)


def blockers(root):
    single.blockers(root)
    for key, *_ in contract.EXPERIMENTS:
        single.blockers(root, audit_relative=contract.ExperimentProfile(key).AUDIT_RELATIVE)
    for path in (Path(root) / contract.MATRIX_ROOT).glob("*/matrix-intent.json"):
        reference = pre.ref(path)
        active = _ACTIVE_INTENT.get()
        intent = pre.read(reference)
        if reference == active and intent.get("owner_pid") == os.getpid():
            continue
        try:
            closed = json.loads((path.parent / "matrix-result-v1.json").read_bytes())
            if (closed.get("schema") != contract.MATRIX_EVIDENCE or closed.get("intent") != reference
                    or closed.get("evidence_hash") != store.digest({k: v for k, v in closed.items() if k != "evidence_hash"})):
                raise ValueError("matrix_result_not_bound")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("pending_or_damaged_matrix_preserve_no_launch:" + str(path)) from exc


def draft(code_sha=None):
    return store.safe(schema=contract.MATRIX_AUTHORIZATION, status="DRAFT_NOT_AUTHORIZED",
        trade_date=None, code_sha=code_sha, single_use=True, matrix_authorization_id=None,
        plan_hash=contract.frozen_plan()["plan_hash"], plan_file_sha256=None,
        approval_source=None, experiments={row[0]: {"plan": None, "authorization": None}
                                            for row in contract.EXPERIMENTS})


def parent_intent(root, trade_date):
    return json.loads((Path(root) / contract.MATRIX_ROOT / trade_date / "matrix-intent.json").read_bytes())


def parent_authorization(auth, root, *, controller):
    intent = parent_intent(root, auth["trade_date"])
    outer = pre.read(intent["authorization"])
    if (intent.get("schema") != "protocol_differential_intent_v1"
            or intent.get("matrix_authorization_id") != auth["matrix_authorization_id"]
            or intent.get("code_sha") != auth["code_sha"] or intent.get("trade_date") != auth["trade_date"]
            or intent.get("plan_hash") != contract.frozen_plan()["plan_hash"]
            or (controller and intent.get("owner_pid") != os.getpid())
            or outer.get("status") != "PM_APPROVED_SINGLE_DIFFERENTIAL_MATRIX"
            or outer.get("matrix_authorization_id") != auth["matrix_authorization_id"]
            or pre.read(outer["experiments"][auth["experiment_id"]]["authorization"]) != auth):
        raise ValueError("differential_parent_intent_or_approval_mismatch")


def _outer(reference, plan, plan_ref, day, root, current, test_only):
    value = pre.read(reference)
    expected_ids = [row[0] for row in contract.EXPERIMENTS]
    if (value.get("schema") != contract.MATRIX_AUTHORIZATION
            or value.get("status") != "PM_APPROVED_SINGLE_DIFFERENTIAL_MATRIX"
            or value.get("trade_date") != day or value.get("single_use") is not True
            or value.get("test_only", False) is not test_only
            or value.get("plan_hash") != plan["plan_hash"] or pre.read(plan_ref) != plan
            or value.get("plan_file_sha256") != plan_ref["sha256"]
            or not isinstance(value.get("approval_source"), str) or not value["approval_source"].strip()
            or not isinstance(value.get("matrix_authorization_id"), str) or not value["matrix_authorization_id"]
            or list(value.get("experiments", {})) != expected_ids):
        raise ValueError("separate_PM_approval_for_exact_A_D_matrix_required")
    configs = []
    for key in expected_ids:
        profile = contract.ExperimentProfile(key)
        refs = value["experiments"][key]
        profile.check_plan(pre.read(refs["plan"]))
        auth = pre.read(refs["authorization"])
        audit.validate_authorization(auth, code_sha=value["code_sha"], now=current, profile=profile)
        if (auth.get("matrix_authorization_id") != value["matrix_authorization_id"]
                or auth.get("test_only", False) is not test_only or auth.get("trade_date") != day
                or auth.get("approval_source") != value["approval_source"]
                or auth.get("plan_file_sha256") != refs["plan"]["sha256"]):
            raise ValueError("differential_child_approval_not_bound_to_matrix")
        verified = pre.verify_file(auth["preflight"]["path"], auth["preflight"]["sha256"],
            trade_date=day, approved_sha=value["code_sha"], current=current, root=root,
            execution_contract=profile.EXECUTION_CONTRACT, target_at=auth["target_at"], _test_only=test_only)
        if not verified["integrity_verified"]:
            raise ValueError("differential_actual_preflight_rejected:" + key + ":" + ";".join(verified["errors"]))
        permitted = pre.read(auth["preflight"])
        if permitted["inputs"]["plan"] != refs["plan"]:
            raise ValueError("differential_preflight_plan_anchor_mismatch")
        configs.append(auth["local_config"])
    if any(config != configs[0] for config in configs):
        raise ValueError("differential_experiments_must_use_same_anchored_config")
    return value


def _references(path):
    return {"evidence": pre.ref(path), "receipt": pre.ref(path.parent / seal.RECEIPT_NAME),
            "completion": pre.ref(path.parent / seal.COMPLETION_NAME)}


def _verify_item(key, refs):
    return single.verify_files(refs["evidence"]["path"], refs["evidence"]["sha256"],
        receipt_path=refs["receipt"]["path"], expected_receipt_sha256=refs["receipt"]["sha256"],
        completion_path=refs["completion"]["path"], expected_completion_sha256=refs["completion"]["sha256"],
        profile=contract.ExperimentProfile(key))


def continuation(refs, verified, *, current):
    """Failure is not success: this only permits the next independent experiment."""
    if (not verified["evidence_integrity_verified"] or not verified["execution_materials_integrity_verified"]
            or verified["seal_status"] != "SEAL_COMPLETED_WITHIN_BUDGET" or not verified["raw_stream_complete"]):
        return False
    evidence = pre.read(refs["evidence"])
    result = evidence["result"]
    try:
        completed = pre.read(refs["completion"])
        if current < pre.stamp(completed["seal_completed_at"]):
            return False
        pre.fresh_at(pre.read(evidence["authorization"]["preflight"]), current)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if any(result.get(k) is not True for k in ("cleanup_complete", "tree_empty_observed", "pipes_closed",
                                              "stdout_eof", "stderr_eof", "output_complete")):
        return False
    if verified["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED":
        return True
    # Preserve the old success-freeze requirement. A separately sealed parser
    # rejection may continue, but a timeout, partial trace or lost process may not.
    trace = verified.get("trace_summary") or {}
    if not trace.get("complete") or not trace.get("hooks_restored") or result.get("request_timed_out") is not False:
        return False
    terminal = next((json.loads(line) for line in reversed(result["stdout"].splitlines())
                     if isinstance(json.loads(line), dict) and "ok" in json.loads(line)), {})
    chain = terminal.get("exception_chain") or []
    parser_failure = terminal.get("ok") is False and any(
        frame.get("file") == "get_security_bars.py" and frame.get("function") == "parseResponse"
        for item in chain for frame in item.get("frames", []))
    try:
        task = result["task"]
        start, end, work, hard, clean = (result["process_started_monotonic"], result["finished_monotonic"],
            task["io_deadline_monotonic"], task["hard_deadline_monotonic"], result["normal_exit_checked_monotonic"])
        if any(type(x) not in (float, int) or not math.isfinite(x) for x in (start, end, work, hard, clean)):
            return False
        timing = start <= clean < work and clean <= end < min(hard, clean + .5)
    except (KeyError, TypeError):
        return False
    return bool(parser_failure and timing and len(trace.get("interactions", [])) == 4
                and trace["interactions"][-1].get("parser_input_length") is not None)


def interpretation(items):
    if len(items) != 4:
        return "INCOMPLETE_NO_DIFFERENTIAL_CONCLUSION"
    passed = [row["verdict"]["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED" for row in items]
    last = [(row["verdict"].get("trace_summary") or {}).get("interactions", []) for row in items]
    shorts = [rows[-1].get("received_body_sha256") if len(rows) == 4
              and rows[-1].get("received_body_length") == 2 else None for rows in last]
    if not any(passed) and shorts[0] and len(set(shorts)) == 1:
        return "INCONCLUSIVE_NODE_SERVICE_PROTOCOL_STOP_UNCHANGED_NODE_TRIALS"
    if passed == [True, False, True, False]:
        return "REQUEST_SCALE_ASSOCIATION_NOT_A_LIMIT_OR_COUNT_REDUCTION_APPROVAL"
    if passed in ([True, True, False, False], [False, False, True, True]):
        return "SECURITY_OR_MARKET_ASSOCIATION_NOT_ROOT_CAUSE"
    return "NO_SOURCE_QUALIFICATION_OR_SEMANTIC_CONCLUSION"


def capture(plan, trade_date, *, authorization, plan_ref, _test=None):
    contract.check_plan(plan)
    if _test is not None and _test.get("test_only") is not True:
        raise ValueError("explicit_differential_test_context_required")
    root = Path(_test["root"]) if _test else store.ROOT
    clock = _test["clock"] if _test else pre.now
    blockers(root)
    auth = _outer(authorization, plan, plan_ref, trade_date, root, clock(), bool(_test))
    directory = root / contract.MATRIX_ROOT / trade_date
    if directory.exists():
        raise ValueError("differential_date_output_already_exists_no_second_matrix")
    intent = store.write(directory / "matrix-intent.json", store.safe(schema="protocol_differential_intent_v1",
        trade_date=trade_date, code_sha=auth["code_sha"], owner_pid=os.getpid(),
        matrix_authorization_id=auth["matrix_authorization_id"], authorization=authorization,
        plan_hash=plan["plan_hash"], created_at=clock().isoformat(), single_use=True, test_only=bool(_test)))
    token = _ACTIVE_INTENT.set(pre.ref(intent))
    try:
        items, error = _capture_items(auth, trade_date, root, clock, _test)
    finally:
        _ACTIVE_INTENT.reset(token)
    result = store.safe(schema=contract.MATRIX_EVIDENCE, plan=plan, trade_date=trade_date,
        code_sha=auth["code_sha"], matrix_authorization_id=auth["matrix_authorization_id"],
        intent=pre.ref(intent), test_only=bool(_test), items=items, error=error,
        experiment_set_complete=len(items) == 4, interpretation=interpretation(items),
        diagnostic_result="DIFFERENTIAL_ALL_ITEMS_PARSED" if len(items) == 4 and all(
            x["verdict"]["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED" for x in items) else "DIFFERENTIAL_DIAGNOSTIC_FAILED")
    result["evidence_hash"] = store.digest(result)
    path = store.write(directory / "matrix-result-v1.json", result)
    return result, path


def _capture_items(auth, trade_date, root, clock, test):
    items, error = [], None
    for key, *_ in contract.EXPERIMENTS:
        profile = contract.ExperimentProfile(key)
        refs = auth["experiments"][key]
        child = pre.read(refs["authorization"])
        try:
            blockers(root)
            context = dict(test) if test else None
            if context and "worker_modes" in context:
                context["worker_command"] = [*context["worker_command_base"], context["worker_modes"][key], "differential"]
            if context and "query_commands" in context:
                context["query_command"] = context["query_commands"][key]
            _, path = single.capture(pre.read(refs["plan"]), trade_date, child["target_at"],
                authorization=refs["authorization"], profile=profile, _test=context)
            anchors = _references(path)
            verdict = _verify_item(key, anchors)
            decision_at = clock()
            allowed = continuation(anchors, verdict, current=decision_at)
            items.append({"id": key, "files": anchors, "verdict": verdict,
                "continuation_checked_at": decision_at.isoformat(), "continuation_allowed": allowed})
            if not allowed:
                break
        except Exception as exc:
            error = f"{key}:{type(exc).__name__}:{exc}"
            # Any partial original remains where the one-shot executor wrote it.
            break
    return items, error


def verify(path, expected_sha):
    errors = []
    try:
        value = store.read_anchored(path, expected_sha)
        contract.check_plan(value["plan"])
        if value.get("schema") != contract.MATRIX_EVIDENCE or value.get("evidence_hash") != store.digest(
                {k: v for k, v in value.items() if k != "evidence_hash"}):
            raise ValueError("differential_result_identity_or_hash_invalid")
        intent = pre.read(value["intent"])
        outer = pre.read(intent["authorization"])
        if any(value.get(k) != intent.get(k) for k in ("trade_date", "code_sha", "matrix_authorization_id")):
            raise ValueError("differential_result_intent_identity_mismatch")
        ids = [row["id"] for row in value["items"]]
        if ids != [row[0] for row in contract.EXPERIMENTS][:len(ids)] or len(ids) > 4:
            raise ValueError("differential_order_or_duplicate_invalid")
        for i, row in enumerate(value["items"]):
            actual = _verify_item(row["id"], row["files"])
            evidence = pre.read(row["files"]["evidence"])
            child = pre.read(outer["experiments"][row["id"]]["authorization"])
            if (evidence["authorization"] != child or evidence["result"]["task"]["matrix_intent"] != value["intent"]
                    or actual != row["verdict"] or row["continuation_allowed"] is not continuation(
                        row["files"], actual, current=pre.stamp(row["continuation_checked_at"]))
                    or not actual["evidence_integrity_verified"] or not actual["execution_materials_integrity_verified"]
                    or (i < len(ids) - 1 and not row["continuation_allowed"])):
                raise ValueError("differential_leaf_binding_or_continuation_invalid")
        expected_result = "DIFFERENTIAL_ALL_ITEMS_PARSED" if len(ids) == 4 and all(
            x["verdict"]["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED" for x in value["items"]) else "DIFFERENTIAL_DIAGNOSTIC_FAILED"
        if (value["diagnostic_result"] != expected_result or value["experiment_set_complete"] is not (len(ids) == 4)
                or value["interpretation"] != interpretation(value["items"])):
            raise ValueError("differential_claimed_conclusion_invalid")
        if any(value.get(k) != v for k, v in store.safe().items()):
            raise ValueError("differential_unsafe_output")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        errors.append(str(exc))
    return store.safe(status="DIFFERENTIAL_EVIDENCE_VERIFIED" if not errors else "DIFFERENTIAL_EVIDENCE_INVALID",
        integrity_verified=not errors, errors=errors, diagnostic_result=value.get("diagnostic_result") if not errors else "DIFFERENTIAL_DIAGNOSTIC_FAILED",
        interpretation=value.get("interpretation") if not errors else "UNKNOWN",
        test_only=value.get("test_only") if not errors else None,
        experiment_set_complete=value.get("experiment_set_complete", False) if not errors else False)
