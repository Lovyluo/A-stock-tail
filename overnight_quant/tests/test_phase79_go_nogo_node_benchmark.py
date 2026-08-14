from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.mootdx_node_benchmark import (
    BENCHMARK_EVIDENCE_SCHEMA_V1,
    BENCHMARK_EVIDENCE_SCHEMA_V2,
    FORMAL_MINUTE_OFFSET,
    FORMAL_VALIDATION_CODES,
    RECOMMENDATION_MAX_ELAPSED_MS,
    compute_benchmark_evidence_hash,
    run_mootdx_node_benchmark,
    verify_benchmark_evidence,
    write_benchmark_json_atomic,
)
from overnight_quant.data.snapshot_store import ProviderBatch
from overnight_quant.data import minute_probe_worker
from overnight_quant.data.minute_probe_sources import (
    MootdxMinuteProbeCollectors,
)


ROOT = Path(__file__).resolve().parents[2]
GO_NOGO_SCRIPT = (
    ROOT / "overnight_quant" / "scripts" / "run_minute_probe_go_nogo.ps1"
)
GO_NOGO_HARNESS = (
    ROOT / "overnight_quant" / "tests" / "go_nogo_behavior_harness.ps1"
)
ENDPOINT_SCRIPT = (
    ROOT / "overnight_quant" / "scripts" / "run_probe_endpoint_preflight.py"
)
CODES = ["000001", "000333", "600000", "600519", "601318"]


def test_node_benchmark_screens_all_candidates_and_recommends_only_fast_node():
    endpoints = [
        _endpoint("fast", "10.0.0.1"),
        _endpoint("slow", "10.0.0.2"),
        _endpoint("partial", "10.0.0.3"),
        _endpoint("timeout", "10.0.0.4"),
    ]
    calls = []

    def worker(task, deadline_ms):
        calls.append(deepcopy(task))
        endpoint_id = task["endpoint"]["id"]
        if endpoint_id == "timeout":
            return _timeout_result()
        covered = CODES if endpoint_id != "partial" else CODES[:4]
        elapsed = 900 if endpoint_id == "fast" else 1600
        return _worker_result(task, covered=covered, elapsed_ms=elapsed)

    result = run_mootdx_node_benchmark(
        CODES,
        trade_date="2026-08-14",
        endpoint_candidates=endpoints,
        qualification_rounds=5,
        worker_runner=worker,
        compare_offsets=False,
        clock=lambda: datetime(2026, 8, 14, 14, 20, tzinfo=CN_TZ),
    )

    assert result["screened_endpoint_count"] == 4
    assert len(result["screening"]) == 4
    assert len(result["qualification"]) == 2
    assert all(row["round_count"] == 5 for row in result["qualification"])
    assert result["recommended_endpoint"]["id"] == "fast"
    assert result["screening_survivor_count"] == 2
    assert result["automatic_configuration_change"] is False
    assert result["formal_minute_offset"] == 800
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []
    screened_ids = [
        task["endpoint"]["id"]
        for task in calls[: len(endpoints)]
    ]
    assert screened_ids == [row["id"] for row in endpoints]


def test_node_with_any_timeout_or_max_latency_over_limit_is_not_recommended():
    endpoint = _endpoint("unstable", "10.0.0.1")
    calls = 0

    def worker(task, deadline_ms):
        nonlocal calls
        calls += 1
        if calls == 3:
            return _timeout_result()
        return _worker_result(task, elapsed_ms=1501)

    result = run_mootdx_node_benchmark(
        CODES,
        trade_date="2026-08-14",
        endpoint_candidates=[endpoint],
        qualification_rounds=5,
        worker_runner=worker,
        compare_offsets=False,
    )

    summary = result["qualification"][0]
    assert summary["timeout_count"] == 1
    assert summary["recommendation_eligible"] is False
    assert result["recommended_endpoint"] is None


def test_no_screening_survivor_stops_before_qualification_and_offset():
    result = run_mootdx_node_benchmark(
        CODES,
        trade_date="2026-08-14",
        endpoint_candidates=[_endpoint("partial", "10.0.0.3")],
        worker_runner=lambda task, deadline: _worker_result(
            task,
            covered=CODES[:4],
        ),
    )

    assert result["status"] == "NO_SCREENING_SURVIVOR"
    assert result["screening_survivor_count"] == 0
    assert result["qualification"] == []
    assert result["offset_comparison"]["status"] == "NOT_RUN"
    assert result["recommended_endpoint"] is None
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def test_diagnostic_scope_never_recommends_or_runs_formal_offset_comparison():
    result = run_mootdx_node_benchmark(
        ["000001"],
        trade_date="2026-08-14",
        endpoint_candidates=[_endpoint("fast", "10.0.0.1")],
        diagnostic_only=True,
        worker_runner=lambda task, deadline: _worker_result(task),
    )

    assert result["diagnostic_only"] is True
    assert result["status"] == "DIAGNOSTIC_BENCHMARK_COMPLETED"
    assert result["recommended_endpoint"] is None
    assert result["offset_comparison"]["status"] == "NOT_RUN"


def test_non_formal_scope_requires_diagnostic_mode():
    with pytest.raises(ValueError, match="formal_codes_mismatch"):
        run_mootdx_node_benchmark(
            ["000001"],
            endpoint_candidates=[],
        )


def test_recommendation_limit_cannot_be_relaxed_above_1500ms():
    assert RECOMMENDATION_MAX_ELAPSED_MS == 1500
    with pytest.raises(ValueError, match="exceeds_1500ms"):
        run_mootdx_node_benchmark(
            CODES,
            endpoint_candidates=[],
            recommendation_max_ms=1501,
        )


def test_offset_comparison_reports_market_content_equivalence():
    endpoint = _endpoint("fast", "10.0.0.1")

    result = run_mootdx_node_benchmark(
        CODES,
        trade_date="2026-08-14",
        endpoint_candidates=[endpoint],
        qualification_rounds=5,
        worker_runner=lambda task, deadline: _worker_result(
            task,
            elapsed_ms=800,
            canonical_hash="c" * 64,
        ),
    )

    comparison = result["offset_comparison"]
    assert comparison["status"] == "OFFSET_COMPARISON_COMPLETED"
    assert comparison["same_record_counts"] is True
    assert comparison["same_1450_ohlcv_signatures"] is True
    assert comparison["same_canonical_hashes"] is True
    assert comparison["formal_default_unchanged"] is True
    assert sorted(comparison["runs"]) == ["320", "800"]


def test_offset_hash_difference_is_audit_failure_without_changing_default():
    endpoint = _endpoint("fast", "10.0.0.1")

    def worker(task, deadline_ms):
        offset = int(task.get("minute_offset") or FORMAL_MINUTE_OFFSET)
        value = "a" * 64 if offset == 800 else "b" * 64
        return _worker_result(task, elapsed_ms=700, canonical_hash=value)

    result = run_mootdx_node_benchmark(
        CODES,
        endpoint_candidates=[endpoint],
        qualification_rounds=5,
        worker_runner=worker,
    )

    assert result["formal_minute_offset"] == 800
    assert result["offset_comparison"]["same_canonical_hashes"] is False
    assert result["automatic_configuration_change"] is False


@pytest.mark.parametrize(
    ("signatures", "presence", "reason_fragment"),
    [
        ({}, {code: False for code in CODES}, "signature_codes_mismatch"),
        (
            {
                code: {
                    "ohlcv_hash": "d" * 64,
                    "event_time": "2026-08-14T14:50:00+08:00",
                }
                for code in CODES[:-1]
            },
            {code: code != CODES[-1] for code in CODES},
            f"1450_presence_missing:{CODES[-1]}",
        ),
        (
            {
                code: {
                    "ohlcv_hash": "d" * 64,
                    "event_time": "2026-08-14T14:49:00+08:00",
                }
                for code in CODES
            },
            {code: True for code in CODES},
            "signature_time_invalid",
        ),
    ],
)
def test_offset_comparison_fails_closed_for_incomplete_1450_contract(
    signatures,
    presence,
    reason_fragment,
):
    result = run_mootdx_node_benchmark(
        CODES,
        trade_date="2026-08-14",
        endpoint_candidates=[_endpoint("fast", "10.0.0.1")],
        worker_runner=lambda task, deadline: _worker_result(
            task,
            signatures=signatures,
            presence_by_code=presence,
        ),
    )

    comparison = result["offset_comparison"]
    assert comparison["status"] == "OFFSET_COMPARISON_INCOMPLETE"
    assert any(
        reason_fragment in reason
        for reason in comparison["incomplete_reasons"]
    )
    assert comparison["same_record_counts"] is False
    assert comparison["same_1450_ohlcv_signatures"] is False
    assert comparison["same_canonical_hashes"] is False


def test_benchmark_next_day_keeps_real_observation_time_and_data_trade_date():
    calls = []
    actual_time = datetime(2026, 8, 15, 9, 12, 30, tzinfo=CN_TZ)

    def worker(task, deadline_ms):
        calls.append(deepcopy(task))
        return _worker_result(task)

    result = run_mootdx_node_benchmark(
        CODES,
        trade_date="2026-08-14",
        endpoint_candidates=[_endpoint("fast", "10.0.0.1")],
        worker_runner=worker,
        compare_offsets=False,
        clock=lambda: actual_time,
    )

    assert result["data_trade_date"] == "2026-08-14"
    assert result["benchmark_started_at"].startswith("2026-08-15T09:12:30")
    assert result["benchmark_completed_at"].startswith("2026-08-15T09:12:30")
    assert result["observed_at"] == result["benchmark_started_at"]
    assert calls
    assert all(task["data_trade_date"] == "2026-08-14" for task in calls)
    assert all(task["observed_at"].startswith("2026-08-15") for task in calls)
    assert all(task["operation"] == "benchmark_preflight" for task in calls)
    assert result["screening"][0]["worker_request_started_at"]
    assert result["screening"][0]["worker_request_completed_at"]


def test_benchmark_collector_filters_prior_trade_date_without_backdating_observed():
    class Frame:
        empty = False

        def to_dict(self, orient):
            assert orient == "records"
            return [
                {
                    "datetime": "2026-08-14T14:50:00+08:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "vol": 100,
                    "amount": 1050,
                },
                {
                    "datetime": "2026-08-15T14:50:00+08:00",
                    "open": 20,
                    "high": 21,
                    "low": 19,
                    "close": 20.5,
                    "vol": 200,
                    "amount": 4100,
                },
            ]

    class Client:
        def bars(self, **kwargs):
            return Frame()

        def close(self):
            return None

    completed = datetime(2026, 8, 15, 9, 12, 31, tzinfo=CN_TZ)
    collector = MootdxMinuteProbeCollectors(
        ["000001"],
        client_factory=Client,
        clock=lambda: completed,
        benchmark_data_trade_date="2026-08-14",
    )
    observed = datetime(2026, 8, 15, 9, 12, 30, tzinfo=CN_TZ)
    batch = collector.collect_minute_bars(observed)

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record["event_time"].startswith("2026-08-14T14:50:00")
    assert record["observed_at"].startswith("2026-08-15T09:12:30")
    assert record["available_at"].startswith("2026-08-15T09:12:31")


def test_benchmark_v1_and_v2_evidence_verify_without_mutating_payload():
    legacy = {
        "status": "MOOTDX_NODE_BENCHMARK_COMPLETED",
        "benchmark_version": "mootdx_node_benchmark_v1",
        "trade_date": "2026-08-14",
        "observed_at": "2026-08-14T15:00:00.000+08:00",
        "data_ready": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    legacy["benchmark_evidence_hash"] = compute_benchmark_evidence_hash(
        legacy
    )
    before = deepcopy(legacy)
    legacy_verification = verify_benchmark_evidence(legacy)

    current = run_mootdx_node_benchmark(
        CODES,
        trade_date="2026-08-14",
        endpoint_candidates=[],
        clock=lambda: datetime(2026, 8, 15, 9, 0, tzinfo=CN_TZ),
    )
    current_verification = verify_benchmark_evidence(current)

    assert legacy == before
    assert legacy_verification["status"] == "BENCHMARK_EVIDENCE_VERIFIED"
    assert legacy_verification["benchmark_evidence_schema_version"] == (
        BENCHMARK_EVIDENCE_SCHEMA_V1
    )
    assert current_verification["status"] == "BENCHMARK_EVIDENCE_VERIFIED"
    assert current_verification["benchmark_evidence_schema_version"] == (
        BENCHMARK_EVIDENCE_SCHEMA_V2
    )


def test_formal_worker_ignores_benchmark_offset(monkeypatch):
    build_contracts = []

    class Collector:
        endpoint_id = "unit"

        def collect_minute_bars(self, observed_at):
            return ProviderBatch(
                records=[],
                data_types=["minute_bar"],
                source_version="unit_v1",
                raw_hash="a" * 64,
            )

        def close(self):
            return None

    def build(source, codes, **kwargs):
        build_contracts.append(
            (
                kwargs["minute_offset"],
                kwargs["benchmark_data_trade_date"],
            )
        )
        return Collector()

    monkeypatch.setattr(minute_probe_worker, "build_minute_probe_collector", build)
    base = {
        "source": "mootdx",
        "codes": CODES,
        "observed_at": "2026-08-14T14:50:05+08:00",
        "minute_offset": 320,
    }
    minute_probe_worker.execute_probe_worker_task(
        {**base, "operation": "minute"}
    )
    minute_probe_worker.execute_probe_worker_task(
        {**base, "operation": "benchmark_minute"}
    )
    minute_probe_worker.execute_probe_worker_task(
        {
            **base,
            "operation": "benchmark_preflight",
            "data_trade_date": "2026-08-13",
        }
    )

    assert build_contracts == [
        (800, None),
        (320, None),
        (320, "2026-08-13"),
    ]


def test_benchmark_writer_is_utf8_and_never_overwrites(tmp_path):
    target = tmp_path / "benchmark.json"
    payload = {
        "status": "MOOTDX_NODE_BENCHMARK_COMPLETED",
        "note": "审计",
        "data_ready": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    write_benchmark_json_atomic(target, payload)
    first_hash = _sha256(target)

    assert json.loads(target.read_bytes().decode("utf-8"))["note"] == "审计"
    with pytest.raises(FileExistsError):
        write_benchmark_json_atomic(target, {"changed": True})
    assert _sha256(target) == first_hash


@pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="PowerShell behavior test requires Windows PowerShell",
)
def test_go_nogo_real_powershell_preserves_no_go_and_recovery_hash(tmp_path):
    head = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
    ).strip()
    task_name = f"AStockMissing-{tmp_path.name}"
    common = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(GO_NOGO_SCRIPT),
        "-Date",
        date.today().isoformat(),
        "-MainRoot",
        str(ROOT),
        "-MainHead",
        head,
        "-MainTaskName",
        task_name,
        "-Endpoint",
        "127.0.0.1:1",
        "-DeadlineClock",
        "23:59:59",
        "-RecoveryDeadlineClock",
        "23:59:59",
        "-OutputDirectory",
        str(tmp_path),
    ]
    first = subprocess.run(
        common,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    standard = tmp_path / f"minute_probe_go_nogo_{date.today().isoformat()}.json"
    payload = json.loads(standard.read_text(encoding="utf-8"))
    first_hash = _sha256(standard)

    assert first.returncode == 2
    assert payload["status"] == "SAMPLING_NO_GO"
    assert payload["enabled_tasks"] == []
    process_check = next(
        row for row in payload["checks"] if row["name"] == "no_probe_or_stress_worker"
    )
    assert isinstance(process_check["detail"], str)

    repeated = subprocess.run(
        common,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert repeated.returncode == 3
    assert _sha256(standard) == first_hash

    recovery = subprocess.run(
        [*common, "-RecoveryMode", "recovery"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    recovery_path = tmp_path / (
        f"minute_probe_go_nogo_recovery_{date.today().isoformat()}.json"
    )
    recovery_payload = json.loads(recovery_path.read_text(encoding="utf-8"))

    assert recovery.returncode == 2
    assert recovery_payload["status"] == "SAMPLING_NO_GO"
    assert recovery_payload["original_guard_sha256"].lower() == first_hash
    assert _sha256(standard) == first_hash
    assert recovery_payload["data_ready"] is False
    assert recovery_payload["candidates"] == []
    assert recovery_payload["tickets"] == []
    assert recovery_payload["orders"] == []


@pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="PowerShell behavior test requires Windows PowerShell",
)
def test_go_nogo_gate_failure_keeps_preexisting_task_disabled(tmp_path):
    state_path = tmp_path / "task_state.json"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(GO_NOGO_HARNESS),
            "-GoNoGoScript",
            str(GO_NOGO_SCRIPT),
            "-ProjectRoot",
            str(ROOT),
            "-OutputDirectory",
            str(tmp_path),
            "-StatePath",
            str(state_path),
            "-Date",
            date.today().isoformat(),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    result_path = tmp_path / (
        f"minute_probe_go_nogo_{date.today().isoformat()}.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert state["state"] == "Disabled"
    assert state["disable_count"] >= 1
    assert state["enable_count"] == 0
    assert result["status"] == "SAMPLING_NO_GO"
    assert result["final_task_states"] == {"UnitMainTask": "Disabled"}
    assert result["enabled_tasks"] == []
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


@pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="PowerShell behavior test requires Windows PowerShell",
)
@pytest.mark.parametrize(
    ("scenario", "expected_exit"),
    [
        ("existing_result", 3),
        ("invalid_endpoint", 2),
        ("invalid_codes", 2),
        ("preflight_exception", 2),
        ("write_failure", 4),
    ],
)
def test_go_nogo_failure_paths_disable_ready_task_and_preserve_files(
    tmp_path,
    scenario,
    expected_exit,
):
    state_path = tmp_path / f"task_state_{scenario}.json"
    completed = _run_go_nogo_harness(
        tmp_path,
        state_path,
        scenario=scenario,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert completed.returncode == expected_exit
    assert state["state"] == "Disabled"
    assert state["disable_count"] >= 1
    assert state["enable_count"] == 0
    if scenario == "existing_result":
        assert state["existing_hash_before"]
        assert state["existing_hash_after"] == state["existing_hash_before"]
    if scenario == "write_failure":
        assert state["blocking_hash_before"]
        assert state["blocking_hash_after"] == state["blocking_hash_before"]
    result_path = tmp_path / (
        f"minute_probe_go_nogo_{date.today().isoformat()}.json"
    )
    if scenario not in {"write_failure"}:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert result["status"] == "SAMPLING_NO_GO"
        assert result.get("enabled_tasks", []) == []
        assert result.get("candidates", []) == []
        assert result.get("tickets", []) == []
        assert result.get("orders", []) == []
    if scenario in {"invalid_endpoint", "invalid_codes"}:
        failed_checks = {
            row["name"]
            for row in result["checks"]
            if row["passed"] is False
        }
        expected_check = (
            "endpoint_contract"
            if scenario == "invalid_endpoint"
            else "formal_codes_exact"
        )
        assert expected_check in failed_checks


@pytest.mark.skipif(
    shutil.which("powershell.exe") is None,
    reason="PowerShell behavior test requires Windows PowerShell",
)
def test_go_nogo_validate_only_does_not_change_ready_task_state(tmp_path):
    state_path = tmp_path / "task_state_validate_only.json"
    completed = _run_go_nogo_harness(
        tmp_path,
        state_path,
        scenario="validate_only",
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert completed.returncode == 2
    assert state["state"] == "Ready"
    assert state["disable_count"] == 0
    assert state["enable_count"] == 0
    assert not (
        tmp_path / f"minute_probe_go_nogo_{date.today().isoformat()}.json"
    ).exists()


def test_endpoint_preflight_imports_project_from_arbitrary_working_directory(
    tmp_path,
):
    completed = subprocess.run(
        [
            sys.executable,
            str(ENDPOINT_SCRIPT),
            "--endpoint",
            "127.0.0.1:1",
            "--endpoint-id",
            "unit@127.0.0.1:1",
            "--codes",
            ",".join(CODES),
            "--deadline-ms",
            "2000",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ENDPOINT_PREFLIGHT_FAILED"
    assert "ModuleNotFoundError" not in completed.stderr
    assert payload["data_ready"] is False
    assert payload["candidates"] == []
    assert payload["tickets"] == []
    assert payload["orders"] == []


def _endpoint(identifier, host):
    return {
        "id": identifier,
        "name": identifier,
        "host": host,
        "port": 7709,
    }


def _run_go_nogo_harness(tmp_path, state_path, *, scenario):
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(GO_NOGO_HARNESS),
            "-GoNoGoScript",
            str(GO_NOGO_SCRIPT),
            "-ProjectRoot",
            str(ROOT),
            "-OutputDirectory",
            str(tmp_path),
            "-StatePath",
            str(state_path),
            "-Date",
            date.today().isoformat(),
            "-Scenario",
            scenario,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )


def _worker_result(
    task,
    *,
    covered=None,
    elapsed_ms=800,
    canonical_hash="c" * 64,
    signatures=None,
    presence_by_code=None,
):
    codes = sorted(task["codes"] if covered is None else covered)
    endpoint_id = task["endpoint"]["id"]
    signatures = signatures if signatures is not None else {
        code: {
            "ohlcv_hash": "d" * 64,
            "event_time": (
                f"{task.get('data_trade_date', '2026-08-14')}"
                "T14:50:00+08:00"
            ),
        }
        for code in codes
    }
    presence = presence_by_code if presence_by_code is not None else {
        code: code in signatures for code in task["codes"]
    }
    return {
        "ok": True,
        "payload": {
            "requested_codes": sorted(task["codes"]),
            "covered_codes": codes,
            "presence_by_code": presence,
            "returned_record_count": len(codes) * 240,
            "endpoint_id": endpoint_id,
            "source_versions": ["unit_mootdx_v1"],
            "provider_raw_hash": "e" * 64,
            "raw_response_hashes": ["f" * 64],
            "worker_request_elapsed_ms": elapsed_ms - 50,
            "worker_request_started_at": (
                "2026-08-15T09:30:00.000+08:00"
            ),
            "worker_request_completed_at": (
                "2026-08-15T09:30:00.750+08:00"
            ),
            "signatures": signatures,
            "minute_record_count_by_code": {code: 240 for code in codes},
            "canonical_minute_hash_by_code": {
                code: canonical_hash for code in codes
            },
        },
        "elapsed_ms": elapsed_ms,
        "request_timed_out": False,
        "worker_terminated": False,
        "error_code": "",
        "error": "",
    }


def _timeout_result():
    return {
        "ok": False,
        "elapsed_ms": 2000,
        "request_timed_out": True,
        "worker_terminated": True,
        "error_code": "REQUEST_DEADLINE_EXCEEDED",
        "error": "REQUEST_DEADLINE_EXCEEDED",
    }


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
