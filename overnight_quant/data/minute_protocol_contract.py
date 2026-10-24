"""Single-stock protocol investigation; never the four-wave minute capture plan."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import math
import os
from pathlib import Path
import re

from . import minute_capture_audit as legacy
from . import minute_local_prepare_contract as prepared
from .minute_protocol_trace import MAX_OUTPUT_BYTES

WORKER_VERSION = "mootdx_protocol_diagnostic_worker_v1"
WORKER_MODULE = "overnight_quant.data.minute_protocol_worker"
PLAN_VERSION = "mootdx_protocol_diagnostic_plan_v1"
AUTHORIZATION_VERSION = "mootdx_protocol_diagnostic_authorization_v1"
EVIDENCE_VERSION = "mootdx_protocol_diagnostic_evidence_v1"
AUDIT_ONLY_VERIFIER_VERSION = "mootdx_protocol_diagnostic_verifier_v1"
VERIFIER_VERSION = "mootdx_protocol_diagnostic_verifier_v2"
EXECUTION_CONTRACT = "single_bars_protocol_diagnostic_v1"
CODES = ["000001"]


def frozen_plan(*, audit_only_v1=False):
    value = {"schema": PLAN_VERSION, "execution_contract": EXECUTION_CONTRACT,
        "worker_version": WORKER_VERSION, "evidence_version": EVIDENCE_VERSION,
        "authorization_version": AUTHORIZATION_VERSION,
        "verifier": AUDIT_ONLY_VERIFIER_VERSION if audit_only_v1 else VERIFIER_VERSION,
        "endpoint": legacy.ENDPOINT, "codes": CODES, "request": {"symbol": "000001", "frequency": "1m", "start": 0, "offset": 800},
        "source_version": legacy.SOURCE_VERSION, "dependencies": prepared.DEPENDENCIES,
        "handshake_requests": 3, "bars_requests": 1, "transaction_requests": 0,
        "max_interactions": 4, "max_recv_per_interaction": 32, "max_body_bytes": 65535,
        "max_send_bytes": 4096, "max_output_bytes": MAX_OUTPUT_BYTES,
        "preparation_lead_ms": 5000, "preparation_work_ms": 3500, "preparation_cleanup_ms": 500,
        "network_work_ms": 1500, "absolute_network_and_cleanup_ms": 2000, "shared_cleanup_ms": 500,
        "max_residence_ms": 7000, "concurrency": 1, "retry": False, "fallback": False,
        "date": None, "target_at": None, "execution_authorized": False,
        "purpose": "protocol_evidence_only_not_coverage_or_source_qualification",
        "root_cause": "ROOT_CAUSE_UNCONFIRMED", "budget": "CAPTURE_BUDGET_FEASIBILITY_UNPROVEN"}
    value = deepcopy(value)
    return {**value, "plan_hash": legacy.digest(value)}


def check_plan(value, *, audit_only_v1=False):
    if value != frozen_plan(audit_only_v1=audit_only_v1):
        raise ValueError("protocol_diagnostic_plan_mismatch")


def worker_file_sha256():
    return sha256(Path(__file__).with_name("minute_protocol_worker.py").read_bytes()).hexdigest()


def check_task(task, *, audit_only_v1=False):
    if (task.get("worker_version") != WORKER_VERSION
            or task.get("plan_hash") != frozen_plan(audit_only_v1=audit_only_v1)["plan_hash"]
            or task.get("codes") != CODES or task.get("endpoint") != legacy.ENDPOINT
            or task.get("dependencies") != prepared.DEPENDENCIES or task.get("source_version") != legacy.SOURCE_VERSION
            or type(task.get("wave")) is not int or task["wave"] != 0 or type(task.get("parent_pid")) is not int or task["parent_pid"] <= 0
            or not isinstance(task.get("batch_id"), str) or not task["batch_id"]
            or not re.fullmatch(r"[0-9a-f]{40}", str(task.get("code_sha", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(task.get("worker_sha256", "")))):
        raise ValueError("protocol_diagnostic_task_identity_invalid")
    stamp = datetime.fromisoformat(task["scheduled_at"])
    if stamp.utcoffset() != timedelta(hours=8) or stamp.date().isoformat() != task["trade_date"]:
        raise ValueError("protocol_diagnostic_date_or_timezone_invalid")
    ref = task["config"]
    if set(ref) != {"path", "sha256"} or not Path(ref["path"]).is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]):
        raise ValueError("protocol_diagnostic_config_invalid")
    names = ("target_monotonic", "preparation_started_monotonic", "preparation_work_deadline",
             "preparation_hard_deadline", "io_deadline_monotonic", "hard_deadline_monotonic")
    if any(type(task.get(k)) not in (int, float) or not math.isfinite(task[k]) for k in names):
        raise ValueError("protocol_diagnostic_deadline_invalid")
    target, start = task["target_monotonic"], task["preparation_started_monotonic"]
    expected = {"preparation_work_deadline": min(start + 3.5, target - 1.5),
        "preparation_hard_deadline": min(start + 4, target - 1),
        "io_deadline_monotonic": target + 1.5, "hard_deadline_monotonic": target + 2}
    if not 0 < target - start <= 5 or any(abs(task[k] - v) > 1e-9 for k, v in expected.items()):
        raise ValueError("protocol_diagnostic_deadline_contract_mismatch")


def go_message(task, sent_monotonic):
    return {"kind": "GO", "task_hash": legacy.digest(task), "worker_version": WORKER_VERSION,
            "sent_monotonic": sent_monotonic}


def check_go(value, task, received_monotonic):
    sent = value.get("sent_monotonic")
    if type(sent) not in (int, float) or not math.isfinite(sent) or value != go_message(task, sent):
        raise ValueError("protocol_diagnostic_GO_identity_invalid")
    if not task["target_monotonic"] <= sent <= received_monotonic < task["io_deadline_monotonic"]:
        raise ValueError("protocol_diagnostic_GO_time_invalid_no_reset")


def check_ready(value, task, pid):
    at = value.get("ready_monotonic")
    if (value.get("kind") != "READY_LOCAL_ONLY" or value.get("worker_version") != WORKER_VERSION
            or value.get("task_hash") != legacy.digest(task) or value.get("pid") != pid
            or value.get("code_sha") != task["code_sha"] or value.get("config") != task["config"]
            or value.get("dependencies") != task["dependencies"] or value.get("batch_id") != task["batch_id"]
            or value.get("plan_hash") != task["plan_hash"] or value.get("wave") != 0
            or type(value.get("network_attempts_before_go")) is not int or value["network_attempts_before_go"] != 0
            or type(at) not in (int, float) or not math.isfinite(at) or at > task["preparation_work_deadline"]):
        raise ValueError("protocol_diagnostic_READY_invalid")


def task_for(target, *, actual, started, batch_id, code_sha, config, test_only=False):
    t = started + (target - actual).total_seconds()
    value = {"worker_version": WORKER_VERSION, "plan_hash": frozen_plan()["plan_hash"],
        "batch_id": batch_id, "code_sha": code_sha, "wave": 0, "codes": list(CODES),
        "endpoint": dict(legacy.ENDPOINT), "source_version": legacy.SOURCE_VERSION,
        "dependencies": dict(prepared.DEPENDENCIES), "config": dict(config),
        "worker_sha256": worker_file_sha256(), "parent_pid": os.getpid(), "test_only": test_only,
        "trade_date": target.date().isoformat(), "scheduled_at": target.isoformat(),
        "preparation_started_monotonic": started, "target_monotonic": t,
        "preparation_work_deadline": min(started + 3.5, t - 1.5),
        "preparation_hard_deadline": min(started + 4, t - 1),
        "io_deadline_monotonic": t + 1.5, "hard_deadline_monotonic": t + 2}
    check_task(value)
    return value
