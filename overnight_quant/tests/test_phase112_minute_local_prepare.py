"""Explicitly synthetic: real subprocesses, installed parsers, zero source requests."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from overnight_quant.data import minute_capture_audit as legacy
from overnight_quant.data import minute_local_prepare_contract as contract
from overnight_quant.data import minute_local_prepare_process as process

FIXTURE = Path(__file__).parent / "fixtures/minute_local_prepare_fixture.py"


def task(tmp_path):
    config = tmp_path / "native-config.json"
    raw = json.dumps({"SERVER": {"HQ": [["test-only", "59.36.5.11", 7709]]},
                      "BESTIP": {"HQ": ["59.36.5.11", 7709]}}).encode()
    config.write_bytes(raw)
    start = time.perf_counter()
    target = start + 4.99
    return {"worker_version": contract.WORKER_VERSION, "plan_hash": contract.frozen_plan()["plan_hash"],
        "batch_id": "synthetic-only-batch", "code_sha": "a" * 40, "wave": 0,
        "codes": list(legacy.CODES), "endpoint": legacy.ENDPOINT, "source_version": legacy.SOURCE_VERSION,
        "dependencies": contract.DEPENDENCIES, "config": {"path": str(config), "sha256": sha256(raw).hexdigest()},
        "worker_sha256": contract.worker_file_sha256(), "parent_pid": os.getpid(), "test_only": True,
        "trade_date": "2026-10-22", "scheduled_at": "2026-10-22T14:49:55+08:00",
        "preparation_started_monotonic": start, "target_monotonic": target,
        "preparation_work_deadline": min(start + 3.5, target - 1.5),
        "preparation_hard_deadline": min(start + 4, target - 1),
        "io_deadline_monotonic": target + 1.5, "hard_deadline_monotonic": target + 2}


def run(tmp_path, mode="complete", **kwargs):
    result = process.run_prepared_wave(task(tmp_path), worker_command=[sys.executable, str(FIXTURE), mode, str(tmp_path)], **kwargs)
    (tmp_path / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert result["cleanup_complete"], result
    assert result["tree_empty_observed"] and result["stdout_eof"] and result["stderr_eof"], result
    return result


def events(result):
    return [json.loads(line) for line in result["stdout"].splitlines()]


def test_real_prepare_GO_installed_connect_handshake_five_reads_and_residence(tmp_path):
    result = run(tmp_path)
    assert result["ok"], result
    values = events(result)
    ready = next(v for v in values if v.get("kind") == "READY_LOCAL_ONLY")
    go = next(v for v in values if v.get("kind") == "GO_ACCEPTED")
    connected = next(v for v in values if v.get("kind") == "fixture_connect")
    assert ready["network_attempts_before_go"] == go["network_attempts_before_go"] == 0
    assert ready["ready_monotonic"] < result["task"]["preparation_work_deadline"]
    assert connected["monotonic"] >= go["monotonic"] >= result["task"]["target_monotonic"]
    assert next(v for v in values if v.get("kind") == "fixture_counts")["counts"] == {
        "external_network_attempts": 0, "connect": 1, "handshake": 3, "bars": 5}
    assert len([v for v in values if v.get("kind") == "response"]) == 5
    assert result["preparation_elapsed_ms"] < 3500 and result["total_residence_ms"] > 4000
    assert result["work_elapsed_ms"] < 1500 and result["wave_elapsed_ms"] < 2000


@pytest.mark.parametrize("controls,reason", [({"go_offset_ms": -100}, "before_target"),
    ({"duplicate_go": True}, "duplicate_GO"), ({"wrong_go_identity": True}, "identity_or_kind")])
def test_GO_invalid_never_connects(tmp_path, controls, reason):
    result = run(tmp_path, _test=controls)
    assert not result["ok"] and reason in result["error"]
    assert not (tmp_path / "connected").exists()


@pytest.mark.parametrize("mode,reason", [("wrong_ready", "ready_local_only_identity"),
    ("swallowed_network", "boundary_attempt_recorded"), ("ready_timeout", "READY_timeout")])
def test_prepare_rejects_without_connect(tmp_path, mode, reason):
    result = run(tmp_path, mode)
    assert not result["ok"] and reason in result["error"]
    assert not (tmp_path / "connected").exists()
    assert result["go_sent_monotonic"] is None


def test_late_GO_keeps_original_absolute_deadline(tmp_path):
    result = run(tmp_path, "work_timeout", _test={"go_offset_ms": 600})
    assert not result["ok"] and result["request_timed_out"]
    assert result["work_elapsed_ms"] < 1200
    assert result["finished_monotonic"] < result["task"]["hard_deadline_monotonic"]
    assert result["cleanup_ms"] < 500


def test_missing_GO_EOF_cannot_connect_or_extend_absolute_deadline(tmp_path):
    result = run(tmp_path, _test={"keep_input_open": True})
    assert not result["ok"] and result["cleanup_ms"] < 500
    assert result["finished_monotonic"] < result["task"]["hard_deadline_monotonic"]
    assert not (tmp_path / "connected").exists()


def test_buffered_early_GO_is_rejected_even_when_read_after_target(tmp_path):
    value = task(tmp_path)
    early = contract.go_message(value, value["target_monotonic"] - .1)
    with pytest.raises(ValueError, match="before_target"):
        contract.check_go(early, value, value["target_monotonic"] + .1)


@pytest.mark.parametrize("alter", ["nonfinite_ready", "boolean_counter"])
def test_ready_precision_and_counter_types_are_not_guessed(tmp_path, alter):
    value = task(tmp_path)
    ready = {"kind": "READY_LOCAL_ONLY", "task_hash": legacy.digest(value), "worker_version": contract.WORKER_VERSION,
        "pid": 123, "dependencies": contract.DEPENDENCIES, "config": value["config"], "code_sha": value["code_sha"],
        "wave": value["wave"], "batch_id": value["batch_id"], "plan_hash": value["plan_hash"],
        "network_attempts_before_go": 0, "ready_monotonic": value["preparation_started_monotonic"] + .5}
    ready["ready_monotonic" if alter == "nonfinite_ready" else "network_attempts_before_go"] = (
        float("nan") if alter == "nonfinite_ready" else False)
    with pytest.raises(ValueError, match="ready_local_only"):
        contract.check_ready(ready, value, 123)


@pytest.mark.parametrize("mode,delay", [("complete", 1.5), ("work_timeout", 5.2)])
def test_cancel_ready_wait_or_work_reaps(tmp_path, mode, delay):
    cancel = threading.Event()
    timer = threading.Timer(delay, cancel.set)
    timer.start()
    try:
        result = run(tmp_path, mode, cancel_event=cancel)
    finally:
        timer.cancel()
        timer.join(timeout=2)
    assert not result["ok"] and "parent_cancelled" in result["error"]
    assert (tmp_path / "connected").exists() is (mode == "work_timeout")


def test_descendant_holding_pipes_is_killed_within_shared_cleanup_budget(tmp_path):
    result = run(tmp_path, "orphan_pipe")
    assert not result["ok"] and result["request_timed_out"]
    assert result["worker_terminated"] and result["cleanup_ms"] < 500
    assert (tmp_path / "descendant.pid").exists()


def test_old_plan_and_reset_deadline_rejected_without_process(tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("No child may start")
    monkeypatch.setattr(process.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="local_prepare_frozen_plan"):
        contract.check_plan(legacy.frozen_plan())
    value = task(tmp_path)
    value["io_deadline_monotonic"] += .001
    with pytest.raises(ValueError, match="deadline_contract"):
        process.run_prepared_wave(value)


def test_real_production_worker_rejects_synthetic_scope_before_vendor_imports(tmp_path):
    result = process.run_prepared_wave(task(tmp_path))
    assert not result["ok"] and "test_scope_mismatch" in result["error"]
    assert result["cleanup_complete"] and result["go_sent_monotonic"] is None
    assert not any(v.get("stage") == "local_dependency_imports" for v in events(result))


def test_delayed_job_assignment_still_owns_venv_interpreter_before_READY(tmp_path, monkeypatch):
    original = process._ProcessScope
    def delayed(*a, **k):
        time.sleep(.35)
        return original(*a, **k)
    monkeypatch.setattr(process, "_ProcessScope", delayed)
    result = run(tmp_path)
    assert result["ok"] and result["ready_pid_in_owned_job"]
    assert result["process_start_policy"] == "suspended_until_owned_job_then_resume"
    assert result["process_startup_and_scope_ms"] >= 350


def test_config_hash_failure_before_READY_has_zero_connect(tmp_path):
    value = task(tmp_path)
    value["config"]["sha256"] = "0" * 64
    result = process.run_prepared_wave(value, worker_command=[sys.executable, str(FIXTURE), "complete", str(tmp_path)])
    assert not result["ok"] and "config_external_sha256_mismatch" in result["error"]
    assert result["cleanup_complete"] and result["go_sent_monotonic"] is None
    assert not (tmp_path / "connected").exists()


def test_parent_death_during_bars_kills_owned_interpreter_not_just_launcher(tmp_path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    driver = FIXTURE.with_name("minute_local_prepare_parent_fixture.py")
    parent = subprocess.Popen([sys.executable, str(driver), str(tmp_path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # No outer test job: it would mask a broken kill-on-owner-close setting.
    # Hold the actual interpreter handle and kill its exact owner, not a venv redirector.
    child_handle = owner_handle = None
    try:
        deadline = time.perf_counter() + 12
        while not (tmp_path / "bars-blocked").exists() and time.perf_counter() < deadline:
            time.sleep(.005)
        assert (tmp_path / "bars-blocked").exists()
        pid = int((tmp_path / "worker.pid").read_text())
        child_handle = kernel.OpenProcess(0x100001 | 0x1000, False, pid)
        assert child_handle
        # The isolated driver records its real controller PID before spawn.
        owner = int((tmp_path / "owner.pid").read_text())
        owner_handle = kernel.OpenProcess(0x100001, False, owner)
        assert owner_handle
        assert kernel.TerminateProcess(owner_handle, 23)
        assert kernel.WaitForSingleObject(owner_handle, 2000) == 0
        assert kernel.WaitForSingleObject(child_handle, 2000) == 0
        parent.communicate(timeout=5)
        assert (tmp_path / "intent.json").exists()
        assert legacy.pending_preparation_receipts(tmp_path) == [str(tmp_path / "2026-10-22" / "wave-0.started.json")]
    finally:
        for handle in (child_handle, owner_handle):
            if handle:
                if kernel.WaitForSingleObject(handle, 0) != 0:
                    kernel.TerminateProcess(handle, 99)
                    kernel.WaitForSingleObject(handle, 5000)
                kernel.CloseHandle(handle)
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=5)
