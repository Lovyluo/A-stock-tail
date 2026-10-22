"""Versioned candidate execution contract; never a source qualification profile."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from datetime import date
import math
from pathlib import Path
import re

from . import minute_capture_audit as legacy

EXECUTION_CONTRACT = "local_prepare_v2"
PLAN_VERSION = "mootdx_minute_capture_plan_v2"
WORKER_VERSION = "mootdx_local_prepare_worker_v2"
EVIDENCE_VERSION = "mootdx_minute_capture_audit_v3"
VERIFIER_VERSION = "mootdx_minute_capture_verifier_v3"
SEAL_RECEIPT_VERSION = "mootdx_minute_seal_receipt_v1"
SEAL_COMPLETION_VERSION = "mootdx_minute_seal_completion_v1"
PREFLIGHT_VERSION = "minute_capture_preflight_v2"
PREFLIGHT_VERIFIER = "minute_capture_preflight_verifier_v2"
AUTHORIZATION_VERSION = "minute_capture_manual_authorization_v3"
DEPENDENCIES = {"mootdx": "0.11.7", "tdxpy": "0.2.7"}


def frozen_plan():
    plan = {key: value for key, value in deepcopy(legacy.frozen_plan()).items() if key != "plan_hash"}
    plan.update(schema=PLAN_VERSION, version=EVIDENCE_VERSION, worker_version=WORKER_VERSION,
        verifier=VERIFIER_VERSION, authorization_version=AUTHORIZATION_VERSION,
        execution_contract=EXECUTION_CONTRACT, preparation_lead_ms=5000,
        preparation_total_budget_ms=4000, preparation_work_budget_ms=3500,
        preparation_cleanup_reserve_ms=500, ready_latest_before_target_ms=1000,
        ready_work_deadline_before_target_ms=1500, maximum_worker_residence_ms=7000,
        preparation_network_policy="deny_before_imports_count_even_swallowed_attempts",
        go_policy="one_parent_GO_then_EOF_never_reset_absolute_deadlines",
        process_startup_budget_phase="local_preparation_counted_separately_not_in_2000ms",
        parent_death_policy="windows_job_kill_on_parent_handle_close",
        execution_enabled_by_default=False, preconnection=False,
        cross_wave_worker_reuse=False, cross_wave_connection_reuse=False,
        seal_receipt_version=SEAL_RECEIPT_VERSION, seal_completion_version=SEAL_COMPLETION_VERSION,
        seal_policy="external_anchors_for_raw_receipt_and_post_write_completion_witness")
    return {**plan, "plan_hash": legacy.digest(plan)}


def check_plan(value):
    if value != frozen_plan():
        raise ValueError("local_prepare_frozen_plan_mismatch")


def check_task(task):
    if not isinstance(task, dict):
        raise ValueError("prepared_worker_task_object_required")
    if (task.get("worker_version") != WORKER_VERSION or task.get("plan_hash") != frozen_plan()["plan_hash"]
            or task.get("codes") != list(legacy.CODES) or task.get("endpoint") != legacy.ENDPOINT
            or task.get("source_version") != legacy.SOURCE_VERSION or task.get("dependencies") != DEPENDENCIES
            or type(task.get("wave")) is not int or not 0 <= task["wave"] < 4
            or not isinstance(task.get("batch_id"), str) or not task["batch_id"]
            or not re.fullmatch(r"[0-9a-f]{40}", str(task.get("code_sha", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(task.get("worker_sha256", "")))
            or type(task.get("parent_pid")) is not int or task["parent_pid"] <= 0):
        raise ValueError("prepared_worker_identity_invalid")
    date.fromisoformat(task["trade_date"])
    reference = task["config"]
    if (set(reference) != {"path", "sha256"} or not Path(reference["path"]).is_absolute()
            or not re.fullmatch(r"[0-9a-f]{64}", str(reference["sha256"]))):
        raise ValueError("prepared_local_config_identity_invalid")
    names = ("target_monotonic", "preparation_started_monotonic", "preparation_work_deadline",
             "preparation_hard_deadline", "io_deadline_monotonic", "hard_deadline_monotonic")
    if any(type(task.get(k)) not in (float, int) or not math.isfinite(task[k]) for k in names):
        raise ValueError("prepared_absolute_deadline_invalid")
    target, start = task["target_monotonic"], task["preparation_started_monotonic"]
    expected = {"preparation_work_deadline": min(start + 3.5, target - 1.5),
                "preparation_hard_deadline": min(start + 4, target - 1),
                "io_deadline_monotonic": target + 1.5, "hard_deadline_monotonic": target + 2}
    if not 0 < target - start <= 5 or any(abs(task[k] - v) > 1e-9 for k, v in expected.items()):
        raise ValueError("prepared_deadline_contract_mismatch")


def go_message(task, sent_monotonic):
    return {"kind": "GO", "task_hash": legacy.digest(task), "worker_version": WORKER_VERSION,
            "sent_monotonic": sent_monotonic}


def check_go(value, task, received_monotonic):
    sent = value.get("sent_monotonic")
    if (type(sent) not in (float, int) or not math.isfinite(sent)
            or value != go_message(task, sent)):
        raise ValueError("prepared_GO_identity_or_kind_invalid")
    if sent < task["target_monotonic"] or received_monotonic < task["target_monotonic"]:
        raise ValueError("prepared_GO_before_target")
    if not sent <= received_monotonic < task["io_deadline_monotonic"]:
        raise ValueError("prepared_GO_monotonic_order_or_budget_invalid")


def check_ready(value, task, pid):
    ready_at = value.get("ready_monotonic")
    if (value.get("kind") != "READY_LOCAL_ONLY" or value.get("task_hash") != legacy.digest(task)
            or value.get("worker_version") != WORKER_VERSION or value.get("pid") != pid
            or value.get("dependencies") != DEPENDENCIES or value.get("config") != task["config"]
            or value.get("code_sha") != task["code_sha"] or value.get("wave") != task["wave"]
            or value.get("batch_id") != task["batch_id"] or value.get("plan_hash") != task["plan_hash"]
            or type(value.get("network_attempts_before_go")) is not int or value["network_attempts_before_go"] != 0
            or type(ready_at) not in (float, int) or not math.isfinite(ready_at)
            or ready_at > task["preparation_work_deadline"]):
        raise ValueError("ready_local_only_identity_or_budget_invalid")


def worker_file_sha256():
    return sha256(Path(__file__).with_name("minute_local_prepare_worker.py").read_bytes()).hexdigest()


def verifier_hash():
    return legacy.digest({"verifier": VERIFIER_VERSION, "plan_hash": frozen_plan()["plan_hash"],
        "rules": ["external_file_sha256", "raw_stream_sha256", "READY_job_identity",
                  "one_GO_with_send_receive_order", "absolute_phase_deadlines_no_reset",
                  "native_rows_and_request_replay", "same_batch_code_config_scope",
                  "bounded_tree_and_pipe_cleanup", "immutable_start_cleanup_witness_no_reset",
                  "external_seal_receipt_and_post_write_completion_anchors",
                  "recompute_seal_absolute_deadline_and_monotonic_budget",
                  "separate_integrity_capture_seal_final_execution",
                  "audit_integrity_never_grants_admission"]})
