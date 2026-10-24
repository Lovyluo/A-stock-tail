"""Synthetic re-signed timelines; no source requests or qualification claims."""
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
import socket
import subprocess
import sys

import pytest

from overnight_quant.data import minute_capture_audit as store
from overnight_quant.data import minute_protocol_audit as audit
from overnight_quant.data import minute_protocol_contract as contract
from overnight_quant.data.minute_local_prepare_process import run_prepared_wave
from overnight_quant.tests.test_phase114_minute_protocol_trace import FIXTURE, CLI, task


def resign(value, events=None):
    result = value["result"]
    if events is not None:
        result["stdout"] = "\n".join(json.dumps(e) for e in events) + "\n"
    for name in ("stdout", "stderr"):
        result[name + "_sha256"] = sha256(result[name].encode()).hexdigest()
    value["evidence_hash"] = store.digest({k: v for k, v in value.items() if k != "evidence_hash"})
    return value


def stream_events(result):
    # Include the terminal object (which deliberately has no `kind`).
    return [json.loads(line) for line in result["stdout"].splitlines()]


def timeline(value, *, go_delay=0, completed=None, finished=None):
    result, events = value["result"], stream_events(value["result"])
    released = False
    for event in events:
        if event.get("kind") == "GO_ACCEPTED":
            released = True
        if released:
            for key in ("monotonic", "sent_monotonic", "received_monotonic"):
                if key in event:
                    event[key] += go_delay
            for key in ("started_monotonic", "completed_monotonic", "at_monotonic"):
                if key in event.get("record", {}):
                    event["record"][key] += go_delay
    result["go_sent_monotonic"] += go_delay
    if completed is not None:
        result["normal_exit_checked_monotonic"] = completed
    if finished is not None:
        result["finished_monotonic"] = finished
    start = result["task"]["preparation_started_monotonic"]
    end, done = result["normal_exit_checked_monotonic"], result["finished_monotonic"]
    result.update(work_elapsed_ms=(end - result["go_sent_monotonic"]) * 1000,
        cleanup_ms=(done - end) * 1000,
        waiting_after_ready_ms=(result["go_sent_monotonic"] - result["ready_checked_monotonic"]) * 1000,
        wave_elapsed_ms=(done - result["task"]["target_monotonic"]) * 1000,
        total_residence_ms=(done - start) * 1000, elapsed_ms=(done - start) * 1000,
        cleanup_deadline_monotonic=min(result["task"]["hard_deadline_monotonic"], end + .5))
    return resign(value, events)


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    root = tmp_path_factory.mktemp("protocol-absolute-test-only")
    def denied(*args, **kwargs):
        raise AssertionError("Phase115 forbids external networking")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", denied)
        patch.setattr(socket, "getaddrinfo", denied)
        result = run_prepared_wave(task(root), contract_profile=contract,
                                  worker_command=[sys.executable, str(FIXTURE), "complete"])
    (root / "result.json").write_text(json.dumps(result), encoding="utf-8")
    assert result["ok"] and result["cleanup_complete"] and result["tree_empty_observed"], result
    value = audit.make_evidence(result, test_only=True)
    assert audit.verify(value)["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED", audit.verify(value)
    return value


def failed(value, reason):
    result = audit.verify(resign(value))
    assert result["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_FAILED", result
    assert not result["timing_contract_verified"], result
    assert any(reason in item for item in result["timing_errors"]), result
    assert result["data_ready"] is result["hard_gate_authorized"] is result["sampling_authorized"] is False
    assert all(result[name] == [] for name in ("records", "research_ranking", "candidates", "tickets", "orders"))
    return result


def test_late_GO_never_resets_absolute_exit_and_freeze_deadline(captured):
    value = deepcopy(captured)
    target = value["result"]["task"]["target_monotonic"]
    timeline(value, go_delay=.8, completed=target + 1.8, finished=target + 1.9)
    assert value["result"]["work_elapsed_ms"] < 1500
    assert value["result"]["cleanup_ms"] < 500
    result = failed(value, "absolute_deadline_exceeded")
    assert result["evidence_integrity_verified"]  # Authentic bytes do not imply valid execution.
    assert result["recomputed_timing"]["work_elapsed_ms"] == value["result"]["work_elapsed_ms"]


@pytest.mark.parametrize("offset,success", [(-.000001, True), (0, False), (.000001, False)])
def test_absolute_work_deadline_boundary_without_added_tolerance(captured, offset, success):
    value = deepcopy(captured)
    cutoff = value["result"]["task"]["io_deadline_monotonic"]
    timeline(value, completed=cutoff + offset, finished=cutoff + offset + .01)
    result = audit.verify(value)
    assert result["evidence_integrity_verified"]
    assert (result["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED") is success


@pytest.mark.parametrize("offset,success", [(-.000001, True), (0, False), (.000001, False)])
def test_shared_cleanup_and_absolute_hard_boundary(captured, offset, success):
    value = deepcopy(captured)
    cutoff = value["result"]["task"]["io_deadline_monotonic"]
    timeline(value, completed=cutoff - .00001, finished=cutoff + .49999 + offset)
    result = audit.verify(value)
    assert (result["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED") is success
    if not success:
        assert "shared_cleanup" in result["timing_errors"][0]


@pytest.mark.parametrize("field", ["preparation_elapsed_ms", "waiting_after_ready_ms", "work_elapsed_ms",
    "cleanup_ms", "total_residence_ms", "elapsed_ms", "wave_elapsed_ms"])
def test_declared_durations_are_recomputed_not_trusted(captured, field):
    value = deepcopy(captured)
    value["result"][field] += 1
    failed(value, "declared_duration_mismatch:" + field)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "1.0", None, -1])
@pytest.mark.parametrize("field", ["normal_exit_checked_monotonic", "finished_monotonic", "work_elapsed_ms",
                                  "output_freeze_ms", "cleanup_deadline_monotonic"])
def test_nonfinite_wrong_type_or_negative_time_rejected(captured, field, bad):
    value = deepcopy(captured)
    value["result"][field] = bad
    if type(bad) is float and not math.isfinite(bad):
        # Canonical evidence cannot even be re-signed with non-JSON numbers.
        with pytest.raises(ValueError, match="Out of range"):
            resign(value)
        checked = audit.verify(value)
        assert not checked["evidence_integrity_verified"]
        assert checked["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_FAILED"
        timing = audit._absolute_timing(value["result"], stream_events(value["result"]))
        assert not timing["timing_contract_verified"]
        assert "time_missing_or_invalid" in timing["timing_errors"][0]
    else:
        failed(value, "time_missing_or_invalid")


@pytest.mark.parametrize("field", ["process_started_monotonic", "controller_entered_monotonic",
    "ready_checked_monotonic", "go_sent_monotonic", "normal_exit_checked_monotonic",
    "finished_monotonic", "cleanup_deadline_monotonic", "output_freeze_ms"])
def test_missing_required_parent_stage_never_succeeds(captured, field):
    value = deepcopy(captured)
    del value["result"][field]
    failed(value, "time_missing_or_invalid")


@pytest.mark.parametrize("field", ["started_monotonic", "completed_monotonic", "at_monotonic"])
def test_raw_request_stage_times_required_finite_and_ordered(captured, field):
    value = deepcopy(captured)
    events = stream_events(value["result"])
    for event in events:
        if event.get("kind") == "protocol_io" and field in event["record"]:
            # All copies of the same boundary must agree, even after re-signing.
            event["record"][field] = math.nan
    resign(value, events)
    failed(value, "time_missing_or_invalid")


@pytest.mark.parametrize("field", ["started_monotonic", "completed_monotonic", "at_monotonic"])
def test_missing_request_boundary_is_not_inferred_from_claimed_elapsed(captured, field):
    value = deepcopy(captured)
    events = stream_events(value["result"])
    for event in events:
        if event.get("kind") == "protocol_io":
            event["record"].pop(field, None)
    failed(resign(value, events), "time_missing_or_invalid")


@pytest.mark.parametrize("field", ["sent_monotonic", "received_monotonic", "monotonic"])
def test_missing_GO_boundary_cannot_succeed(captured, field):
    value = deepcopy(captured)
    events = stream_events(value["result"])
    next(event for event in events if event.get("kind") == "GO_ACCEPTED").pop(field)
    failed(resign(value, events), "time_missing_or_invalid")


def test_request_after_normal_exit_and_preparation_order_are_rejected(captured):
    value = deepcopy(captured)
    events = stream_events(value["result"])
    parser = next(e for e in events if e.get("operation") == "parser_input")
    parser["record"]["at_monotonic"] = value["result"]["normal_exit_checked_monotonic"] + .001
    failed(resign(value, events), "raw_stage_order_or_completion")
    value = deepcopy(captured)
    value["result"]["controller_entered_monotonic"] = value["result"]["ready_checked_monotonic"] + 1
    failed(value, "absolute_stage_order")


def test_output_freeze_cannot_be_proven_by_boolean_alone(captured):
    value = deepcopy(captured)
    value["result"]["output_freeze_ms"] = value["result"]["work_elapsed_ms"] + 1
    failed(value, "output_freeze_or_startup_evidence")
    value = deepcopy(captured)
    value["result"]["output_frozen_before_work_deadline"] = False
    failed(value, "output_freeze_or_startup_evidence")
    value = deepcopy(captured)
    value["result"]["termination_ms"] = value["result"]["cleanup_ms"] + 1
    failed(value, "cleanup_stage_durations_exceed_shared_budget")


def test_legal_failed_capture_preserves_integrity_and_double_replay(captured, tmp_path):
    value = deepcopy(captured)
    target = value["result"]["task"]["target_monotonic"]
    timeline(value, go_delay=.8, completed=target + 1.8, finished=target + 1.9)
    value["result"].update(ok=False, request_timed_out=True,
        error="network_and_normal_exit:TimeoutError:prepared_output_freeze_after_work_deadline",
        output_frozen_before_work_deadline=False)
    resign(value)
    path = tmp_path / "synthetic-failed-evidence.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    anchor = sha256(path.read_bytes()).hexdigest()
    replays = []
    for index in range(2):
        output = tmp_path / f"replay-{index}.json"
        cli = subprocess.run([sys.executable, str(CLI), "--verify", str(path),
            "--expected-file-sha256", anchor, "--replay-output", str(output)], capture_output=True,
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert cli.returncode == 2, cli.stderr
        result = json.loads(output.read_bytes())
        assert result["evidence_integrity_verified"] and result["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_FAILED"
        assert sha256(path.read_bytes()).hexdigest() == anchor
        replays.append(output.read_bytes())
    assert replays[0] == replays[1]


def test_v1_integrity_audit_does_not_authorize_current_plan(captured):
    value = deepcopy(captured)
    old = contract.frozen_plan(audit_only_v1=True)
    value.update(schema="mootdx_protocol_diagnostic_evidence_v1", verifier=contract.AUDIT_ONLY_VERIFIER_VERSION, plan=old)
    result = value["result"]
    result["task"]["plan_hash"] = old["plan_hash"]
    result["task_hash"] = store.digest(result["task"])
    events = stream_events(result)
    for event in events:
        if "plan_hash" in event:
            event["plan_hash"] = old["plan_hash"]
        if "task_hash" in event:
            event["task_hash"] = result["task_hash"]
    verified = audit.verify(resign(value, events))
    assert verified["evidence_integrity_verified"] and verified["timing_contract_verified"]
    assert verified["legacy_verifier_audit_only"]
    assert verified["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_FAILED"
    with pytest.raises(ValueError, match="plan_mismatch"):
        contract.check_plan(old)
    with pytest.raises(ValueError, match="task_identity_invalid"):
        contract.check_task(result["task"])
