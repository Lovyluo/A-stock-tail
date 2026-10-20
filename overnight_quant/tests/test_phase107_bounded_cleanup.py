from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from overnight_quant.data import minute_capture_audit as audit
from overnight_quant.data import probe_worker_process as process
from test_phase107_minute_capture_audit import DAY, FIXTURE, assert_safe, run


def task():
    return {"parent_started_at": f"{DAY}T14:49:55+08:00", "parent_started_monotonic": time.perf_counter(),
        "io_deadline_monotonic": time.perf_counter() + 1.5, "trade_date": DAY, "wave": 0,
        "worker_version": audit.WORKER_VERSION, "source_version": audit.SOURCE_VERSION,
        "codes": list(audit.CODES), "endpoint": audit.ENDPOINT}


def save_result(directory, result):
    report = {key: value for key, value in result.items() if key not in ("stdout", "stderr", "payload")}
    (directory / "bounded-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


@pytest.fixture(autouse=True)
def result_diagnostics(monkeypatch, tmp_path):
    original = process.run_probe_worker_process
    def measured(*args, **kwargs):
        result = original(*args, **kwargs)
        save_result(tmp_path, result)
        return result
    monkeypatch.setattr(process, "run_probe_worker_process", measured)
    monkeypatch.setattr(audit, "run_probe_worker_process", measured)


def stop_pid(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100001, False, pid)
        if handle:
            try:
                kernel.TerminateProcess(handle, 1)
                assert kernel.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel.CloseHandle(handle)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.fixture
def owned(monkeypatch, tmp_path):
    native = process.subprocess.Popen
    children = []
    def start(*args, **kwargs):
        child = native(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(process.subprocess, "Popen", start)
    yield children
    # Independent, bounded teardown even when the injected cleanup deliberately fails.
    for path in tmp_path.rglob("*.pid"):
        stop_pid(int(path.read_text()))
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        assert child.poll() is not None
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None and not stream.closed:
                stream.close()


@pytest.mark.parametrize("failure", ["hang", "fail"])
def test_taskkill_hang_or_failure_cannot_enter_new_path(tmp_path, owned, failure):
    driver = FIXTURE.with_name("bounded_cleanup_driver_fixture.py")
    child = subprocess.Popen([sys.executable, str(driver), failure, str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=os.name != "nt")
    scope = process._ProcessScope(child)
    try:
        stdout, stderr = child.communicate(json.dumps(task()).encode(), timeout=6)
        assert child.returncode == 0, stderr
        outcome = json.loads(stdout)
        result = outcome["result"]
        save_result(tmp_path, result)
        assert not (tmp_path / "legacy-called").exists()
        assert outcome["driver_elapsed_ms"] < 2600  # harness guard, not production admission
        assert result["request_timed_out"] and result["cleanup_complete"]
        assert result["elapsed_ms"] < 2000
    finally:
        scope.terminate()
        scope.close()


def test_normal_timeout_reaps_root_and_descendant_and_preserves_output(tmp_path, owned):
    pid_path = tmp_path / "grandchild.pid"
    result = process.run_probe_worker_process(task(), 2000, bounded_cleanup_ms=500,
        retain_streams=True, worker_command=[sys.executable, str(FIXTURE), "block", str(pid_path)])
    assert result["error_code"] == process.WORKER_TIMEOUT_ERROR
    assert result["cleanup_complete"] and result["tree_empty_observed"]
    assert result["stdout_eof"] and result["stderr_eof"] and result["pipes_closed"]
    assert result["pid"] and result["returncode"] is not None
    assert any(item.get("kind") == "response" for item in audit.events_from_result(result))
    assert 0 < result["cleanup_ms"] <= 500 and result["elapsed_ms"] < 2000


def test_root_exited_but_descendant_keeps_both_pipes_open(tmp_path, owned):
    result = process.run_probe_worker_process(task(), 2000, bounded_cleanup_ms=500,
        retain_streams=True, worker_command=[sys.executable, str(FIXTURE), "orphan_pipe",
                                           str(tmp_path / "grandchild.pid")])
    assert result["returncode"] == 0 and not result["ok"]
    assert result["request_timed_out"] and result["cleanup_complete"]
    assert result["tree_empty_observed"] and result["stdout_eof"] and result["stderr_eof"]
    assert "synthetic_test_only" in result["stdout"] and "before root exit" in result["stderr"]
    assert result["elapsed_ms"] < 2000


@pytest.mark.parametrize("failure", ["not_exiting", "terminate_error"])
def test_cleanup_exhaustion_is_failure_with_pid_and_partial_stream(tmp_path, owned, monkeypatch, failure):
    def cannot_terminate(self):
        if failure == "terminate_error":
            raise OSError("synthetic terminate failure")
    monkeypatch.setattr(process._ProcessScope, "terminate", cannot_terminate)
    before = time.monotonic()
    result = process.run_probe_worker_process(task(), 2000, bounded_cleanup_ms=500,
        retain_streams=True, worker_command=[sys.executable, str(FIXTURE), "block",
                                           str(tmp_path / "grandchild.pid")])
    assert time.monotonic() - before < 2.6
    assert result["error_code"] == process.WORKER_CLEANUP_ERROR
    assert result["cleanup_complete"] is False and result["cleanup_budget_exhausted"]
    assert not result["ok"] and result["pid"] and result["returncode"] is None
    assert not result["tree_empty_observed"] and not result["stdout_eof"]
    assert any(item.get("kind") == "response" for item in audit.events_from_result(result))
    if failure == "terminate_error":
        assert any("synthetic terminate failure" in error for error in result["diagnostics"])


def test_termination_and_pipe_wait_share_one_cleanup_reserve(tmp_path, owned, monkeypatch):
    def slow_root_only(self):
        until = time.perf_counter() + .35
        while time.perf_counter() < until:
            time.sleep(max(0, until - time.perf_counter()))
        self.process.kill()  # Deliberately leave the descendant and inherited pipes alive.
    monkeypatch.setattr(process._ProcessScope, "terminate", slow_root_only)
    result = process.run_probe_worker_process(task(), 2000, bounded_cleanup_ms=500,
        retain_streams=True, worker_command=[sys.executable, str(FIXTURE), "block",
                                           str(tmp_path / "grandchild.pid")])
    assert not result["cleanup_complete"] and result["error_code"] == process.WORKER_CLEANUP_ERROR
    assert result["termination_ms"] >= 350
    assert result["drain_and_exit_ms"] < 220  # not another 500ms after termination
    assert 490 <= result["cleanup_ms"] < 650
    assert result["elapsed_ms"] < 2150  # measured late result still rejected; hard budget is unchanged


def test_cleanup_failure_stops_all_later_waves_and_persists_global_guard(tmp_path, owned, monkeypatch):
    monkeypatch.setattr(process._ProcessScope, "terminate", lambda self: None)
    evidence, path = run(tmp_path / "audit", "block", extra=(tmp_path / "grandchild.pid",))
    assert len(evidence["waves"]) == 1 and len(owned) == 1
    assert evidence["summary"]["cleanup_complete"] is False
    assert evidence["summary"]["residual_process_state"] == "UNCONFIRMED"
    assert "worker_cleanup_incomplete_no_new_worker" in evidence["capture_error"]
    guard = tmp_path / "audit/cleanup-incomplete.json"
    original = guard.read_bytes()
    assert json.loads(original)["pid"] == evidence["waves"][0]["result"]["pid"]
    with pytest.raises(ValueError, match="prior_worker_cleanup_unconfirmed"):
        run(tmp_path / "audit")
    with pytest.raises(ValueError, match="prior_worker_cleanup_unconfirmed"):
        audit.capture(audit.frozen_plan(), "2026-10-22", authorization={}, audit_root=tmp_path / "audit",
            _test={"test_only": True, "clock": lambda: datetime.fromisoformat("2026-10-22T14:49:55+08:00"),
                   "sleep": lambda _: None})
    assert len(owned) == 1 and guard.read_bytes() == original
    anchor = sha256(path.read_bytes()).hexdigest()
    assert audit.verify(evidence, anchor, anchor)["integrity_verified"]
    assert_safe(evidence)


def test_no_io_budget_means_no_process(owned):
    result = process.run_probe_worker_process(task(), 500, bounded_cleanup_ms=500, retain_streams=True)
    assert not owned and not result["ok"] and result["request_timed_out"]
    assert result["cleanup_complete"] and result["pid"] is None
