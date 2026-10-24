"""Offline protocol evidence/replay and distinct future authorization validation."""
import base64
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
import re

from . import minute_capture_audit as legacy
from . import minute_protocol_contract as contract
from .minute_protocol_trace import MAX_BODY_BYTES, MAX_INTERACTIONS, MAX_OUTPUT_BYTES, MAX_RECV_CALLS, ProtocolTrace


def authorization_draft(code_sha=None):
    return {"schema": contract.AUTHORIZATION_VERSION, "execution_contract": contract.EXECUTION_CONTRACT,
            "status": "DRAFT_NOT_AUTHORIZED", "code_sha": code_sha, "trade_date": None,
            "target_at": None, "plan_hash": contract.frozen_plan()["plan_hash"], "single_use": True,
            "plan_file_sha256": None, "approval_source": None, "preflight": None, "local_config": None}


def validate_authorization(value, *, code_sha, now, _during_preparation=False):
    if (value.get("schema") != contract.AUTHORIZATION_VERSION
            or value.get("execution_contract") != contract.EXECUTION_CONTRACT
            or value.get("status") != "PM_APPROVED_SINGLE_PROTOCOL_DIAGNOSTIC"
            or value.get("code_sha") != code_sha or not re.fullmatch(r"[0-9a-f]{40}", code_sha)
            or value.get("plan_hash") != contract.frozen_plan()["plan_hash"]
            or value.get("single_use") is not True or not value.get("approval_source")):
        raise ValueError("independent_protocol_PM_authorization_required")
    target = datetime.fromisoformat(value["target_at"])
    if (target.utcoffset() != timedelta(hours=8) or target.date().isoformat() != value.get("trade_date")
            or now.date() != target.date() or now.utcoffset() != timedelta(hours=8)
            or now >= target - timedelta(seconds=0 if _during_preparation else 5)):
        raise ValueError("protocol_authorization_date_or_preparation_window_invalid")
    for name in ("preflight", "local_config"):
        reference = value[name]
        legacy.read_anchored(reference["path"], reference["sha256"])
    # This validates bindings only. It does not grant approval or validate the
    # future operational preflight's logic, service, calendar or clock freshness.
    return {"bindings_valid": True, "sampling_authorized": False,
            "operational_preflight_review_still_required": True}


def make_evidence(result, *, test_only):
    value = legacy.safe(schema=contract.EVIDENCE_VERSION, verifier=contract.VERIFIER_VERSION,
        execution_contract=contract.EXECUTION_CONTRACT, plan=contract.frozen_plan(),
        batch_id=result["task"]["batch_id"], code_sha=result["task"]["code_sha"],
        trade_date=result["task"]["trade_date"], test_only=test_only, result=result,
        root_cause="ROOT_CAUSE_UNCONFIRMED", capture_budget="CAPTURE_BUDGET_FEASIBILITY_UNPROVEN")
    return {**value, "evidence_hash": legacy.digest(value)}


def _blob(value):
    data = value["data"]
    raw = base64.b64decode(data["base64"], validate=True)
    if (len(raw) != data["captured_length"] or sha256(raw).hexdigest() != data["captured_sha256"]
            or type(data["returned_length"]) is not int or data["returned_length"] < len(raw)
            or data["capture_complete"] is not (len(raw) == data["returned_length"])):
        raise ValueError("protocol_blob_integrity_invalid")
    return raw


def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("protocol_time_missing_or_invalid:" + name)
    return value


def _absolute_timing(result, events):
    """Assess execution separately: a well-preserved timeout is still evidence."""
    measured = {}
    try:
        task = result["task"]
        def number(mapping, name):
            return _number(mapping.get(name), name)
        target = number(task, "target_monotonic")
        work_deadline = number(task, "io_deadline_monotonic")
        hard_deadline = number(task, "hard_deadline_monotonic")
        start = number(task, "preparation_started_monotonic")
        prep_deadline = number(task, "preparation_work_deadline")
        process_start = number(result, "process_started_monotonic")
        entered = number(result, "controller_entered_monotonic")
        ready_checked = number(result, "ready_checked_monotonic")
        sent = number(result, "go_sent_monotonic")
        completed = number(result, "normal_exit_checked_monotonic")
        finished = number(result, "finished_monotonic")
        cleanup_deadline = number(result, "cleanup_deadline_monotonic")
        ready = [event for event in events if event.get("kind") == "READY_LOCAL_ONLY"]
        go = [event for event in events if event.get("kind") == "GO_ACCEPTED"]
        if len(ready) != 1 or len(go) != 1:
            raise ValueError("protocol_timing_READY_GO_required")
        ready_at = number(ready[0], "ready_monotonic")
        go_sent = number(go[0], "sent_monotonic")
        received = number(go[0], "received_monotonic")
        accepted = number(go[0], "monotonic")
        # In the unchanged controller this finally-entry marker is sampled
        # AFTER normal-exit checks AND successful output decode/hash/freeze.
        # It is a conservative upper bound, not an invented freeze timestamp.
        measured = {"preparation_elapsed_ms": (ready_checked - start) * 1000,
            "waiting_after_ready_ms": (sent - ready_checked) * 1000,
            "work_elapsed_ms": (completed - sent) * 1000,
            "cleanup_ms": (finished - completed) * 1000,
            "total_residence_ms": (finished - start) * 1000,
            "elapsed_ms": (finished - start) * 1000,
            "wave_elapsed_ms": (finished - target) * 1000,
            "normal_exit_and_freeze_completed_by_monotonic": completed,
            "absolute_work_deadline_monotonic": work_deadline,
            "absolute_cleanup_deadline_monotonic": hard_deadline}
        if not (process_start == start <= entered <= ready_at <= ready_checked < prep_deadline
                and target <= sent == go_sent <= received <= accepted <= completed <= finished):
            raise ValueError("protocol_absolute_stage_order_invalid")
        if completed >= work_deadline:
            raise ValueError("protocol_normal_exit_or_output_freeze_absolute_deadline_exceeded")
        if (cleanup_deadline != min(hard_deadline, completed + .5)
                or not finished < cleanup_deadline <= hard_deadline):
            raise ValueError("protocol_shared_cleanup_absolute_deadline_exceeded")
        for name in ("preparation_elapsed_ms", "waiting_after_ready_ms", "work_elapsed_ms", "cleanup_ms",
                     "total_residence_ms", "elapsed_ms", "wave_elapsed_ms"):
            declared = number(result, name)
            # Arithmetic consistency only; no tolerance is added to deadlines.
            if not math.isclose(declared, measured[name], rel_tol=1e-9, abs_tol=1e-6):
                raise ValueError("protocol_declared_duration_mismatch:" + name)
        freeze_ms = number(result, "output_freeze_ms")
        startup_ms = number(result, "process_startup_and_scope_ms")
        if (result.get("output_frozen_before_work_deadline") is not True
                or freeze_ms > measured["work_elapsed_ms"]
                or startup_ms > measured["preparation_elapsed_ms"]):
            raise ValueError("protocol_output_freeze_or_startup_evidence_invalid")
        cleanup_parts = sum(number(result, name) for name in
            ("termination_ms", "drain_and_exit_ms", "pipe_and_scope_close_ms"))
        if cleanup_parts > measured["cleanup_ms"]:
            raise ValueError("protocol_cleanup_stage_durations_exceed_shared_budget")
        cursor = start
        released = False
        for event in events:
            kind = event.get("kind")
            if kind == "READY_LOCAL_ONLY":
                at = number(event, "ready_monotonic")
            elif kind == "GO_ACCEPTED":
                released = True
                at = number(event, "monotonic")
            elif kind == "client_stage":
                at = number(event, "monotonic")
                if not released and at > ready_at:
                    raise ValueError("protocol_local_stage_after_READY")
                if "elapsed_ms" in event:
                    number(event, "elapsed_ms")
            elif kind == "protocol_io":
                if not released:
                    raise ValueError("protocol_request_before_GO")
                record, operation = event["record"], event["operation"]
                if operation == "parser_input":
                    at = number(record, "at_monotonic")
                else:
                    began = number(record, "started_monotonic")
                    at = number(record, "completed_monotonic") if operation.endswith("_result") else began
                    if not accepted <= began <= at:
                        raise ValueError("protocol_request_time_order_invalid")
            else:
                continue
            if not cursor <= at <= completed or (released and at < accepted):
                raise ValueError("protocol_raw_stage_order_or_completion_invalid")
            cursor = at
        return {"timing_contract_verified": True, "timing_errors": [], "recomputed_timing": measured}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {"timing_contract_verified": False, "timing_errors": [str(exc)], "recomputed_timing": measured}


def verify(value):
    errors = []
    result, terminal, trace = {}, None, None
    malformed_stream = False
    events = []
    audit_only_v1 = value.get("verifier") == contract.AUDIT_ONLY_VERIFIER_VERSION
    audit_only_v2 = value.get("verifier") == contract.AUDIT_ONLY_V2
    historical = audit_only_v1 or audit_only_v2
    try:
        if (value.get("schema") != ("mootdx_protocol_diagnostic_evidence_v1" if historical else contract.EVIDENCE_VERSION)
                or value.get("verifier") not in (contract.VERIFIER_VERSION, contract.AUDIT_ONLY_VERIFIER_VERSION, contract.AUDIT_ONLY_V2)
                or value.get("execution_contract") != contract.EXECUTION_CONTRACT
                or value.get("evidence_hash") != legacy.digest({k: v for k, v in value.items() if k != "evidence_hash"})):
            raise ValueError("protocol_evidence_identity_or_hash_invalid")
        contract.check_plan(value["plan"], audit_only_v1=audit_only_v1, audit_only_v2=audit_only_v2)
        result = value["result"]
        task = result["task"]
        contract.check_task(task, audit_only_v1=audit_only_v1, audit_only_v2=audit_only_v2)
        if result.get("task_hash") != legacy.digest(task) or value.get("test_only") is not task.get("test_only", False):
            raise ValueError("protocol_task_hash_or_test_scope_invalid")
        if any(value[name] != task[name] for name in ("batch_id", "code_sha", "trade_date")):
            raise ValueError("protocol_batch_identity_invalid")
        for name in ("stdout", "stderr"):
            if result[name + "_sha256"] != sha256(result[name].encode()).hexdigest():
                raise ValueError("protocol_raw_stream_hash_invalid")
        if len(result["stdout"].encode()) + len(result["stderr"].encode()) > MAX_OUTPUT_BYTES:
            raise ValueError("protocol_output_size_exceeded")
        trace = ProtocolTrace(deadline=float("inf"))
        for line in result["stdout"].splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                # An interrupted final line remains a valid failed raw capture;
                # it can never provide a successful terminal result.
                terminal = None
                malformed_stream = True
                continue
            if event.get("execution_worker_version") != contract.WORKER_VERSION:
                raise ValueError("protocol_stream_version_invalid")
            events.append(event)
            if "ok" in event:
                terminal = event
            if event.get("kind") != "protocol_io":
                continue
            index, operation, record = event["interaction"], event["operation"], event["record"]
            if type(index) is not int or not 0 <= index < MAX_INTERACTIONS:
                raise ValueError("protocol_interaction_index_invalid")
            if operation in ("send_started", "send_not_dispatched"):
                if index != len(trace.interactions):
                    raise ValueError("protocol_send_order_invalid")
                if len(_blob(record)) > 4096:
                    raise ValueError("protocol_send_material_bound_invalid")
                trace.interactions.append({"index": index, "send": record, "recv": [], "parser_inputs": []})
            else:
                if index != len(trace.interactions) - 1:
                    raise ValueError("protocol_recv_or_parser_order_invalid")
                group = trace.interactions[index]
                if operation == "send_result":
                    if "returned_length" in group["send"] or any(record[k] != group["send"][k] for k in ("started_monotonic", "requested_length", "positional_flags", "keyword_flags")):
                        raise ValueError("protocol_send_result_identity_invalid")
                    group["send"].update(record)
                elif operation == "recv_started":
                    if len(group["recv"]) >= MAX_RECV_CALLS or record["call_index"] != len(group["recv"]):
                        raise ValueError("protocol_recv_limit_or_index_invalid")
                    group["recv"].append(record)
                elif operation == "recv_result":
                    if (not group["recv"] or record["call_index"] != len(group["recv"]) - 1
                            or "returned_length" in group["recv"][-1]
                            or any(record[k] != group["recv"][-1][k] for k in ("started_monotonic", "requested_length", "positional_flags", "keyword_flags"))):
                        raise ValueError("protocol_recv_result_identity_invalid")
                    if record.get("data") is not None:
                        _blob(record)
                        if record["returned_length"] != record["data"]["returned_length"] or record["eof"] is not (record["returned_length"] == 0):
                            raise ValueError("protocol_recv_count_invalid")
                    group["recv"][-1].update(record)
                    if sum((r.get("data") or {}).get("captured_length", 0) for r in group["recv"]) > MAX_BODY_BYTES + 16:
                        raise ValueError("protocol_recv_material_bound_invalid")
                elif operation == "parser_input":
                    if group["parser_inputs"]:
                        raise ValueError("protocol_duplicate_parser_input")
                    if len(_blob(record)) > MAX_BODY_BYTES:
                        raise ValueError("protocol_parser_material_bound_invalid")
                    group["parser_inputs"].append(record)
                else:
                    raise ValueError("protocol_operation_unknown")
            if operation != "parser_input" and "completed_monotonic" in record:
                if record["started_monotonic"] > record["completed_monotonic"]:
                    raise ValueError("protocol_io_monotonic_order_invalid")
            if record.get("data"):
                trace.retained_bytes += record["data"]["captured_length"]
        if terminal:
            claimed = (terminal.get("payload") or terminal).get("trace_summary")
            if claimed:
                trace.failure, trace.hooks_restored = claimed["failure"], claimed["hooks_restored"]
                # Output-limit failure can prevent the final un-emitted record
                # from reaching the parent; do not invent its raw bytes.
                actual = trace.summary()
                if claimed.get("complete") and actual != claimed:
                    raise ValueError("protocol_derived_summary_replay_mismatch")
        if result.get("ok") is True:
            if malformed_stream:
                raise ValueError("protocol_success_stream_contains_invalid_JSON")
            ready = [e for e in events if e.get("kind") == "READY_LOCAL_ONLY"]
            go = [e for e in events if e.get("kind") == "GO_ACCEPTED"]
            if len(ready) != 1 or len(go) != 1:
                raise ValueError("protocol_READY_GO_count_invalid")
            contract.check_ready(ready[0], task, ready[0]["pid"])
            if (result.get("ready_pid_in_owned_job") is not True or result.get("tree_tracking") != "windows_job"
                    or not task["target_monotonic"] <= result["go_sent_monotonic"] == go[0]["sent_monotonic"]
                    <= go[0]["received_monotonic"] <= go[0]["monotonic"] < task["io_deadline_monotonic"]
                    or go[0]["network_attempts_before_go"] != 0 or go[0]["task_hash"] != legacy.digest(task)
                    or result.get("returncode") != 0 or result.get("stdout_eof") is not True or result.get("stderr_eof") is not True
                    or not terminal or terminal.get("ok") is not True
                    or terminal["payload"].get("test_only") is not value["test_only"]
                    or terminal["payload"].get("boundary_attempts") != [] or terminal["payload"].get("allowed_connections") != 1):
                raise ValueError("protocol_GO_job_exit_or_boundary_invalid")
        for name in ("data_ready", "hard_gate_authorized", "sampling_authorized"):
            if value.get(name) is not False:
                raise ValueError("protocol_safety_flag_invalid")
        if any(value.get(k) != [] for k in ("records", "research_ranking", "candidates", "tickets", "orders")):
            raise ValueError("protocol_safety_output_invalid")
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        errors.append(str(exc))
    if trace is not None and (terminal is None or result.get("output_complete") is False):
        trace.failure = trace.failure or "protocol_terminal_missing_or_output_incomplete"
    timing = _absolute_timing(result, events)
    success = bool(not errors and not historical and timing["timing_contract_verified"]
                   and result.get("ok") is True and terminal and terminal.get("ok") is True)
    if success:
        claimed = terminal["payload"]["trace_summary"]
        success = (claimed["complete"] and claimed["hooks_restored"] and len(trace.interactions) == 4
                   and len(trace.interactions[-1]["parser_inputs"]) == 1
                   and result.get("cleanup_complete") is True and result.get("tree_empty_observed") is True
                   and result.get("output_complete") is True and result.get("pipes_closed") is True)
    return legacy.safe(verifier=contract.VERIFIER_VERSION, evidence_integrity_verified=not errors,
        evidence_verifier=value.get("verifier"), legacy_verifier_audit_only=historical, **timing,
        errors=errors, diagnostic_result="PROTOCOL_DIAGNOSTIC_CAPTURED" if success else "PROTOCOL_DIAGNOSTIC_FAILED",
        trace_summary=trace.summary() if trace else None, evidence_hash=value.get("evidence_hash"),
        test_only=value.get("test_only"), raw_stream_complete=not malformed_stream and terminal is not None,
        root_cause="ROOT_CAUSE_UNCONFIRMED",
        capture_budget="CAPTURE_BUDGET_FEASIBILITY_UNPROVEN")
