"""Versioned candidate audit. Old authorization/plan/evidence never selects this path."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import minute_capture_audit as legacy
from . import minute_local_prepare_contract as contract
from .minute_local_prepare_process import run_prepared_wave
from .probe_worker_process import _decode_worker_output

ROOT, AUDIT_ROOT = legacy.ROOT, legacy.AUDIT_ROOT


def task_for(target, *, actual, started, batch_id, wave, code_sha, config, test_only=False):
    target_mono = started + (target - actual).total_seconds()
    task = {"worker_version": contract.WORKER_VERSION, "plan_hash": contract.frozen_plan()["plan_hash"],
        "batch_id": batch_id, "code_sha": code_sha, "wave": wave, "codes": list(legacy.CODES),
        "endpoint": dict(legacy.ENDPOINT), "source_version": legacy.SOURCE_VERSION,
        "dependencies": dict(contract.DEPENDENCIES), "config": dict(config),
        "worker_sha256": contract.worker_file_sha256(), "parent_pid": os.getpid(), "test_only": test_only,
        "trade_date": target.date().isoformat(), "scheduled_at": target.isoformat(),
        "preparation_started_monotonic": started, "target_monotonic": target_mono,
        "preparation_work_deadline": min(started + 3.5, target_mono - 1.5),
        "preparation_hard_deadline": min(started + 4, target_mono - 1),
        "io_deadline_monotonic": target_mono + 1.5, "hard_deadline_monotonic": target_mono + 2}
    contract.check_task(task)
    return task


def wave_errors(wave, *, trade_date, batch_id, code_sha):
    errors = []
    result, task = wave["result"], wave["result"]["task"]
    contract.check_task(task)
    index = wave["wave"]
    opening_task = {k: v for k, v in task.items() if k != "launch_root_pid"}
    opening_bytes = (json.dumps({"task": opening_task, "started_at": wave["parent_started_at"]},
                               ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if wave.get("start_receipt_sha256") != sha256(opening_bytes).hexdigest():
        errors.append("prepared_start_receipt_hash_mismatch")
    if (task["wave"] != index or task["trade_date"] != trade_date or task["batch_id"] != batch_id
            or task["code_sha"] != code_sha or task["scheduled_at"] != f"{trade_date}T{legacy.CLOCKS[index]}+08:00"
            or task.get("launch_root_pid") != result["pid"] or result["task_hash"] != legacy.digest(task)):
        errors.append("prepared_wave_binding_invalid")
    for name in ("stdout", "stderr"):
        if result[name + "_sha256"] != sha256(result[name].encode()).hexdigest():
            errors.append("prepared_stream_hash_mismatch:" + name)
    values = legacy.events_from_result(result)
    ready = [v for v in values if v["kind"] == "READY_LOCAL_ONLY"]
    go = [v for v in values if v["kind"] == "GO_ACCEPTED"]
    if result.get("ok"):
        if len(ready) != 1 or len(go) != 1 or result["ready"] != ready[0]:
            errors.append("prepared_READY_GO_count_invalid")
        else:
            contract.check_ready(ready[0], task, ready[0]["pid"])
            chain = ready[0].get("parent_chain", [])
            if (result.get("ready_pid_in_owned_job") is not True or result.get("tree_tracking") != "windows_job"
                    or not chain or chain[0] != ready[0]["pid"] or task["launch_root_pid"] not in chain
                    or task["parent_pid"] not in chain
                    or chain.index(task["launch_root_pid"]) >= chain.index(task["parent_pid"])):
                errors.append("prepared_READY_job_or_parent_chain_invalid")
            if (go[0].get("task_hash") != legacy.digest(task) or go[0].get("network_attempts_before_go") != 0
                    or go[0].get("sent_monotonic") != result["go_sent_monotonic"]
                    or not task["target_monotonic"] <= result["go_sent_monotonic"] <= go[0]["received_monotonic"]
                    <= go[0]["monotonic"] <= task["io_deadline_monotonic"]):
                errors.append("prepared_GO_time_or_identity_invalid")
        terminal = _decode_worker_output(result["stdout"])
        if (not terminal or terminal.get("ok") is not True or terminal.get("payload") != result.get("payload")
                or terminal["payload"].get("boundary_attempts") != []
                or type(terminal["payload"].get("allowed_connections")) is not int
                or terminal["payload"]["allowed_connections"] != 1):
            errors.append("prepared_terminal_or_boundary_invalid")
        names = ("process_started_monotonic", "ready_checked_monotonic", "go_sent_monotonic",
                 "normal_exit_checked_monotonic", "finished_monotonic")
        if any(type(result.get(k)) not in (float, int) or not math.isfinite(result[k]) for k in names):
            errors.append("prepared_timing_not_finite")
        else:
            start, ready_at, sent, exited, end = (result[k] for k in names)
            expected = {"preparation_elapsed_ms": (ready_at - start) * 1000,
                "waiting_after_ready_ms": (sent - ready_at) * 1000,
                "work_elapsed_ms": (exited - sent) * 1000,
                "wave_elapsed_ms": max(0, (end - task["target_monotonic"]) * 1000),
                "total_residence_ms": (end - start) * 1000, "cleanup_ms": (end - exited) * 1000}
            if (not task["preparation_started_monotonic"] <= start <= ready_at < task["preparation_work_deadline"]
                    or not ready_at <= sent <= exited <= task["io_deadline_monotonic"]
                    or not exited <= end < result["cleanup_deadline_monotonic"] <= task["hard_deadline_monotonic"]
                    or end - start > 7 or end - exited > .5
                    or any(type(result.get(k)) not in (float, int) or not math.isfinite(result[k])
                           or abs(result[k] - value) > 1e-6 for k, value in expected.items())):
                errors.append("prepared_absolute_deadline_or_accounting_invalid")
        if (result.get("cleanup_complete") is not True or result.get("tree_empty_observed") is not True
                or result.get("stdout_eof") is not True or result.get("stderr_eof") is not True):
            errors.append("prepared_cleanup_not_confirmed")
    return errors


def analyse(waves, trade_date, batch_id, code_sha):
    converted, issues = deepcopy(waves), []
    for wave, view in zip(waves, converted):
        issues.extend(wave_errors(wave, trade_date=trade_date, batch_id=batch_id, code_sha=code_sha))
        # Reuse native-row checks on their original network window; never rewrite
        # the original result's seven-second residence or its stream timestamps.
        view["result"]["elapsed_ms"] = wave["result"].get("wave_elapsed_ms") or math.inf
    summary = legacy.analyse(converted, trade_date)
    issues.extend(summary["issues"])
    summary.update(issues=list(dict.fromkeys(issues)), execution_contract=contract.EXECUTION_CONTRACT,
                   capture_budget="CAPTURE_BUDGET_FEASIBILITY_UNPROVEN")
    if issues:
        summary["status"] = "MINUTE_CAPTURE_AUDIT_FAILED"
    return summary


def _authorization(auth, trade_date, plan, current, *, root=ROOT, test_only=False):
    from . import minute_capture_preflight as preflight
    if (auth.get("schema") != contract.AUTHORIZATION_VERSION or auth.get("status") != "PM_APPROVED_SINGLE_AUDIT"
            or auth.get("trade_date") != trade_date or auth.get("plan_hash") != plan["plan_hash"]
            or auth.get("execution_contract") != contract.EXECUTION_CONTRACT or auth.get("single_use") is not True
            or auth.get("test_only", False) is not test_only):
        raise ValueError("new_version_date_specific_PM_authorization_required")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=root, text=True).strip()
    if head != auth.get("code_sha") or status:
        raise ValueError("authorized_prepared_code_or_worktree_mismatch")
    reference = auth["preflight"]
    verified = preflight.verify_file(reference["path"], reference["sha256"], trade_date=trade_date,
        approved_sha=auth["code_sha"], current=current, root=root, _test_only=test_only,
        execution_contract=contract.EXECUTION_CONTRACT)
    if not verified["integrity_verified"]:
        raise ValueError("prepared_preflight_rejected:" + ";".join(verified["errors"]))
    return legacy.read_anchored(reference["path"], reference["sha256"])


def capture(plan, trade_date, *, authorization, audit_root=AUDIT_ROOT, _test=None):
    contract.check_plan(plan)
    datetime.fromisoformat(trade_date)
    clock = _test["clock"] if _test else lambda: datetime.now(legacy.CN)
    sleep = _test["sleep"] if _test else time.sleep
    current = clock()
    if current.date().isoformat() != trade_date or current.utcoffset() != timedelta(hours=8):
        raise ValueError("prepared_current_date_or_timezone_invalid")
    if _test:
        if _test.get("test_only") is not True:
            raise ValueError("prepared_explicit_test_context_required")
    elif Path(audit_root).resolve() != AUDIT_ROOT.resolve():
        raise ValueError("prepared_shared_ignored_audit_root_required")
    guard = Path(audit_root) / "cleanup-incomplete.json"
    if guard.exists():
        raise ValueError("prior_worker_cleanup_unconfirmed_preserve_guard_no_new_worker")
    if legacy.pending_preparation_receipts(audit_root):
        raise ValueError("pending_preparation_cleanup_receipt_preserve_no_new_worker")
    first = datetime.fromisoformat(f"{trade_date}T{legacy.CLOCKS[0]}+08:00") - timedelta(seconds=5)
    if current > first:
        raise ValueError("prepared_first_preparation_window_missed")
    preflight = (_authorization(authorization, trade_date, plan, current,
        root=_test.get("root", ROOT) if _test else ROOT, test_only=bool(_test))
        if not _test or _test.get("validate_authorization") else None)
    code_sha, config = authorization["code_sha"], authorization["local_config"]
    directory = Path(audit_root) / trade_date
    batch_id = uuid.uuid4().hex
    # Share legacy date intent and persistent cleanup guard: no new-version escape
    # from a historical failed attempt or unconfirmed process cleanup.
    legacy.write(directory / "intent.json", legacy.safe(schema=contract.EVIDENCE_VERSION,
        batch_id=batch_id, plan_hash=plan["plan_hash"], authorization=authorization, started_at=current.isoformat()))
    waves, error = [], None
    try:
        for index, label in enumerate(legacy.CLOCKS):
            target = datetime.fromisoformat(f"{trade_date}T{label}+08:00")
            start_at = target - timedelta(seconds=5)
            while clock() < start_at:
                sleep(min(.05, max(0, (start_at - clock()).total_seconds())))
            started, actual = time.perf_counter(), clock()
            if preflight is not None and index == 0:
                from .minute_capture_preflight import fresh_at
                fresh_at(preflight, actual)
            task = task_for(target, actual=actual, started=started, batch_id=batch_id, wave=index,
                            code_sha=code_sha, config=config, test_only=bool(_test))
            start_path = legacy.write(directory / f"wave-{index}.started.json", {"task": task, "started_at": actual.isoformat()})
            start_sha256 = sha256(start_path.read_bytes()).hexdigest()
            check_go = None
            if preflight is not None and index == 0:
                check_go = lambda: fresh_at(preflight, clock())
            result = run_prepared_wave(task, worker_command=_test.get("worker_command") if _test else None,
                                      before_go=check_go)
            publish_started = time.perf_counter()
            wave = {"wave": index, "target_at": target.isoformat(), "parent_started_at": actual.isoformat(),
                    "parent_completed_at": clock().isoformat(), "start_receipt_sha256": start_sha256, "result": result}
            waves.append(wave)
            if result["cleanup_complete"] is not True:
                legacy.write(guard, legacy.safe(status="WORKER_CLEANUP_INCOMPLETE", batch_id=batch_id,
                    wave=index, result=result, automatic_reset_allowed=False, residual_process_state="UNCONFIRMED"))
            legacy.write(directory / f"wave-{index}.json", wave)
            if not result["cleanup_complete"]:
                raise ValueError("prepared_cleanup_incomplete_stop_all_later_workers")
            if not result["ok"]:
                raise ValueError("prepared_wave_failed:" + result["error"])
            partial = analyse(waves, trade_date, batch_id, code_sha)
            issues = [v for v in partial["issues"] if v != "four_waves_twenty_bars_requests_not_complete"]
            if issues:
                raise ValueError(issues[0])
            publish_ms = (time.perf_counter() - publish_started) * 1000
            legacy.write(directory / f"wave-{index}.publication.json", {"wave": index,
                "published_at": clock().isoformat(), "publication_and_validation_ms": publish_ms})
            if publish_ms > plan["per_wave_publish_and_validation_budget_ms"]:
                raise ValueError("prepared_wave_publication_budget_exhausted")
    except Exception as exc:
        error = f"{type(exc).__name__}:{exc}"
    confirmation_started = time.perf_counter()
    summary = analyse(waves, trade_date, batch_id, code_sha)
    confirmation_ms = (time.perf_counter() - confirmation_started) * 1000
    if confirmation_ms > plan["final_confirmation_budget_ms"]:
        error = error or "prepared_final_confirmation_budget_exhausted"
    if clock() > datetime.fromisoformat(f"{trade_date}T14:51:05+08:00"):
        error = error or "prepared_confirmation_after_collection_deadline"
    if error:
        summary = legacy.safe(**{**summary, "status": "MINUTE_CAPTURE_AUDIT_FAILED", "capture_error": error})
    evidence = legacy.safe(schema=contract.EVIDENCE_VERSION, verifier=contract.VERIFIER_VERSION,
        verifier_hash=contract.verifier_hash(),
        plan=plan, trade_date=trade_date, code_sha=code_sha, batch_id=batch_id, test_only=bool(_test),
        authorization=authorization, authorization_hash=legacy.digest(authorization),
        waves=waves, summary=summary, capture_error=error, confirmation_elapsed_ms=confirmation_ms,
        source_finality="UNPROVEN")
    evidence["evidence_hash"] = legacy.digest(evidence)
    seal_started = time.perf_counter()
    path = legacy.write(directory / "evidence-v2.json", evidence)
    legacy.write(directory / "file-anchor-v2.json", {"file": path.name, "sha256": sha256(path.read_bytes()).hexdigest()})
    late = clock() > datetime.fromisoformat(f"{trade_date}T14:51:05+08:00")
    legacy.write(directory / "seal-receipt-v2.json", legacy.safe(
        status="MINUTE_CAPTURE_AUDIT_FAILED" if late or time.perf_counter() - seal_started > 3 else summary["status"],
        evidence_file=path.name, sealed_at=clock().isoformat(), late_seal=late,
        cleanup_complete=summary.get("cleanup_complete"), source_available_at="UNPROVEN"))
    return evidence, path


def verify(evidence, expected_file_sha256, actual_file_sha256):
    errors, summary = [], None
    if not expected_file_sha256 or expected_file_sha256 != actual_file_sha256:
        errors.append("external_file_sha256_mismatch")
    if (evidence.get("schema") != contract.EVIDENCE_VERSION or evidence.get("verifier") != contract.VERIFIER_VERSION
            or evidence.get("verifier_hash") != contract.verifier_hash()
            or evidence.get("plan") != contract.frozen_plan()):
        errors.append("prepared_evidence_version_or_plan_mismatch_old_audit_only")
    if evidence.get("evidence_hash") != legacy.digest({k: v for k, v in evidence.items() if k != "evidence_hash"}):
        errors.append("prepared_evidence_hash_mismatch")
    try:
        auth = evidence["authorization"]
        if (auth.get("schema") != contract.AUTHORIZATION_VERSION or auth.get("plan_hash") != evidence["plan"]["plan_hash"]
                or auth.get("status") != "PM_APPROVED_SINGLE_AUDIT"
                or auth.get("test_only", False) is not evidence.get("test_only")
                or auth.get("trade_date") != evidence["trade_date"] or auth.get("code_sha") != evidence["code_sha"]
                or auth.get("execution_contract") != contract.EXECUTION_CONTRACT or auth.get("single_use") is not True
                or evidence.get("authorization_hash") != legacy.digest(auth)):
            errors.append("prepared_authorization_binding_invalid")
        if any(w["result"]["task"]["config"] != auth["local_config"]
               or w["result"]["task"].get("test_only", False) is not evidence.get("test_only")
               for w in evidence["waves"]):
            errors.append("prepared_config_or_scope_binding_invalid")
        summary = analyse(evidence["waves"], evidence["trade_date"], evidence["batch_id"], evidence["code_sha"])
        if evidence.get("capture_error"):
            summary = legacy.safe(**{**summary, "status": "MINUTE_CAPTURE_AUDIT_FAILED",
                                   "capture_error": evidence["capture_error"]})
        if summary != evidence.get("summary"):
            errors.append("prepared_recomputed_summary_mismatch")
    except (KeyError, ValueError, TypeError, IndexError, OverflowError):
        errors.append("prepared_wave_structure_invalid")
    if any(evidence.get(k) != value for k, value in legacy.safe().items()):
        errors.append("prepared_unsafe_or_promoted_output")
    return legacy.safe(status="MINUTE_CAPTURE_EVIDENCE_VERIFIED" if not errors else "MINUTE_CAPTURE_EVIDENCE_INVALID",
        verifier=contract.VERIFIER_VERSION, integrity_verified=not errors, errors=errors, summary=summary,
        verifier_hash=contract.verifier_hash(),
        evidence_hash=evidence.get("evidence_hash"), source_finality="UNPROVEN")
