from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_go_nogo import (
    NO_GO,
    SAMPLING_GO,
    file_sha256,
    run_market_source_go_nogo,
    verify_market_source_go_nogo_evidence,
)
from overnight_quant.data.static_source_qualification import (
    S1_PARTIAL_QUALIFICATION_RECORD_HASH,
)
from overnight_quant.scripts import run_market_source_validation as validation


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "overnight_quant" / "scripts" / "run_market_source_go_nogo.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
TRADE_DATE = "2026-09-18"
FIXED_CODES = "000001,000333,600000,600519,601318"


def _calendar() -> dict:
    return {
        "source": "tencent",
        "source_version": "ifzq_fqkline_day_v2026-07-30",
        "raw_hash": "a" * 64,
        "trade_dates": [TRADE_DATE],
        "qualification_record_hash": S1_PARTIAL_QUALIFICATION_RECORD_HASH,
        "qualification_status": "qualified",
    }


def _environment(**changes) -> dict:
    value = {
        "now": f"{TRADE_DATE}T12:30:00+08:00",
        "timezone_id": "China Standard Time",
        "clock_skew_ms": 250,
        "dns": True,
        "tls": True,
        "proxy": {"in_use": False, "local": False, "listening": None},
        "endpoint_reachability": {
            "market_breadth": True,
            "industry_snapshot": True,
            "fund_flow": True,
        },
        "request_hashes": {
            "market_breadth": "1" * 64,
            "industry_snapshot": "2" * 64,
            "fund_flow": "3" * 64,
        },
        "output_writable": True,
        "same_day_result_exists": False,
        "residual_workers": [],
        "duplicate_tasks": [],
        "similar_collectors": [],
    }
    value.update(changes)
    return value


def _run_ps(tmp_path: Path, environment: dict, *, calendar=None, output=None):
    if POWERSHELL is None:
        pytest.skip("PowerShell is required for behavior tests")
    output = output or tmp_path / "go_nogo.json"
    calendar_path = tmp_path / "calendar.json"
    fixture_path = tmp_path / "environment.json"
    calendar_bytes = json.dumps(
        calendar or _calendar(), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    calendar_path.write_bytes(calendar_bytes)
    fixture_path.write_text(
        json.dumps(environment, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    command = [
        POWERSHELL,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-Date",
        TRADE_DATE,
        "-ProjectRoot",
        str(ROOT),
        "-Output",
        str(output),
        "-CalendarContract",
        str(calendar_path),
        "-CalendarFileSha256",
        hashlib.sha256(calendar_bytes).hexdigest(),
        "-Codes",
        FIXED_CODES,
        "-CutoffClock",
        "13:30:00",
        "-TestOnlyEnvironmentFixture",
        str(fixture_path),
        "-PythonExe",
        sys.executable,
    ]
    environment_vars = dict(os.environ)
    environment_vars["A_STOCK_GO_NOGO_TEST_MODE"] = "1"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment_vars,
    )
    payload = (
        json.loads(completed.stdout)
        if completed.returncode == 3
        else json.loads(output.read_text(encoding="utf-8"))
        if output.exists()
        else json.loads(completed.stdout)
    )
    return completed, payload, output


def _assert_safe(payload):
    assert payload["data_ready"] is False
    assert payload["hard_gate_authorized"] is False
    assert payload["candidates"] == []
    assert payload["tickets"] == []
    assert payload["orders"] == []
    assert payload["consecutive_qualified_days"] == 0
    assert payload["task_actions"] == []
    assert payload["enabled_tasks"] == []


def test_powershell_normal_environment_writes_sampling_go(tmp_path):
    completed, payload, output = _run_ps(tmp_path, _environment())
    assert completed.returncode == 0
    assert payload["status"] == SAMPLING_GO
    assert payload["evidence_scope"] == "test_only"
    assert payload["sampling_authorized"] is False
    _assert_safe(payload)
    anchor = file_sha256(output)
    verified = verify_market_source_go_nogo_evidence(
        payload, expected_file_sha256=anchor, actual_file_sha256=anchor
    )
    assert verified["evidence_integrity_verified"] is True


def test_production_environment_can_authorize_sampling_without_counting_day():
    result = run_market_source_go_nogo(
        trade_date=TRADE_DATE,
        codes=FIXED_CODES.split(","),
        calendar_contract=_calendar(),
        environment=_environment(),
        started_at=f"{TRADE_DATE}T12:29:59+08:00",
        completed_at=f"{TRADE_DATE}T12:30:01+08:00",
        evidence_scope="production",
    )
    assert result["status"] == SAMPLING_GO
    assert result["sampling_authorized"] is True
    assert result["qualification_result"] == "NOT_EVALUATED"
    _assert_safe(result)


def test_environment_fixture_is_rejected_without_test_mode(tmp_path):
    if POWERSHELL is None:
        pytest.skip("PowerShell is required for behavior tests")
    output = tmp_path / "go_nogo.json"
    calendar_path = tmp_path / "calendar.json"
    fixture_path = tmp_path / "environment.json"
    calendar_bytes = json.dumps(_calendar(), sort_keys=True).encode("utf-8")
    calendar_path.write_bytes(calendar_bytes)
    fixture_path.write_text(json.dumps(_environment()), encoding="utf-8")
    command = [
        POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT), "-Date", TRADE_DATE, "-ProjectRoot", str(ROOT),
        "-Output", str(output), "-CalendarContract", str(calendar_path),
        "-CalendarFileSha256", hashlib.sha256(calendar_bytes).hexdigest(),
        "-TestOnlyEnvironmentFixture", str(fixture_path), "-PythonExe", sys.executable,
    ]
    environment_vars = dict(os.environ)
    environment_vars.pop("A_STOCK_GO_NOGO_TEST_MODE", None)
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, env=environment_vars
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert payload["status"] == NO_GO
    assert payload["failure_reasons"][0].startswith(
        "go_nogo_failed:ValueError:test_only_environment_fixture_forbidden"
    )
    assert not output.exists()
    _assert_safe(payload)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"now": f"{TRADE_DATE}T13:30:00.001+08:00"}, "before_cutoff"),
        ({"proxy": {"in_use": True, "local": True, "listening": False}}, "proxy_readiness"),
        ({"residual_workers": ["123"]}, "no_residual_workers"),
        ({"duplicate_tasks": ["AStockMarketSource-Unit"]}, "no_duplicate_tasks"),
        ({"similar_collectors": ["market_source_validation"]}, "no_similar_collectors"),
    ],
)
def test_powershell_environment_failures_are_no_go(tmp_path, changes, reason):
    completed, payload, _ = _run_ps(tmp_path, _environment(**changes))
    assert completed.returncode == 2
    assert payload["status"] == NO_GO
    assert reason in payload["failure_reasons"]
    assert payload["sampling_authorized"] is False
    _assert_safe(payload)


def test_powershell_non_trading_day_is_no_go(tmp_path):
    calendar = _calendar()
    calendar["trade_dates"] = ["2026-09-17"]
    completed, payload, _ = _run_ps(tmp_path, _environment(), calendar=calendar)
    assert completed.returncode == 2
    assert payload["status"] == NO_GO
    assert "trusted_trading_day" in payload["failure_reasons"]
    _assert_safe(payload)


def test_powershell_duplicate_output_is_not_overwritten(tmp_path):
    output = tmp_path / "go_nogo.json"
    original = b'{"immutable":true}\n'
    output.write_bytes(original)
    before = hashlib.sha256(original).hexdigest()
    completed, payload, _ = _run_ps(tmp_path, _environment(), output=output)
    assert completed.returncode == 3
    assert payload["status"] == NO_GO
    assert payload["failure_reasons"] == ["immutable_result_exists"]
    assert hashlib.sha256(output.read_bytes()).hexdigest() == before
    _assert_safe(payload)


def test_go_then_formal_collection_failure_never_counts_a_day(tmp_path, monkeypatch):
    completed, go, _ = _run_ps(tmp_path, _environment())
    assert completed.returncode == 0

    def failed_worker(task, deadline_ms, worker_command):
        return {
            "ok": False,
            "error_code": "REQUEST_DEADLINE_EXCEEDED",
            "request_timed_out": True,
            "worker_terminated": True,
            "elapsed_ms": deadline_ms,
        }

    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    formal = validation.run_market_source_validation(
        network=True,
        trade_date=TRADE_DATE,
        output="formal_failure.json",
        worker_runner=failed_worker,
    )
    assert go["status"] == SAMPLING_GO
    assert go["sampling_authorized"] is False
    assert formal["provider_validation_passed"] is False
    assert formal["consecutive_qualified_days"] == 0
    assert formal["data_ready"] is False
    assert formal["candidates"] == formal["tickets"] == formal["orders"] == []


def test_resigned_tampering_and_wrong_external_anchor_are_rejected(tmp_path):
    _, payload, output = _run_ps(tmp_path, _environment())
    anchor = file_sha256(output)
    tampered = deepcopy(payload)
    tampered["status"] = NO_GO
    from overnight_quant.data.market_source_go_nogo import compute_go_nogo_evidence_hash
    tampered["go_nogo_evidence_hash"] = compute_go_nogo_evidence_hash(tampered)
    result = verify_market_source_go_nogo_evidence(
        tampered, expected_file_sha256=anchor, actual_file_sha256=anchor
    )
    assert result["evidence_integrity_verified"] is False
    assert "status_mismatch" in result["errors"]
    wrong = verify_market_source_go_nogo_evidence(
        payload, expected_file_sha256="f" * 64, actual_file_sha256=anchor
    )
    assert wrong["evidence_integrity_verified"] is False
    assert "external_sha256_anchor_mismatch" in wrong["errors"]
