from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_label_probe import (
    PROBE_EVIDENCE_SCHEMA_V2,
    classify_minute_label_samples,
    compute_probe_evidence_hash,
    run_scheduled_minute_label_probe,
)
from overnight_quant.data.probe_evidence import verify_probe_evidence
from overnight_quant.data.snapshot_store import ProviderBatch
from overnight_quant.data.source_qualification import _validate_probe_day
from overnight_quant.scripts import run_minute_label_probe as probe_cli


DAY = "2026-09-02"
CODES = ["000001", "000333", "600000", "600519", "601318"]
CLOCKS = ("14:49:55", "14:50:05", "14:50:30", "14:51:05")
ENDPOINT = {
    "id": "fixed@127.0.0.1:7709",
    "name": "fixed",
    "host": "127.0.0.1",
    "port": 7709,
}


class _Clock:
    def __init__(self, value: str):
        self.current = datetime.fromisoformat(value)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=float(seconds))

    def monotonic(self) -> float:
        return self.current.timestamp()


class _MootdxCollector:
    probe_source = "mootdx"
    source_version = "unit_mootdx_minute_v1"

    def __init__(self):
        self.codes = list(CODES)

    def collect_minute_bars(self, _observed_at):
        records = []
        for code in self.codes:
            records.append(
                {
                    "data_type": "minute_bar",
                    "event_time": f"{DAY}T14:50:00+08:00",
                    "source": "mootdx_tdx_std_minute",
                    "source_version": self.source_version,
                    "raw_hash": "a" * 64,
                    "payload": {
                        "code": code,
                        "open": 10.0,
                        "high": 10.2,
                        "low": 9.9,
                        "close": 10.1,
                        "volume": 1000.0,
                        "amount": 10100.0,
                        "field_units": {"volume": "share"},
                    },
                }
            )
        return ProviderBatch(
            records=records,
            data_types=["minute_bar"],
            source_version=self.source_version,
            raw_hash="b" * 64,
        )

    def collect_transaction_evidence(self, _observed_at):
        return {
            "source": "mootdx",
            "source_version": "unit_mootdx_transaction_v1",
            "trade_date": DAY,
            "requested_codes": list(CODES),
            "by_code": {},
        }

    def close(self):
        return None


def test_failed_base_probe_cannot_be_upgraded_by_transaction_attribution(
    monkeypatch,
):
    clock = _Clock(f"{DAY}T14:50:00+08:00")
    monkeypatch.setattr(
        "overnight_quant.data.minute_label_probe."
        "attribute_mootdx_minute_intervals",
        lambda *_args, **_kwargs: {
            "status": "minute_end_provisional",
            "source": "mootdx",
            "per_stock": {},
            "all_stocks_final": True,
            "reasons": ["unit_attribution"],
            "provisional_time_contract": {},
            "combined_evidence_hash": "c" * 64,
        },
    )

    result = run_scheduled_minute_label_probe(
        CODES,
        trade_date=DAY,
        source="mootdx",
        collectors=_MootdxCollector(),
        clock=clock,
        sleep=clock.advance,
        monotonic=clock.monotonic,
    )

    assert result["status"] == "MINUTE_LABEL_INCONCLUSIVE"
    assert result["minute_label_validation_status"] == "INCONCLUSIVE"
    assert result["transaction_attribution"]["status"] == (
        "minute_end_provisional"
    )
    assert result["missed_sample_count"] == 1
    assert result["reasons"].count("probe_started_late:14:49:55") == 1
    _assert_safe(result)


def test_unstarted_missed_sample_verifies_but_qualification_rejects():
    samples = _complete_v2_samples()
    target = datetime.fromisoformat(samples[0]["target_at"])
    missed_at = target + timedelta(milliseconds=3000)
    samples[0].update(
        {
            "request_started_at": missed_at.isoformat(timespec="milliseconds"),
            "request_completed_at": missed_at.isoformat(timespec="milliseconds"),
            "schedule_lag_ms": 3000.0,
            "completion_lag_ms": 3000.0,
            "request_elapsed_ms": 0.0,
            "sample_window_missed": True,
            "error_code": "SAMPLE_WINDOW_MISSED",
            "error": "SAMPLE_WINDOW_MISSED",
            "covered_codes": [],
            "presence_by_code": {code: False for code in CODES},
            "signatures": {},
            "raw_response_hashes": [],
            "provider_raw_hash": "",
            "source_versions": [],
            "returned_record_count": 0,
            "endpoint_id": "",
        }
    )
    preflight = _ready_preflight()
    audit = {
        "late_start_count": 1,
        "deadline_exceeded_count": 0,
        "missed_sample_count": 1,
        "late_record_count": 0,
    }
    payload = classify_minute_label_samples(
        samples,
        required_codes=CODES,
        source="mootdx",
        schema_version=PROBE_EVIDENCE_SCHEMA_V2,
        source_preflight=preflight,
        audit_summary=audit,
    )
    payload.update(
        {
            "execution_ok": True,
            "data_ready": False,
            "trade_date": DAY,
            "source_role": "qualification_candidate",
            **audit,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }
    )

    verification = verify_probe_evidence(payload, source="mootdx")
    errors = _validate_probe_day(
        payload,
        source="mootdx",
        expected_codes=CODES,
        minimum_stock_count=len(CODES),
    )

    assert verification["status"] == "PROBE_EVIDENCE_VERIFIED"
    assert "probe_timing_audit_failed:missed_sample_count" in errors
    assert "probe_sample_failed:14:49:55" in errors
    _assert_safe(verification)


def test_late_formal_window_without_fixed_endpoint_fails_before_worker_call():
    clock = _Clock(f"{DAY}T14:49:49+08:00")
    calls = []

    def worker(task, _deadline_ms):
        calls.append(task)
        raise AssertionError("worker must not be called")

    result = run_scheduled_minute_label_probe(
        CODES,
        trade_date=DAY,
        source="mootdx",
        clock=clock,
        sleep=clock.advance,
        monotonic=clock.monotonic,
        worker_runner=worker,
    )

    assert result["status"] == "SOURCE_PREFLIGHT_FAILED"
    assert result["source_preflight"]["error_code"] == (
        "FIXED_ENDPOINT_REQUIRED_IN_FORMAL_WINDOW"
    )
    assert calls == []
    _assert_safe(result)


def test_fixed_endpoint_is_the_only_endpoint_tried_and_failure_is_final():
    clock = _Clock(f"{DAY}T14:49:49+08:00")
    calls = []

    def worker(task, _deadline_ms):
        calls.append(task["endpoint"]["id"])
        return {
            "ok": False,
            "error_code": "PROBE_REQUEST_FAILED",
            "error": "unit failure",
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
        endpoint_candidates=[ENDPOINT],
    )

    assert result["status"] == "SOURCE_PREFLIGHT_FAILED"
    assert calls == [ENDPOINT["id"]]
    assert len(result["source_preflight"]["attempts"]) == 1
    _assert_safe(result)


def test_probe_cli_forwards_one_fixed_endpoint(monkeypatch, capsys):
    captured = {}

    def fake_probe(codes, **kwargs):
        captured.update({"codes": codes, **kwargs})
        return {
            "status": "MINUTE_LABEL_INCONCLUSIVE",
            "data_ready": False,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }

    monkeypatch.setattr(probe_cli, "run_scheduled_minute_label_probe", fake_probe)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_minute_label_probe.py",
            "--source",
            "mootdx",
            "--codes",
            ",".join(CODES),
            "--date",
            DAY,
            "--endpoint",
            "127.0.0.1:7709",
            "--endpoint-id",
            ENDPOINT["id"],
        ],
    )

    exit_code = probe_cli.main()
    json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert captured["endpoint_candidates"] == [
        {
            **ENDPOINT,
            "name": "mootdx_fixed",
        }
    ]


@pytest.mark.parametrize(
    ("listening", "expected_status", "allowed"),
    [
        (True, "SOURCE_LOCAL_PROXY_READY", True),
        (False, "SOURCE_LOCAL_PROXY_UNAVAILABLE", False),
    ],
)
def test_eastmoney_local_proxy_check_is_source_scoped(
    listening,
    expected_status,
    allowed,
):
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "source_proxy_readiness.ps1"
    )
    listener_value = "$true" if listening else "$false"
    command = (
        f". '{script}'; "
        "$r=Get-SourceProxyReadiness -Source eastmoney "
        "-ResolveProxy { param($destination) "
        "[uri]'http://127.0.0.1:7897' } "
        f"-TestLocalListener {{ param($hostName,$port) {listener_value} }}; "
        "$r|ConvertTo-Json -Depth 5"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["status"] == expected_status
    assert result["source_allowed"] is allowed
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def test_mootdx_proxy_check_never_resolves_or_blocks():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "source_proxy_readiness.ps1"
    )
    command = (
        f". '{script}'; "
        "$r=Get-SourceProxyReadiness -Source mootdx "
        "-ResolveProxy { throw 'must_not_run' } "
        "-TestLocalListener { throw 'must_not_run' }; "
        "$r|ConvertTo-Json -Depth 5"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "SOURCE_PROXY_CHECK_NOT_REQUIRED"
    assert result["source_allowed"] is True
    _assert_safe(result)


def _complete_v2_samples():
    samples = []
    for index, clock in enumerate(CLOCKS):
        target = datetime.fromisoformat(f"{DAY}T{clock}+08:00")
        completed = target + timedelta(milliseconds=100)
        raw_hash = f"{index + 1:x}" * 64
        samples.append(
            {
                "probe_source": "mootdx",
                "target_at": target.isoformat(timespec="seconds"),
                "sampled_at": target.isoformat(timespec="seconds"),
                "request_started_at": target.isoformat(timespec="milliseconds"),
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
                "requested_codes": list(CODES),
                "covered_codes": list(CODES),
                "presence_by_code": {code: False for code in CODES},
                "signatures": {},
                "raw_response_hashes": [raw_hash],
                "provider_raw_hash": "a" * 64,
                "source_versions": ["unit_mootdx_minute_v1"],
                "returned_record_count": len(CODES),
                "endpoint_id": ENDPOINT["id"],
                "sample_trade_date": DAY,
                "error": "",
            }
        )
    return samples


def _ready_preflight():
    started = f"{DAY}T14:49:40.000+08:00"
    completed = f"{DAY}T14:49:40.100+08:00"
    attempt = {
        "endpoint_id": ENDPOINT["id"],
        "request_started_at": started,
        "request_completed_at": completed,
        "request_elapsed_ms": 100.0,
        "request_deadline_ms": 2000,
        "request_timed_out": False,
        "worker_terminated": False,
        "covered_codes": list(CODES),
        "coverage_ratio": 1.0,
        "provider_raw_hash": "b" * 64,
        "raw_response_hashes": ["c" * 64],
        "source_versions": ["unit_mootdx_minute_v1"],
        "error_code": "",
    }
    return {
        "status": "SOURCE_PREFLIGHT_READY",
        "source": "mootdx",
        "started_at": started,
        "completed_at": completed,
        "endpoint_id": ENDPOINT["id"],
        "selected_endpoint": dict(ENDPOINT),
        "covered_codes": list(CODES),
        "coverage_ratio": 1.0,
        "request_elapsed_ms": 100.0,
        "attempts": [attempt],
    }


def _assert_safe(result):
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []
