from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from overnight_quant.data.market_session_confirmation import (
    SESSION_CONFIRMED,
    SESSION_REJECTED,
    build_market_session_confirmation,
    compute_market_session_confirmation_hash,
    file_sha256 as confirmation_file_sha256,
    verify_market_session_confirmation_evidence,
    write_market_session_confirmation_json_atomic,
)
from overnight_quant.data.market_source_go_nogo import (
    GO_NOGO_EVIDENCE_SCHEMA_V1,
    GO_NOGO_VERIFIER_CONTRACT_V1,
    NO_GO,
    SAMPLING_GO,
    compute_go_nogo_evidence_hash,
    run_market_source_go_nogo,
    verify_market_source_go_nogo_evidence,
)
from overnight_quant.data.market_source_providers import (
    FIXED_CODES,
    SOURCE_IDENTITIES,
)
from overnight_quant.data.source_capability_adapters import (
    compute_source_adapter_registry_hash,
    get_source_adapter_registry,
)
from overnight_quant.data.static_source_qualification import (
    S1_PARTIAL_QUALIFICATION_RECORD_HASH,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_ADAPTER,
    TENCENT_ORIGIN_SOURCE,
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_SOURCE_VERSION,
    compute_tencent_request_hash,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "overnight_quant" / "scripts" / "run_market_source_go_nogo.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
TRADE_DATE = "2026-09-18"


def _calendar() -> dict:
    target = date.fromisoformat(TRADE_DATE)
    values = []
    cursor = target - timedelta(days=1)
    while len(values) < 65:
        if cursor.weekday() < 5:
            values.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    values.reverse()
    return {
        "source": "tencent",
        "source_version": "ifzq_fqkline_day_v2026-07-30",
        "raw_hash": "a" * 64,
        "trade_dates": values,
        "latest_completed_trade_date": values[-1],
        "qualification_record_hash": S1_PARTIAL_QUALIFICATION_RECORD_HASH,
        "qualification_status": "qualified",
    }


def _adapter_execution(**changes) -> dict:
    binding = next(
        row
        for row in get_source_adapter_registry()
        if row["capability"] == "quote" and row["origin_source"] == "tencent"
    )
    registry_hash = compute_source_adapter_registry_hash()
    result = {
        "status": "SOURCE_ADAPTER_BOUND",
        "execution_ok": True,
        "provider_called": True,
        "selection_registry_scope": "production",
        "production_adapter_registry_hash": registry_hash,
        "selection_adapter_registry_hash": registry_hash,
        "requested_identity": [
            "quote",
            TENCENT_ORIGIN_SOURCE,
            TENCENT_ADAPTER,
            TENCENT_SOURCE_VERSION,
        ],
        "binding": binding,
    }
    result.update(changes)
    return result


def _records(*, event_dates=None, codes=FIXED_CODES, mutate=None) -> list[dict]:
    dates = list(event_dates or [TRADE_DATE] * len(codes))
    request_hash = compute_tencent_request_hash(FIXED_CODES)
    rows = []
    for index, code in enumerate(codes):
        row = {
            "capability": "quote",
            "origin_source": TENCENT_ORIGIN_SOURCE,
            "adapter": TENCENT_ADAPTER,
            "source_version": TENCENT_SOURCE_VERSION,
            "event_time": f"{dates[index]}T10:00:00+08:00",
            "observed_at": f"{TRADE_DATE}T10:00:01+08:00",
            "available_at": f"{TRADE_DATE}T10:00:01.100000+08:00",
            "request_hash": request_hash,
            "raw_hash": "b" * 64,
            "payload": {"code": code, "price": 10.0},
        }
        if mutate:
            mutate(index, row)
        rows.append(row)
    return rows


def _confirmation(*, records=None, adapter_execution=None, provider_key=None) -> dict:
    return build_market_session_confirmation(
        trade_date=TRADE_DATE,
        records=_records() if records is None else records,
        provider_key=provider_key or TENCENT_QUOTE_PROVIDER_KEY,
        adapter_execution=adapter_execution or _adapter_execution(),
        started_at=f"{TRADE_DATE}T10:00:00+08:00",
        completed_at=f"{TRADE_DATE}T10:00:02+08:00",
    )


def _environment() -> dict:
    return {
        "now": f"{TRADE_DATE}T12:30:00+08:00",
        "timezone_id": "China Standard Time",
        "clock_skew_ms": 100,
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


def _json_sha(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _run_go(*, calendar=None, confirmation=None, scope="production") -> dict:
    calendar = _calendar() if calendar is None else calendar
    confirmation = _confirmation() if confirmation is None else confirmation
    return run_market_source_go_nogo(
        trade_date=TRADE_DATE,
        codes=FIXED_CODES,
        calendar_contract=calendar,
        calendar_expected_file_sha256=_json_sha(calendar),
        calendar_actual_file_sha256=_json_sha(calendar),
        session_confirmation_contract=confirmation,
        session_expected_file_sha256=_json_sha(confirmation),
        session_actual_file_sha256=_json_sha(confirmation),
        environment=_environment(),
        started_at=f"{TRADE_DATE}T12:29:59+08:00",
        completed_at=f"{TRADE_DATE}T12:30:01+08:00",
        evidence_scope=scope,
    )


def _assert_safe(payload: dict) -> None:
    assert payload["data_ready"] is False
    assert payload["hard_gate_authorized"] is False
    assert payload["candidates"] == []
    assert payload["tickets"] == []
    assert payload["orders"] == []


def test_completed_calendar_excludes_target_but_current_session_allows_go():
    result = _run_go()
    assert TRADE_DATE not in _calendar()["trade_dates"]
    assert result["status"] == SAMPLING_GO
    assert result["sampling_authorized"] is True
    assert result["qualification_result"] == "NOT_EVALUATED"
    assert result["consecutive_qualified_days"] == 0
    _assert_safe(result)


def test_target_date_in_completed_calendar_is_rejected():
    calendar = _calendar()
    calendar["trade_dates"].append(TRADE_DATE)
    calendar["latest_completed_trade_date"] = TRADE_DATE
    result = _run_go(calendar=calendar)
    assert result["status"] == NO_GO
    assert "completed_calendar_contract" in result["failure_reasons"]
    _assert_safe(result)


@pytest.mark.parametrize("case", ["duplicate", "unsorted", "weekend"])
def test_completed_calendar_structure_is_strict(case):
    calendar = _calendar()
    if case == "duplicate":
        calendar["trade_dates"].insert(-1, calendar["trade_dates"][-2])
    elif case == "unsorted":
        calendar["trade_dates"][-2:] = reversed(calendar["trade_dates"][-2:])
    else:
        calendar["trade_dates"][-1] = "2026-09-12"
        calendar["latest_completed_trade_date"] = "2026-09-12"
    assert _run_go(calendar=calendar)["status"] == NO_GO


def test_missing_current_session_confirmation_is_rejected():
    result = _run_go(confirmation={})
    assert result["status"] == NO_GO
    assert "current_session_confirmation_contract" in result["failure_reasons"]
    _assert_safe(result)


def test_current_session_requires_all_five_codes():
    confirmation = _confirmation(records=_records(codes=FIXED_CODES[:-1]))
    assert confirmation["status"] == SESSION_REJECTED
    assert "fixed_code_coverage_incomplete" in confirmation["confirmation_errors"]
    assert _run_go(confirmation=confirmation)["status"] == NO_GO


def test_same_day_quote_before_market_session_is_rejected():
    def mutate(_index, row):
        row["event_time"] = f"{TRADE_DATE}T09:00:00+08:00"

    confirmation = _confirmation(records=_records(mutate=mutate))
    assert confirmation["status"] == SESSION_REJECTED
    assert any("record_before_market_session" in item for item in confirmation["confirmation_errors"])


@pytest.mark.parametrize(
    "event_dates",
    [
        ["2026-09-17"] * 5,
        [TRADE_DATE, TRADE_DATE, "2026-09-17", TRADE_DATE, TRADE_DATE],
    ],
)
def test_stale_or_mixed_quote_dates_are_rejected(event_dates):
    confirmation = _confirmation(records=_records(event_dates=event_dates))
    assert confirmation["status"] == SESSION_REJECTED
    assert any("record_trade_date_mismatch" in error for error in confirmation["confirmation_errors"])
    assert _run_go(confirmation=confirmation)["status"] == NO_GO


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("event_time", f"{TRADE_DATE}T10:00:02+08:00", "record_event_after_observed"),
        ("available_at", f"{TRADE_DATE}T10:00:00+08:00", "record_available_before_observed"),
    ],
)
def test_quote_time_order_is_strict(field, value, error):
    def mutate(index, row):
        if index == 0:
            row[field] = value

    confirmation = _confirmation(records=_records(mutate=mutate))
    assert confirmation["status"] == SESSION_REJECTED
    assert any(error in item for item in confirmation["confirmation_errors"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.update(origin_source="eastmoney"),
        lambda row: row.update(source_version="wrong"),
    ],
)
def test_non_tencent_or_wrong_version_is_rejected(mutate):
    def change(index, row):
        if index == 0:
            mutate(row)

    confirmation = _confirmation(records=_records(mutate=change))
    assert confirmation["status"] == SESSION_REJECTED
    assert any("record_source_identity_invalid" in item for item in confirmation["confirmation_errors"])


def test_candidate_or_test_only_provider_cannot_confirm_session():
    adapter = _adapter_execution(selection_registry_scope="test_only")
    confirmation = _confirmation(adapter_execution=adapter)
    assert confirmation["status"] == SESSION_REJECTED
    assert "production_adapter_execution_invalid" in confirmation["confirmation_errors"]


def test_confirmation_external_sha_mismatch_is_rejected():
    confirmation = _confirmation()
    result = verify_market_session_confirmation_evidence(
        confirmation,
        expected_file_sha256="f" * 64,
        actual_file_sha256=_json_sha(confirmation),
    )
    assert result["evidence_integrity_verified"] is False
    assert "external_sha256_anchor_mismatch" in result["errors"]

    go = run_market_source_go_nogo(
        trade_date=TRADE_DATE,
        codes=FIXED_CODES,
        calendar_contract=_calendar(),
        calendar_expected_file_sha256=_json_sha(_calendar()),
        calendar_actual_file_sha256=_json_sha(_calendar()),
        session_confirmation_contract=confirmation,
        session_expected_file_sha256="f" * 64,
        session_actual_file_sha256=_json_sha(confirmation),
        environment=_environment(),
        started_at=f"{TRADE_DATE}T12:29:59+08:00",
        completed_at=f"{TRADE_DATE}T12:30:01+08:00",
        evidence_scope="production",
    )
    assert go["status"] == NO_GO
    assert go["sampling_authorized"] is False


def test_resigned_confirmation_tampering_fails_external_anchor():
    original = _confirmation()
    anchor = _json_sha(original)
    tampered = deepcopy(original)
    tampered["records"][0]["event_time"] = "2026-09-17T10:00:00+08:00"
    tampered["confirmation_errors"] = [
        "record_trade_date_mismatch:000001"
    ]
    tampered["status"] = SESSION_REJECTED
    tampered["market_session_confirmation_evidence_hash"] = (
        compute_market_session_confirmation_hash(tampered)
    )
    result = verify_market_session_confirmation_evidence(
        tampered,
        expected_file_sha256=anchor,
        actual_file_sha256=_json_sha(tampered),
    )
    assert result["evidence_integrity_verified"] is False
    assert "external_sha256_anchor_mismatch" in result["errors"]


def test_weekend_with_only_previous_day_quotes_is_no_go():
    saturday = "2026-09-19"
    records = _records(event_dates=[TRADE_DATE] * 5)
    confirmation = build_market_session_confirmation(
        trade_date=saturday,
        records=records,
        provider_key=TENCENT_QUOTE_PROVIDER_KEY,
        adapter_execution=_adapter_execution(),
        started_at=f"{saturday}T10:00:00+08:00",
        completed_at=f"{saturday}T10:00:02+08:00",
    )
    assert confirmation["status"] == SESSION_REJECTED
    assert any("record_trade_date_mismatch" in item for item in confirmation["confirmation_errors"])


def test_existing_confirmation_evidence_is_never_overwritten(tmp_path):
    target = tmp_path / "session.json"
    first = _confirmation()
    write_market_session_confirmation_json_atomic(target, first)
    before = confirmation_file_sha256(target)
    with pytest.raises(FileExistsError):
        write_market_session_confirmation_json_atomic(target, _confirmation())
    assert confirmation_file_sha256(target) == before


def test_powershell_state_machine_consumes_both_contracts(tmp_path):
    if POWERSHELL is None:
        pytest.skip("PowerShell is required for behavior test")
    calendar = _calendar()
    confirmation = _confirmation()
    calendar_path = tmp_path / "calendar.json"
    confirmation_path = tmp_path / "confirmation.json"
    fixture_path = tmp_path / "environment.json"
    output = tmp_path / "go.json"
    calendar_path.write_text(json.dumps(calendar, sort_keys=True), encoding="utf-8")
    confirmation_path.write_text(json.dumps(confirmation, sort_keys=True), encoding="utf-8")
    fixture_path.write_text(json.dumps(_environment(), sort_keys=True), encoding="utf-8")
    command = [
        POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(SCRIPT), "-Date", TRADE_DATE, "-ProjectRoot", str(ROOT),
        "-Output", str(output), "-CalendarContract", str(calendar_path),
        "-CalendarFileSha256", confirmation_file_sha256(calendar_path),
        "-SessionConfirmationContract", str(confirmation_path),
        "-SessionConfirmationFileSha256", confirmation_file_sha256(confirmation_path),
        "-TestOnlyEnvironmentFixture", str(fixture_path), "-PythonExe", sys.executable,
    ]
    environ = dict(os.environ)
    environ["A_STOCK_GO_NOGO_TEST_MODE"] = "1"
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=environ)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert payload["status"] == SAMPLING_GO
    assert payload["sampling_authorized"] is False
    _assert_safe(payload)


def test_v1_evidence_is_verified_audit_only_and_cannot_authorize():
    payload = {
        "evidence_schema_version": GO_NOGO_EVIDENCE_SCHEMA_V1,
        "verifier_contract_version": GO_NOGO_VERIFIER_CONTRACT_V1,
        "status": SAMPLING_GO,
        "execution_ok": True,
        "evidence_integrity_verified": False,
        "evidence_scope": "production",
        "sampling_authorized": True,
        "qualification_result": "NOT_EVALUATED",
        "consecutive_qualified_days": 0,
        "trade_date": TRADE_DATE,
        "started_at": f"{TRADE_DATE}T12:00:00+08:00",
        "completed_at": f"{TRADE_DATE}T12:00:01+08:00",
        "cutoff_clock": "13:30:00",
        "fixed_codes": list(FIXED_CODES),
        "source_versions": {
            capability: identity[2]
            for capability, identity in sorted(SOURCE_IDENTITIES.items())
        },
        "request_hashes": {capability: "1" * 64 for capability in SOURCE_IDENTITIES},
        "calendar_contract_hash": "2" * 64,
        "environment_checks": [{"name": "legacy_check", "passed": True, "detail": True}],
        "failure_reasons": [],
        "task_actions": [],
        "enabled_tasks": [],
        "go_nogo_evidence_hash": "",
        "automatic_configuration_change": False,
        "automatic_qualification_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    payload["go_nogo_evidence_hash"] = compute_go_nogo_evidence_hash(payload)
    anchor = _json_sha(payload)
    result = verify_market_source_go_nogo_evidence(
        payload, expected_file_sha256=anchor, actual_file_sha256=anchor
    )
    assert result["evidence_integrity_verified"] is True
    assert result["audit_only"] is True
    assert result["sampling_authorized"] is False
    _assert_safe(result)


def test_go_never_changes_qualification_count_or_trading_outputs():
    result = _run_go()
    assert result["consecutive_qualified_days"] == 0
    assert result["qualification_result"] == "NOT_EVALUATED"
    _assert_safe(result)
