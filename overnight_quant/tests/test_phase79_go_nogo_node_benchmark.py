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
    FORMAL_MINUTE_OFFSET,
    run_mootdx_node_benchmark,
    write_benchmark_json_atomic,
)
from overnight_quant.data.snapshot_store import ProviderBatch
from overnight_quant.data import minute_probe_worker


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
    assert len(result["qualification"]) == 3
    assert all(row["round_count"] == 5 for row in result["qualification"])
    assert result["recommended_endpoint"]["id"] == "fast"
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
        endpoint_candidates=[endpoint],
        qualification_rounds=5,
        worker_runner=worker,
        compare_offsets=False,
    )

    summary = result["qualification"][0]
    assert summary["timeout_count"] == 1
    assert summary["recommendation_eligible"] is False
    assert result["recommended_endpoint"] is None


def test_offset_comparison_reports_market_content_equivalence():
    endpoint = _endpoint("fast", "10.0.0.1")

    result = run_mootdx_node_benchmark(
        CODES,
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


def test_formal_worker_ignores_benchmark_offset(monkeypatch):
    offsets = []

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
        offsets.append(kwargs["minute_offset"])
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

    assert offsets == [800, 320]


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


def _worker_result(
    task,
    *,
    covered=None,
    elapsed_ms=800,
    canonical_hash="c" * 64,
):
    codes = sorted(covered or task["codes"])
    endpoint_id = task["endpoint"]["id"]
    signatures = {
        code: {
            "ohlcv_hash": "d" * 64,
            "event_time": "2026-08-14T14:50:00+08:00",
        }
        for code in codes
    }
    return {
        "ok": True,
        "payload": {
            "requested_codes": sorted(task["codes"]),
            "covered_codes": codes,
            "returned_record_count": len(codes) * 240,
            "endpoint_id": endpoint_id,
            "source_versions": ["unit_mootdx_v1"],
            "provider_raw_hash": "e" * 64,
            "raw_response_hashes": ["f" * 64],
            "worker_request_elapsed_ms": elapsed_ms - 50,
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
