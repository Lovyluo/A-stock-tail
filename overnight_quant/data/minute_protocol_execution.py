"""One opt-in protocol diagnostic; no four-wave or source-qualification path."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from . import minute_capture_audit as store
from . import minute_capture_preflight as preflight
from . import minute_protocol_audit as audit
from . import minute_protocol_contract as contract
from . import minute_local_prepare_seal as sealing
from .minute_local_prepare_process import run_prepared_wave

AUDIT_RELATIVE = "overnight_quant/data/cache/minute_protocol_diagnostic"
SHARED_RELATIVE = "overnight_quant/data/cache/minute_capture_audit"


def blockers(root, *, audit_relative=AUDIT_RELATIVE):
    """Never repair, rename or ignore a pre-existing incomplete owner."""
    shared, own = Path(root) / SHARED_RELATIVE, Path(root) / audit_relative
    if any((path / "cleanup-incomplete.json").exists() for path in (shared, own)):
        raise ValueError("shared_or_protocol_cleanup_incomplete_preserve_no_launch")
    if store.pending_preparation_receipts(shared):
        raise ValueError("shared_pending_preparation_preserve_no_launch")
    directories = {path.parent for pattern in ("*/intent.json", "*/request.started.json") for path in own.glob(pattern)}
    for directory in sorted(directories):
        intent = directory / "intent.json"
        try:
            json.loads(intent.read_bytes())
            start = intent.parent / "request.started.json"
            opened = json.loads(start.read_bytes())
            closed = json.loads((intent.parent / "request.json").read_bytes())
            task = dict(closed["result"]["task"])
            task.pop("launch_root_pid", None)
            task.pop("launch", None)
            if (closed["result"].get("cleanup_complete") is not True or task != opened["task"]
                    or closed["start_receipt_sha256"] != sha256(start.read_bytes()).hexdigest()):
                raise ValueError("protocol_cleanup_not_confirmed")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("protocol_pending_preparation_preserve_no_launch:" + str(intent.parent)) from exc


def authorize(auth, *, trade_date, target_at, code_sha, root, current, test_only=False,
              check_git=True, during_preparation=False, profile=contract):
    contract = profile
    audit.validate_authorization(auth, code_sha=code_sha, now=current,
        _during_preparation=during_preparation, profile=contract)
    if (auth.get("trade_date") != trade_date or auth.get("target_at") != target_at
            or auth.get("test_only", False) is not test_only
            or type(auth.get("approval_source")) is not str or not auth["approval_source"].strip()):
        raise ValueError("protocol_authorization_scope_or_approval_invalid")
    if check_git:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=5).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=root, text=True, timeout=5).strip()
        if head != code_sha or dirty:
            raise ValueError("protocol_fixed_code_or_clean_worktree_mismatch")
    if hasattr(contract, "check_parent_authorization"):
        contract.check_parent_authorization(auth, root, controller=check_git)
    reference = auth["preflight"]
    verified = preflight.verify_file(reference["path"], reference["sha256"], trade_date=trade_date,
        approved_sha=code_sha, current=current, root=root, execution_contract=contract.EXECUTION_CONTRACT,
        target_at=target_at, _test_only=test_only)
    if not verified["integrity_verified"]:
        raise ValueError("protocol_actual_preflight_rejected:" + ";".join(verified["errors"]))
    permitted = preflight.read(reference)
    if auth.get("plan_file_sha256") != permitted["inputs"]["plan"]["sha256"]:
        raise ValueError("protocol_authorized_plan_file_anchor_mismatch")
    return permitted


def _launch_materials(task, root, *, profile=contract):
    opening = preflight.read(task["launch"])
    basic_task = {k: v for k, v in task.items() if k not in ("launch", "launch_root_pid")}
    if opening.get("schema") != "protocol_launch_receipt_v1" or opening.get("task") != basic_task:
        raise ValueError("protocol_launch_receipt_task_mismatch")
    directory = Path(root) / getattr(profile, "AUDIT_RELATIVE", AUDIT_RELATIVE) / task["trade_date"]
    if Path(task["launch"]["path"]).resolve() != (directory / "request.started.json").resolve():
        raise ValueError("protocol_launch_path_mismatch")
    if Path(opening["intent"]["path"]).resolve() != (directory / "intent.json").resolve():
        raise ValueError("protocol_launch_intent_path_mismatch")
    intent = preflight.read(opening["intent"])
    if (intent.get("batch_id") != task["batch_id"] or intent.get("code_sha") != task["code_sha"]
            or intent.get("plan_hash") != task["plan_hash"] or intent.get("target_at") != task["scheduled_at"]
            or intent.get("authorization") != opening["authorization"]):
        raise ValueError("protocol_launch_intent_identity_mismatch")
    auth = preflight.read(opening["authorization"])
    if auth["local_config"] != task["config"]:
        raise ValueError("protocol_launch_config_mismatch")
    if hasattr(profile, "check_parent_launch"):
        profile.check_parent_launch(task, auth, root)
    return auth


def verify_worker_launch(task, *, profile=contract):
    """A direct internal-worker invocation cannot skip the real preflight."""
    auth = _launch_materials(task, store.ROOT, profile=profile)
    # The controller already queried current Git and the live environment.
    # Replay those anchored checks here; spawning git would violate the
    # unchanged local-preparation boundary which forbids helper processes.
    return authorize(auth, trade_date=task["trade_date"], target_at=task["scheduled_at"], code_sha=task["code_sha"],
        root=store.ROOT, current=preflight.now(), during_preparation=True, check_git=False, profile=profile)


def capture(plan, trade_date, target_at, *, authorization, _test=None, profile=contract):
    contract = profile
    contract.check_plan(plan)
    if _test is not None and _test.get("test_only") is not True:
        raise ValueError("explicit_protocol_test_context_required")
    root = Path(_test["root"]) if _test else store.ROOT
    clock = _test["clock"] if _test else preflight.now
    sleep = _test["sleep"] if _test else time.sleep
    writer = _test.get("write", store.write) if _test else store.write
    auth = preflight.read(authorization)
    current, target = clock(), preflight.stamp(target_at)
    check_blockers = getattr(contract, "blockers", blockers)
    check_blockers(root)
    permitted = authorize(auth, trade_date=trade_date, target_at=target_at, code_sha=auth["code_sha"],
                          root=root, current=current, test_only=bool(_test), profile=contract)
    directory = root / getattr(contract, "AUDIT_RELATIVE", AUDIT_RELATIVE) / trade_date
    if directory.exists():
        raise ValueError("protocol_date_output_already_exists_no_second_intent")
    # A live query is still mandatory after validating the anchored preflight.
    # Only the identified current CLI/venv redirector ancestors are exempted;
    # protocol workers and all other controllers remain blockers.
    live_dir = root / "overnight_quant/data/cache/minute_protocol_preflight" / (uuid.uuid4().hex + "-live")
    live_dir.mkdir(parents=True, exist_ok=False)
    live = preflight._query(live_dir, trade_date, root,
        preflight._query_record(permitted["inputs"]["before_environment"])["checked_at"],
        test=_test, execution_contract=contract.EXECUTION_CONTRACT, controller_pid=os.getpid())
    preflight._environment(live, trade_date, auth["code_sha"], root, clock(), bool(_test),
                           execution_contract=contract.EXECUTION_CONTRACT)
    preflight.fresh_at(permitted, clock())
    if clock() >= target - timedelta(seconds=5):
        raise ValueError("protocol_first_preparation_window_missed")
    check_blockers(root)
    batch_id = uuid.uuid4().hex
    intent = writer(directory / "intent.json", store.safe(schema="protocol_diagnostic_intent_v1",
        batch_id=batch_id, code_sha=auth["code_sha"], trade_date=trade_date, target_at=target_at,
        plan_hash=plan["plan_hash"], authorization=authorization, live_environment=live,
        created_at=clock().isoformat(), single_use=True))
    while clock() < target - timedelta(seconds=5):
        sleep(min(.05, (target - timedelta(seconds=5) - clock()).total_seconds()))
    actual, started = clock(), time.perf_counter()
    preflight.fresh_at(permitted, actual)
    task = contract.task_for(target, actual=actual, started=started, batch_id=batch_id,
        code_sha=auth["code_sha"], config=auth["local_config"], test_only=bool(_test),
        **(contract.task_context(root, trade_date) if hasattr(contract, "task_context") else {}))
    opening = writer(directory / "request.started.json", {"schema": "protocol_launch_receipt_v1",
        "task": deepcopy(task), "authorization": authorization, "intent": preflight.ref(intent)})
    task["launch"] = preflight.ref(opening)
    result, error = None, None
    seal_started = None
    try:
        try:
            result = run_prepared_wave(task, contract_profile=contract,
                worker_command=_test.get("worker_command") if _test else None,
                worker_cwd=root,
                before_go=lambda: preflight.fresh_at(permitted, clock()))
        finally:
            # Receipt publication and controller evidence encoding/hash are
            # seal work too, not unaccounted time between work and sealing.
            seal_started = (clock(), result["finished_monotonic"] if result is not None else time.perf_counter())
        if result["cleanup_complete"] is not True:
            writer(root / SHARED_RELATIVE / "cleanup-incomplete.json", store.safe(
                status="PROTOCOL_CLEANUP_UNCONFIRMED", batch_id=batch_id, pid=result.get("pid"),
                result=result, automatic_reset_allowed=False))
        writer(directory / "request.json", {"result": result, "start_receipt_sha256": task["launch"]["sha256"]})
    except Exception as exc:
        error = f"{type(exc).__name__}:{exc}"
        if result is None:
            result = {"task": task, "task_hash": store.digest(task), "ok": False, "error": error,
                "stdout": "", "stderr": "", "stdout_sha256": sha256(b"").hexdigest(),
                "stderr_sha256": sha256(b"").hexdigest(), "cleanup_complete": False, "output_complete": False}
            writer(root / SHARED_RELATIVE / "cleanup-incomplete.json", store.safe(
                status="PROTOCOL_CONTROLLER_RESULT_UNCONFIRMED", batch_id=batch_id, automatic_reset_allowed=False))
    evidence = audit.make_evidence(result, test_only=bool(_test), profile=contract)
    evidence.update(authorization=auth, authorization_reference=authorization,
        project_root=str(root), preparation_started_at=actual.isoformat(),
        authorization_checked_at=current.isoformat(), live_environment=live, execution_error=error)
    evidence["evidence_hash"] = store.digest({k: v for k, v in evidence.items() if k != "evidence_hash"})
    path = sealing.seal(directory, evidence, clock=clock, write=writer, profile=contract, _started=seal_started)
    return evidence, path


def verify_files(path, expected_sha, *, receipt_path=None, expected_receipt_sha256=None,
                 completion_path=None, expected_completion_sha256=None, profile=contract):
    contract = profile
    errors, capture_result, seal_status = [], None, "SEAL_UNCONFIRMED"
    raw_integrity, evidence, raw = False, None, {}
    try:
        evidence = store.read_anchored(path, expected_sha)
        raw = audit.verify(evidence, profile=contract)
        raw_integrity, capture_result = raw["evidence_integrity_verified"], raw["diagnostic_result"]
        errors.extend(raw["errors"])
        auth = preflight.read(evidence["authorization_reference"])
        if auth != evidence["authorization"]:
            raise ValueError("protocol_authorization_reference_mismatch")
        if _launch_materials(evidence["result"]["task"], evidence["project_root"], profile=contract) != auth:
            raise ValueError("protocol_launch_authorization_mismatch")
        authorize(auth, trade_date=evidence["trade_date"], target_at=evidence["result"]["task"]["scheduled_at"],
            code_sha=evidence["code_sha"], root=evidence["project_root"],
            current=preflight.stamp(evidence["authorization_checked_at"]), test_only=evidence["test_only"],
            check_git=False, profile=contract)
        preflight._environment(evidence["live_environment"], evidence["trade_date"], evidence["code_sha"],
            evidence["project_root"], preflight.stamp(evidence["preparation_started_at"]), evidence["test_only"],
            execution_contract=contract.EXECUTION_CONTRACT)
        receipt = store.read_anchored(receipt_path, expected_receipt_sha256) if receipt_path else None
        completion = store.read_anchored(completion_path, expected_completion_sha256) if completion_path else None
        if receipt is not None and receipt.get("seal_started_monotonic") != evidence["result"].get("finished_monotonic"):
            raise ValueError("protocol_seal_must_include_all_post_worker_publication_time")
        seal_errors, seal_status = sealing.verify(evidence, expected_sha, receipt, expected_receipt_sha256,
            sha256(Path(receipt_path).read_bytes()).hexdigest() if receipt_path else None,
            completion, expected_completion_sha256,
            sha256(Path(completion_path).read_bytes()).hexdigest() if completion_path else None, profile=contract)
        errors.extend(seal_errors)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        errors.append(f"{type(exc).__name__}:{exc}")
    success = (not errors and capture_result == "PROTOCOL_DIAGNOSTIC_CAPTURED"
               and seal_status == "SEAL_COMPLETED_WITHIN_BUDGET" and not evidence.get("execution_error"))
    final = "PROTOCOL_DIAGNOSTIC_CAPTURED" if success else "PROTOCOL_DIAGNOSTIC_FAILED"
    return store.safe(verifier=contract.VERIFIER_VERSION, evidence_integrity_verified=raw_integrity,
        execution_materials_integrity_verified=not errors, errors=errors, capture_result=capture_result,
        seal_status=seal_status, diagnostic_result=final, final_execution_result=final,
        test_only=evidence.get("test_only") if evidence else None,
        evidence_hash=evidence.get("evidence_hash") if evidence else None,
        capture_error=evidence.get("execution_error") or evidence.get("result", {}).get("error") if evidence else None,
        trace_summary=raw.get("trace_summary"), raw_stream_complete=raw.get("raw_stream_complete", False),
        timing_contract_verified=raw.get("timing_contract_verified", False),
        timing_errors=raw.get("timing_errors", []), recomputed_timing=raw.get("recomputed_timing", {}),
        root_cause="ROOT_CAUSE_UNCONFIRMED", capture_budget="CAPTURE_BUDGET_FEASIBILITY_UNPROVEN")
