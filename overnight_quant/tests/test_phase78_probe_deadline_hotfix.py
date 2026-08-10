from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import sys
import time

from overnight_quant.data.minute_label_probe import (
    PROBE_EVIDENCE_SCHEMA_V2,
    classify_minute_label_samples,
    compute_probe_evidence_hash,
    run_scheduled_minute_label_probe,
)
from overnight_quant.data.probe_evidence import verify_probe_evidence
from overnight_quant.data.probe_worker_process import (
    WORKER_TIMEOUT_ERROR,
    run_probe_worker_process,
)
from overnight_quant.data.source_qualification import (
    _validate_probe_day,
)


DAY = "2026-08-11"
CODES = ["000001", "600000"]
TARGET_CLOCKS = ("14:49:55", "14:50:05", "14:50:30", "14:51:05")


class _AdvancingClock:
    def __init__(self, value: str):
        self.current = datetime.fromisoformat(value)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=float(seconds))

    def monotonic(self) -> float:
        return self.current.timestamp()


def test_killable_worker_terminates_a_40_second_block_without_late_write(
    tmp_path,
):
    worker = tmp_path / "blocking_worker.py"
    started = tmp_path / "started.txt"
    completed = tmp_path / "completed.txt"
    worker.write_text(
        "\n".join(
            [
                "import json, pathlib, sys, time",
                "task = json.loads(sys.stdin.read())",
                "pathlib.Path(task['started']).write_text('started')",
                "time.sleep(40)",
                "pathlib.Path(task['completed']).write_text('completed')",
                "print(json.dumps({'ok': True, 'payload': {}}))",
            ]
        ),
        encoding="utf-8",
    )

    result = run_probe_worker_process(
        {"started": str(started), "completed": str(completed)},
        2000,
        worker_command=[sys.executable, str(worker)],
    )

    assert result["error_code"] == WORKER_TIMEOUT_ERROR
    assert result["request_timed_out"] is True
    assert result["worker_terminated"] is True
    assert result["returncode"] is not None
    assert result["elapsed_ms"] < 5000
    assert started.exists()
    time.sleep(0.25)
    assert not completed.exists()


def test_two_timed_out_workers_do_not_leak_into_the_next_run(tmp_path):
    worker = tmp_path / "blocking_worker.py"
    worker.write_text(
        "\n".join(
            [
                "import json, pathlib, sys, time",
                "task = json.loads(sys.stdin.read())",
                "pathlib.Path(task['started']).write_text('started')",
                "time.sleep(40)",
                "pathlib.Path(task['completed']).write_text('completed')",
            ]
        ),
        encoding="utf-8",
    )
    results = []
    completed_paths = []
    for index in range(2):
        started = tmp_path / f"started_{index}.txt"
        completed = tmp_path / f"completed_{index}.txt"
        completed_paths.append(completed)
        results.append(
            run_probe_worker_process(
                {
                    "started": str(started),
                    "completed": str(completed),
                },
                1000,
                worker_command=[sys.executable, str(worker)],
            )
        )
        assert started.exists()
    time.sleep(0.25)

    assert all(item["request_timed_out"] for item in results)
    assert all(item["returncode"] is not None for item in results)
    assert all(not path.exists() for path in completed_paths)


def test_first_request_timeout_does_not_delay_the_later_three_samples():
    clock = _AdvancingClock(f"{DAY}T14:49:50+08:00")
    minute_calls = []

    def worker(task, deadline_ms):
        assert deadline_ms == 2000
        minute_calls.append(task)
        if len(minute_calls) == 1:
            clock.advance(2.0)
            return {
                "ok": False,
                "error_code": WORKER_TIMEOUT_ERROR,
                "error": WORKER_TIMEOUT_ERROR,
                "request_timed_out": True,
                "worker_terminated": True,
            }
        clock.advance(0.1)
        return _successful_minute_worker(task)

    result = run_scheduled_minute_label_probe(
        CODES,
        trade_date=DAY,
        source="eastmoney",
        clock=clock,
        sleep=clock.advance,
        monotonic=clock.monotonic,
        worker_runner=worker,
    )

    assert len(minute_calls) == 4
    assert [
        sample["request_started_at"][11:19]
        for sample in result["samples"]
    ] == list(TARGET_CLOCKS)
    assert result["samples"][0]["request_timed_out"] is True
    assert all(
        sample["request_timed_out"] is False
        for sample in result["samples"][1:]
    )
    assert result["deadline_exceeded_count"] == 1
    assert result["missed_sample_count"] == 0
    assert result["late_record_count"] == 0
    _assert_research_only(result)


def test_missed_sample_is_not_replayed_and_later_points_continue():
    clock = _AdvancingClock(f"{DAY}T14:50:00+08:00")
    minute_calls = []

    def worker(task, _deadline_ms):
        minute_calls.append(task)
        clock.advance(0.1)
        return _successful_minute_worker(task)

    result = run_scheduled_minute_label_probe(
        CODES,
        trade_date=DAY,
        source="eastmoney",
        clock=clock,
        sleep=clock.advance,
        monotonic=clock.monotonic,
        worker_runner=worker,
    )

    assert len(minute_calls) == 3
    assert result["samples"][0]["error_code"] == "SAMPLE_WINDOW_MISSED"
    assert result["samples"][0]["sample_window_missed"] is True
    assert result["late_start_count"] == 1
    assert result["missed_sample_count"] == 1
    assert result["deadline_exceeded_count"] == 0
    _assert_research_only(result)


def test_failed_sample_still_reports_that_it_started_late():
    samples = _v2_samples()
    samples[0]["request_started_at"] = f"{DAY}T14:49:58.100+08:00"
    samples[0]["request_completed_at"] = f"{DAY}T14:49:58.200+08:00"
    samples[0]["schedule_lag_ms"] = 3100.0
    samples[0]["completion_lag_ms"] = 3200.0
    samples[0]["error_code"] = "HTTP_REQUEST_FAILED"
    samples[0]["error"] = "HTTP_REQUEST_FAILED"

    result = classify_minute_label_samples(
        samples,
        required_codes=CODES,
        source="eastmoney",
    )

    assert "probe_started_late:14:49:55" in result["reasons"]
    assert "probe_request_failed:14:49:55" in result["reasons"]


def test_mootdx_preflight_pins_one_healthy_endpoint_for_all_requests():
    clock = _AdvancingClock(f"{DAY}T14:49:40+08:00")
    calls = []
    bad = {"id": "bad", "host": "127.0.0.1", "port": 7709}
    good = {"id": "good", "host": "127.0.0.2", "port": 7709}

    def worker(task, _deadline_ms):
        calls.append(deepcopy(task))
        if task["operation"] == "preflight":
            if task["endpoint"]["id"] == "bad":
                return {
                    "ok": False,
                    "error_code": "PROBE_REQUEST_FAILED",
                    "error": "unhealthy",
                    "request_timed_out": False,
                    "worker_terminated": False,
                }
            return _successful_minute_worker(task)
        if task["operation"] == "transaction":
            return {
                "ok": True,
                "payload": {
                    "transaction_evidence": {
                        "source": "mootdx",
                        "source_version": "unit_transaction_v1",
                        "trade_date": DAY,
                        "requested_codes": list(CODES),
                        "request_started_at": f"{DAY}T14:51:05+08:00",
                        "request_completed_at": f"{DAY}T14:51:05+08:00",
                        "by_code": {},
                    }
                },
                "request_timed_out": False,
                "worker_terminated": False,
            }
        clock.advance(0.1)
        return _successful_minute_worker(task)

    result = run_scheduled_minute_label_probe(
        CODES,
        trade_date=DAY,
        source="mootdx",
        clock=clock,
        sleep=clock.advance,
        monotonic=clock.monotonic,
        worker_runner=worker,
        endpoint_candidates=[bad, good],
    )
    verification = verify_probe_evidence(result, source="mootdx")

    minute_tasks = [item for item in calls if item["operation"] == "minute"]
    assert result["source_preflight"]["status"] == "SOURCE_PREFLIGHT_READY"
    assert result["source_preflight"]["endpoint_id"] == "good"
    assert len(result["source_preflight"]["attempts"]) == 2
    assert all(item["endpoint"]["id"] == "good" for item in minute_tasks)
    assert verification["status"] == "PROBE_EVIDENCE_VERIFIED"
    _assert_research_only(result)


def test_failed_preflight_stops_sampling_and_produces_verifiable_evidence():
    clock = _AdvancingClock(f"{DAY}T14:49:40+08:00")
    calls = []

    def worker(task, _deadline_ms):
        calls.append(task["operation"])
        return {
            "ok": False,
            "error_code": "PROBE_REQUEST_FAILED",
            "error": "unhealthy",
            "request_timed_out": False,
            "worker_terminated": False,
        }

    result = run_scheduled_minute_label_probe(
        CODES,
        trade_date=DAY,
        source="mootdx",
        clock=clock,
        sleep=clock.advance,
        monotonic=clock.monotonic,
        worker_runner=worker,
        endpoint_candidates=[
            {"id": "bad", "host": "127.0.0.1", "port": 7709}
        ],
    )
    verification = verify_probe_evidence(result, source="mootdx")
    qualification_errors = _validate_probe_day(
        result,
        source="mootdx",
        expected_codes=list(CODES),
        minimum_stock_count=len(CODES),
    )

    assert result["status"] == "SOURCE_PREFLIGHT_FAILED"
    assert calls == ["preflight"]
    assert verification["status"] == "PROBE_EVIDENCE_VERIFIED"
    assert "probe_day_not_provisional" in qualification_errors
    _assert_research_only(result)


def test_v1_hash_contract_remains_stable_without_a_schema_field():
    sample = _legacy_sample()

    evidence_hash = compute_probe_evidence_hash(
        [sample],
        CODES,
        source="eastmoney",
    )

    assert evidence_hash == (
        "463e27f9047b63f687422033eaf13ee5"
        "664ab39ee49b47178bf2253ef802c8ec"
    )


def test_v2_hash_includes_timing_audit_and_is_deterministic():
    samples = _v2_samples()
    preflight = {
        "status": "NOT_REQUIRED",
        "source": "eastmoney",
        "started_at": f"{DAY}T14:49:50.000+08:00",
        "completed_at": f"{DAY}T14:49:50.000+08:00",
        "endpoint_id": "",
        "covered_codes": [],
        "coverage_ratio": 0.0,
        "request_elapsed_ms": 0.0,
        "attempts": [],
    }
    summary = {
        "late_start_count": 0,
        "deadline_exceeded_count": 0,
        "missed_sample_count": 0,
        "late_record_count": 0,
    }
    first = compute_probe_evidence_hash(
        samples,
        CODES,
        source="eastmoney",
        schema_version=PROBE_EVIDENCE_SCHEMA_V2,
        source_preflight=preflight,
        audit_summary=summary,
    )
    second = compute_probe_evidence_hash(
        deepcopy(samples),
        list(reversed(CODES)),
        source="eastmoney",
        schema_version=PROBE_EVIDENCE_SCHEMA_V2,
        source_preflight=deepcopy(preflight),
        audit_summary=deepcopy(summary),
    )
    payload = {
        "status": "MINUTE_LABEL_INCONCLUSIVE",
        "source": "eastmoney",
        "trade_date": DAY,
        "probe_evidence_schema_version": PROBE_EVIDENCE_SCHEMA_V2,
        "source_preflight": preflight,
        "tracked_codes": list(CODES),
        "samples": samples,
        "probe_evidence_hash": first,
        **summary,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    verification = verify_probe_evidence(payload, source="eastmoney")
    drifted = deepcopy(payload)
    drifted["samples"][0]["schedule_lag_ms"] = 1.0
    drift_verification = verify_probe_evidence(
        drifted,
        source="eastmoney",
    )

    assert first == second
    assert verification["status"] == "PROBE_EVIDENCE_VERIFIED"
    assert drift_verification["status"] == "PROBE_EVIDENCE_INVALID"
    assert "minute_probe_evidence_hash_drift" in drift_verification["errors"]


def _successful_minute_worker(task):
    codes = sorted(str(code).zfill(6) for code in task["codes"])
    endpoint = task.get("endpoint") or {}
    return {
        "ok": True,
        "payload": {
            "requested_codes": list(codes),
            "covered_codes": list(codes),
            "presence_by_code": {code: False for code in codes},
            "signatures": {},
            "raw_response_hashes": ["a" * 64],
            "provider_raw_hash": "b" * 64,
            "source_versions": [f"unit_{task['source']}_minute_v1"],
            "returned_record_count": len(codes),
            "endpoint_id": str(endpoint.get("id") or ""),
        },
        "request_timed_out": False,
        "worker_terminated": False,
        "error_code": "",
        "error": "",
    }


def _legacy_sample():
    return {
        "probe_source": "eastmoney",
        "target_at": f"{DAY}T14:49:55+08:00",
        "request_started_at": f"{DAY}T14:49:55+08:00",
        "request_completed_at": f"{DAY}T14:49:55.100+08:00",
        "request_elapsed_ms": 100.0,
        "requested_codes": list(CODES),
        "covered_codes": list(CODES),
        "presence_by_code": {code: False for code in CODES},
        "signatures": {},
        "raw_response_hashes": ["a" * 64],
        "provider_raw_hash": "b" * 64,
        "source_versions": ["unit_eastmoney_minute_v1"],
        "sample_trade_date": DAY,
        "error": "",
    }


def _v2_samples():
    samples = []
    for clock in TARGET_CLOCKS:
        target = datetime.fromisoformat(f"{DAY}T{clock}+08:00")
        completed = target + timedelta(milliseconds=100)
        samples.append(
            {
                **_legacy_sample(),
                "target_at": target.isoformat(timespec="seconds"),
                "sampled_at": target.isoformat(timespec="seconds"),
                "request_started_at": target.isoformat(
                    timespec="milliseconds"
                ),
                "request_completed_at": completed.isoformat(
                    timespec="milliseconds"
                ),
                "schedule_lag_ms": 0.0,
                "completion_lag_ms": 100.0,
                "request_deadline_ms": 2000,
                "request_elapsed_ms": 100.0,
                "request_timed_out": False,
                "sample_window_missed": False,
                "worker_terminated": False,
                "error_code": "",
                "returned_record_count": len(CODES),
                "endpoint_id": "",
            }
        )
    return samples


def _assert_research_only(result):
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []
