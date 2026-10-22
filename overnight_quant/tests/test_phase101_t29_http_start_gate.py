"""Real isolated PowerShell/HttpClient calls to an in-memory handler, no internet."""
from datetime import datetime, timedelta
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import MarketDotNetHttpClientTransport, _run_dotnet_http_worker
from overnight_quant.data.t25_bounded_executor import AuditRequest, BoundedAuditExecutor

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "overnight_quant/tests/fixtures/t29_http_client_worker.ps1"
COMMAND = [shutil.which("pwsh.exe") or "powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)]


def test_python_and_dotnet_monotonic_counters_share_windows_epoch():
    before = time.perf_counter()
    value = subprocess.check_output([COMMAND[0], "-NoProfile", "-Command",
        "if(-not [Diagnostics.Stopwatch]::IsHighResolution){throw 'high_resolution_counter_required'}; "
        "([double][Diagnostics.Stopwatch]::GetTimestamp()/[Diagnostics.Stopwatch]::Frequency).ToString('R',[Globalization.CultureInfo]::InvariantCulture)"], text=True)
    after = time.perf_counter()
    assert before <= float(value.strip()) <= after


def _spawn(tmp, key, *, _processes=None, **scenario):
    marker = tmp / f"{key}.call"
    payload = {"test_only": True, "url": "https://push2.eastmoney.com/test-only",
               "http_start_state_directory": str(tmp / "state"),
               "test_response": {"call_marker": str(marker),
                                 "diagnostic_path": str(tmp / f"{key}.diagnostic.json"), **scenario}}
    started = time.perf_counter()
    p = subprocess.Popen(COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
    if _processes is not None:
        _processes.append((p, tmp, key, started))
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    p.stdin = None
    return p, marker


@contextmanager
def _client_process_scope(processes):
    try:
        yield
    finally:
        # A failed ready barrier must not leave clients waiting for release in
        # later tests. These in-memory handlers never create descendant workers.
        deadline = time.perf_counter() + 5
        failures = []
        for process, directory, key, started in processes:
            was_alive = process.poll() is None
            if was_alive:
                process.kill()
            try:
                stdout, stderr = process.communicate(timeout=max(.001, deadline - time.perf_counter()))
            except subprocess.TimeoutExpired:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
                failures.append({"request_id": key, "pid": process.pid,
                                 "stage": "fixture_cleanup_incomplete", "returncode": process.poll()})
                continue
            phase_path = directory / f"{key}.diagnostic.json"
            try:
                phases = json.loads(phase_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                # Termination can interrupt a checkpoint write. A diagnostic
                # read failure must not prevent the other clients being reaped.
                phases = {"diagnostic_read_error": f"{type(exc).__name__}:{exc}"}
            details = {"test_only": True, "request_id": key, "pid": process.pid,
                       "elapsed_ms": (time.perf_counter() - started) * 1000,
                       "terminated_at_teardown": was_alive, "returncode": process.returncode,
                       "phases": phases, "stdout": stdout, "stderr": stderr}
            (directory / f"{key}.process.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
            if was_alive or process.returncode != 0:
                # Only failures print fixture metadata, never market responses.
                print("PHASE101_CLIENT_FAILURE " + json.dumps(details, ensure_ascii=True))
        assert not failures, failures


@pytest.fixture(autouse=True)
def isolated_client_processes(monkeypatch):
    original = _spawn
    processes = []

    def tracked_spawn(tmp, key, **scenario):
        return original(tmp, key, _processes=processes, **scenario)

    monkeypatch.setattr(sys.modules[__name__], "_spawn", tracked_spawn)
    with _client_process_scope(processes):
        yield original


def _finish(process):
    stdout, stderr = process.communicate(timeout=20)
    assert stdout, stderr
    return json.loads(stdout)


def _barrier(tmp, count=2):
    end = time.perf_counter() + 15
    while len(list(tmp.glob("*.ready"))) < count and time.perf_counter() < end:
        time.sleep(.01)
    diagnostics = {path.name: json.loads(path.read_text(encoding="utf-8-sig"))
                   for path in tmp.glob("*.diagnostic.json")}
    assert len(list(tmp.glob("*.ready"))) == count, diagnostics
    (tmp / "release").touch()


def test_slow_first_cold_start_fast_second_real_http_calls_stay_500ms_apart(tmp_path):
    processes = [_spawn(tmp_path, str(i), cold_ms=550 if i == 0 else 50,
                        ready_marker=str(tmp_path / f"{i}.ready"), release_marker=str(tmp_path / "release"))
                 for i in range(2)]
    _barrier(tmp_path)
    results = [_finish(p) for p, _ in processes]
    assert all(r["ok"] for r in results), results
    starts = sorted(float(marker.read_text()) for _, marker in processes)
    assert starts[1] - starts[0] >= .5
    assert all(r["response"]["client_read_elapsed_ms"] < 2000 for r in results)


def test_three_competing_clients_do_not_burst(tmp_path):
    processes = [_spawn(tmp_path, str(i), ready_marker=str(tmp_path / f"{i}.ready"),
                        release_marker=str(tmp_path / "release")) for i in range(3)]
    _barrier(tmp_path, 3)
    results = [_finish(p) for p, _ in processes]
    assert all(r["ok"] for r in results), results
    starts = sorted(float(marker.read_text()) for _, marker in processes)
    assert all(b - a >= .5 for a, b in zip(starts, starts[1:]))


@pytest.mark.parametrize("corrupt_checkpoint", [False, True])
def test_failed_ready_barrier_scope_reaps_real_client_and_retains_diagnostics(
        tmp_path, isolated_client_processes, corrupt_checkpoint):
    processes = []
    # Use the original implementation so this explicitly exercises nested
    # failure cleanup, independently of the enclosing autouse fixture.
    with pytest.raises(RuntimeError, match="synthetic_barrier_failure"):
        with _client_process_scope(processes):
            process, marker = isolated_client_processes(
                tmp_path, "unreleased", _processes=processes,
                ready_marker=str(tmp_path / "unreleased.ready"), release_marker=str(tmp_path / "never-release"))
            deadline = time.perf_counter() + 15
            while not (tmp_path / "unreleased.ready").exists() and time.perf_counter() < deadline:
                time.sleep(.01)
            assert (tmp_path / "unreleased.ready").exists()
            if corrupt_checkpoint:
                (tmp_path / "unreleased.diagnostic.json").write_text("{interrupted", encoding="utf-8")
            raise RuntimeError("synthetic_barrier_failure")
    assert process.poll() is not None and not marker.exists()
    assert process.stdout.closed and process.stderr.closed
    saved = json.loads((tmp_path / "unreleased.process.json").read_bytes())
    assert saved["terminated_at_teardown"] is True
    if corrupt_checkpoint:
        assert saved["phases"]["diagnostic_read_error"].startswith("JSONDecodeError:")
    else:
        assert saved["phases"]["phases"][-1]["stage"] == "fixture_initialized"


def test_wait_exhausts_budget_zero_http_calls_then_no_burst(tmp_path):
    p, first = _spawn(tmp_path, "first", ready_marker=str(tmp_path / "first.ready"), release_marker=str(tmp_path / "release"))
    q, second = _spawn(tmp_path, "second", cold_ms=20, budget_ms=200,
                       ready_marker=str(tmp_path / "second.ready"), release_marker=str(tmp_path / "release"))
    _barrier(tmp_path)
    assert _finish(p)["ok"]
    result = _finish(q)
    assert not result["ok"] and not second.exists(), result
    assert "budget_exhausted" in result["error_code"]
    r, third = _spawn(tmp_path, "third")
    assert _finish(r)["ok"]
    assert float(third.read_text()) - float(first.read_text()) >= .5


def test_deadline_timer_cancellation_before_gate_keeps_raw_error_and_zero_http(tmp_path):
    p, marker = _spawn(tmp_path, "timer", budget_ms=500, wait_for_deadline_timer=True)
    result = _finish(p)
    assert not result["ok"] and not marker.exists() and not result["http_call_made"]
    assert result["error_code"] == "http_start_budget_exhausted"
    assert result["deadline_timer_cancelled"] is True
    assert result["raw_error_type"] == "System.OperationCanceledException"
    assert result["raw_error_message"]


def test_cancelled_coordination_lock_wait_makes_zero_calls(tmp_path):
    p, first = _spawn(tmp_path, "holder", block_ms=800,
                       ready_marker=str(tmp_path / "holder.ready"), release_marker=str(tmp_path / "release"))
    q, second = _spawn(tmp_path, "waiter", cold_ms=50, budget_ms=200,
                       ready_marker=str(tmp_path / "waiter.ready"), release_marker=str(tmp_path / "release"))
    _barrier(tmp_path)
    assert _finish(p)["ok"]
    result = _finish(q)
    assert not result["ok"] and not second.exists()


def test_body_fully_read_before_completion_timestamp(tmp_path):
    p, _ = _spawn(tmp_path, "body", body_ms=300)
    result = _finish(p)
    assert result["ok"], result
    assert result["response"]["client_read_elapsed_ms"] >= 300
    start = datetime.fromisoformat(result["response"]["request_started_at"])
    end = datetime.fromisoformat(result["response"]["request_completed_at"])
    assert (end - start).total_seconds() >= .3


@pytest.mark.parametrize("delta", [-300000, -1000, 1000, 300000])
def test_wall_clock_jump_never_upgrades_to_success(tmp_path, delta):
    p, marker = _spawn(tmp_path, "jump", clock_delta_ms=delta)
    result = _finish(p)
    assert marker.exists() and not result["ok"]
    assert "http_wall_clock_order_invalid" in result["error_code"]


def test_body_delay_over_budget_fails_without_retry(tmp_path):
    # Use the real request limit so cold setup does not replace the body-timeout path.
    p, marker = _spawn(tmp_path, "body", body_ms=3000, budget_ms=2000)
    result = _finish(p)
    assert marker.exists() and not result["ok"]
    assert p.returncode == 2 and result["http_call_made"]
    assert result["deadline_timer_cancelled"] is True
    assert result["raw_error_type"] in {"System.OperationCanceledException", "System.Threading.Tasks.TaskCanceledException"}
    assert len(list(tmp_path.glob("*.call"))) == 1


def test_killed_client_pending_marker_prevents_subsequent_call(tmp_path):
    p, marker = _spawn(tmp_path, "blocked", block_ms=4000)
    try:
        end = time.perf_counter() + 15
        while not marker.exists() and time.perf_counter() < end:
            time.sleep(.01)
        assert marker.exists()
        p.kill()
        p.communicate(timeout=5)
        q, second = _spawn(tmp_path, "after")
        result = _finish(q)
        assert not result["ok"] and not second.exists()
        assert "coordination_uncertain" in result["error_code"]
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate(timeout=5)


def test_torn_post_call_journal_never_allows_a_new_http_call(tmp_path):
    p, marker = _spawn(tmp_path, "first")
    assert _finish(p)["ok"]
    state = next((tmp_path / "state").glob("*.json"))
    with state.open("ab") as stream:
        stream.write(b'{"pending":false')
    p, marker = _spawn(tmp_path, "torn")
    result = _finish(p)
    assert not result["ok"] and not marker.exists()
    assert "coordination_uncertain" in result["error_code"]


def test_executor_wall_clock_jump_cannot_release_requests_early(tmp_path, monkeypatch):
    import overnight_quant.data.t25_bounded_executor as module
    origin = datetime.now(CN_TZ)
    class JumpClock(datetime):
        calls = 0
        @classmethod
        def now(cls, tz):
            cls.calls += 1
            return datetime.now(tz) + (timedelta(hours=1) if cls.calls > 1 else timedelta())
    monkeypatch.setattr(module, "datetime", JumpClock)
    job = AuditRequest("released", "https://push2.eastmoney.com/test-only", {}, release_at=origin + timedelta(seconds=.8))
    result = BoundedAuditExecutor()._run_for_test([job], deadline=origin + timedelta(seconds=8),
        command=[__import__("sys").executable, "-m", "overnight_quant.data.t25_local_response_worker"],
        payloads={"released": {}}, lock_path=tmp_path / "executor.lock")
    assert result["results"][0]["request_started_offset_ms"] >= 750
    assert not result["execution_ok"]
    assert result["data_ready"] is False


def test_real_executor_cold_start_and_pipe_read_are_in_2000ms_budget(tmp_path):
    jobs = [AuditRequest("blocked", "https://push2.eastmoney.com/test-only", {}),
            AuditRequest("not-dispatched", "https://push2.eastmoney.com/test-only", {}, ("blocked",))]
    result = BoundedAuditExecutor()._run_for_test(jobs, deadline=datetime.now(CN_TZ) + timedelta(seconds=15),
        command=COMMAND, payloads={"blocked": {"call_marker": str(tmp_path / "blocked.call"), "body_ms": 3000},
                                   "not-dispatched": {"call_marker": str(tmp_path / "never.call")}},
        lock_path=tmp_path / "executor.lock")
    assert not result["execution_ok"]
    assert result["dispatched_count"] == 1
    assert result["results"][0]["worker_reaped"]
    assert result["residual_worker_count"] == 0
    assert result["retry_count"] == result["fallback_count"] == 0
    assert not (tmp_path / "never.call").exists()
    assert result["qualification"] == "0/3"
    assert result["data_ready"] is result["hard_gate_authorized"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_standalone_runner_passes_startup_inclusive_monotonic_deadline(tmp_path):
    payload = {"test_only": True, "url": "https://push2.eastmoney.com/test-only",
               "http_start_state_directory": str(tmp_path / "state"),
               "test_response": {"call_marker": str(tmp_path / "call")}}
    result = _run_dotnet_http_worker(payload, 2, COMMAND)
    assert result["ok"], result
    assert result["process_elapsed_ms"] <= 2000


def test_production_worker_expired_startup_budget_never_calls_http(tmp_path):
    command = MarketDotNetHttpClientTransport().worker_command
    payload = {"method": "GET", "url": "https://push2.eastmoney.com/api/qt/stock/get",
               "params": [["secid", "90.BK0474"]], "headers": {}, "allowed_hosts": ["push2.eastmoney.com"],
               "transport_version": "eastmoney_dotnet_httpclient_v1", "max_response_bytes": 5000000,
               "request_deadline_ms": 2000, "deadline_monotonic_seconds": time.perf_counter() - 1,
               "http_start_state_directory": str(tmp_path / "state")}
    value = subprocess.run(command, input=json.dumps(payload), capture_output=True,
                           encoding="utf-8", timeout=10)
    result = json.loads(value.stdout)
    assert not result["ok"] and result["error_code"] == "MARKET_SOURCE_TIMEOUT"
    assert result["request_started_at"] == ""
    assert not (tmp_path / "state").exists()


def test_historical_candidate_failure_integrity_does_not_grant_qualification():
    from overnight_quant.data.eastmoney_industry_strength_candidate import parse_captured_board_strength, board_strength_request
    from overnight_quant.tests.test_phase100_t27_strength_candidate import _fixture
    request, capture = _fixture()
    raw = __import__("base64").b64decode(capture["content_base64"])
    capture["request_completed_at"] = "2026-10-09T14:36:59+08:00"
    result = parse_captured_board_strength(capture, request=board_strength_request("BK1239"), trade_date="2026-10-09",
        feature_cutoff="2026-10-09T14:40:00+08:00", collection_deadline="2026-10-09T14:41:05+08:00",
        expected_raw_sha256=hashlib.sha256(raw).hexdigest(), test_only=True)
    assert result["status"] == "INDUSTRY_STRENGTH_CANDIDATE_REJECTED"
    assert result["records"] == result["candidates"] == result["tickets"] == result["orders"] == []
    assert result["qualification"] == "0/3" and not result["source_qualification_verified"]
