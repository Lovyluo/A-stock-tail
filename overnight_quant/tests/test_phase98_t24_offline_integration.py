from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random
import socket
import subprocess
import sys

import pytest
import requests

from overnight_quant.data.close_confirmation_readiness import materialize_point_in_time_records
from overnight_quant.data.s2_integration_contract import (
    FUND_BLOCKER, INDUSTRY_BLOCKER, _difference_cumulative_for_test,
    phase_budget, security_identity, simulate_phased_execution,
    validate_fund_window, validate_record,
)
from overnight_quant.data.t24_offline_rehearsal import (
    FIXTURE_DATE, FIXTURE_TIMES, run_offline_rehearsal, synthetic_fixture,
)
from overnight_quant.data.source_capability_adapters import (
    SourceProviderEnvelope, execute_source_adapter, get_source_adapter_registry,
)


@pytest.fixture
def records():
    return synthetic_fixture()


def _industry(rows):
    return next(row for row in rows if row["data_type"] == "industry")


def _safe(result):
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["qualification"] == "0/3"
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_actual_adapter_pit_readiness_feature_chain_without_quote_industry(records, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    original = deepcopy(records)
    result = run_offline_rehearsal(records)
    assert result["status"] == "OFFLINE_CHAIN_EXECUTABLE"
    assert result["stock_count"] == 5
    assert result["synthetic_readiness_passed"] is True
    assert result["network_requests_made"] == 0
    assert records == original
    assert all("industry_name" not in row["payload"] for row in records if row["data_type"] == "quote")
    assert result["stock_associations"]["000333"]["board_code"] == "BK1239"
    for features in result["features"].values():
        assert features["feature_event_cutoff"] == FIXTURE_TIMES["feature_cutoff"]
        assert features["execution_not_before"] == FIXTURE_TIMES["execution_start"]
        assert features["feature_availability"]["minute"] is True
        assert features["feature_availability"]["industry"] is True
        assert features["catalyst_score"] == 0
        assert features["stock_relative_to_industry_pct"] == 1.8
        assert features["formal_fund_flow_count"] == 5
    _safe(result)


@pytest.mark.parametrize("code,identity", [("000333", "SZ:000333"), ("SZ000333", "SZ:000333"), ("000333.sz", "SZ:000333"), ("SH600519", "SH:600519")])
def test_normalized_security_identity(code, identity):
    assert security_identity(code) == identity


@pytest.mark.parametrize("code", ["SH000333", "foo", "00033", 333])
def test_invalid_security_identity_rejected(code):
    with pytest.raises(ValueError):
        security_identity(code)


@pytest.mark.parametrize("field,value,reason", [
    ("board_code", "BK9999", "industry_mapping_board_mismatch"),
    ("security_identity", "SH:600000", "industry_mapping_security_mismatch"),
    ("classification_system", "different", "industry_mapping_classification_mismatch"),
    ("classification_version", "old", "industry_mapping_classification_mismatch"),
    ("raw_hash", "x", "industry_mapping_evidence_invalid"),
    ("source_version", "other", "industry_mapping_source_mismatch"),
])
def test_mapping_rejects_wrong_identity_evidence_or_classification(records, field, value, reason):
    _industry(records)["payload"]["association_contract"][field] = value
    result = run_offline_rehearsal(records)
    assert reason in result["errors"]
    _safe(result)


def test_missing_and_conflicting_mapping_fail_closed(records):
    missing = deepcopy(records)
    del _industry(missing)["payload"]["association_contract"]
    assert "industry_mapping_missing" in run_offline_rehearsal(missing)["errors"]
    duplicate = deepcopy(_industry(records))
    duplicate["payload"]["board_code"] = "BK9999"
    duplicate["payload"]["association_contract"]["board_code"] = "BK9999"
    result = run_offline_rehearsal([*records, duplicate])
    assert any("industry_mapping_conflict" in reason for reason in result["errors"])
    _safe(result)


def test_normalized_quote_code_uses_evidenced_mapping(records):
    quote = next(row for row in records if row["data_type"] == "quote")
    quote["payload"]["code"] = "SZ000001"
    result = run_offline_rehearsal(records)
    assert result["status"] == "OFFLINE_CHAIN_EXECUTABLE"


def test_legacy_name_association_still_works_and_mixing_fails(records):
    old = [
        {"data_type": "quote", "payload": {"code": "000001", "industry_name": "bank"}},
        {"data_type": "industry", "payload": {"name": "bank", "change_pct": 1}},
    ]
    assert materialize_point_in_time_records(old)["stocks"][0]["industry"]["change_pct"] == 1
    result = run_offline_rehearsal([*records, old[1]])
    assert result["status"] == "OFFLINE_INTEGRATION_REJECTED"
    _safe(result)


@pytest.mark.parametrize("mutation,reason", [
    (lambda row: row["payload"].pop("change_pct"), "industry_strength_missing"),
    (lambda row: row["payload"]["field_units"].update(change_pct="ratio"), "industry_strength_unit_invalid"),
    (lambda row: row["payload"].update(field_units=[]), "industry_strength_unit_invalid"),
    (lambda row: row.update(event_time=f"{FIXTURE_DATE}T14:50:00.001+08:00"), "integration_future_event"),
    (lambda row: row.update(decision_cutoff=FIXTURE_TIMES["feature_cutoff"]), "decision_cutoff_must_authorize_decision_time"),
    (lambda row: row.pop("published_at"), "published_at_key_missing"),
    (lambda row: row.update(published_at=""), "integration_temporal_fields_invalid"),
    (lambda row: row.update(available_at=f"{FIXTURE_DATE}T14:51:05.001+08:00"), "integration_record_time_order_invalid"),
    (lambda row: row.update(observed_at=f"{FIXTURE_DATE}T14:49:59+08:00"), "integration_record_time_order_invalid"),
])
def test_strict_fields_and_four_stage_time_contract(records, mutation, reason):
    mutation(_industry(records))
    result = run_offline_rehearsal(records)
    assert reason in result["errors"]
    _safe(result)


@pytest.mark.parametrize("native,reason", [
    (f"{FIXTURE_DATE}T14:50:00.001+08:00", "child_native_event_invalid"),
    ("2026-09-17T14:50:00+08:00", "child_native_event_invalid"),
])
def test_child_native_precision_and_dates_are_not_hidden_by_maximum(records, native, reason):
    row = _industry(records)
    row["child_provenance"] = [{
        "request_started_at": f"{FIXTURE_DATE}T14:50:00+08:00",
        "request_completed_at": f"{FIXTURE_DATE}T14:50:01+08:00",
        "available_at": f"{FIXTURE_DATE}T14:50:01+08:00",
        "native_event_times": [native, FIXTURE_TIMES["feature_cutoff"]],
        "time_semantics": "native_feature_event", "request_hash": "a" * 64, "raw_hash": "b" * 64,
    }]
    assert reason in validate_record(row)


def test_non_news_nullable_publication_does_not_authorize_news(records):
    row = deepcopy(_industry(records))
    assert validate_record(row) == []
    row["data_type"] = "news"
    assert "published_at_required" in validate_record(row)


def test_snapshot_cannot_drop_split_time_contract(records):
    from overnight_quant.data.close_confirmation_readiness import normalize_point_in_time_records
    accepted, rejected, _ = normalize_point_in_time_records(records, FIXTURE_TIMES["decision_time"])
    assert not accepted
    assert all("integration_snapshot_time_contract_missing" in row["integration_errors"] for row in rejected)


def test_real_candidate_unknown_semantics_cannot_be_made_ready(records):
    row = _industry(records)
    row["test_only"] = False
    assert INDUSTRY_BLOCKER in validate_record(row)
    fund = next(row for row in records if row["data_type"] == "fund_flow")
    fund["test_only"] = False
    assert FUND_BLOCKER in validate_record(fund)
    for capability in ("industry_snapshot", "fund_flow", "market_breadth"):
        binding = next(row for row in get_source_adapter_registry() if row["capability"] == capability and row["origin_source"] == "eastmoney" and row["adapter"] == "direct_http")
        def forbidden():
            raise AssertionError("candidate must not execute")
        result = execute_source_adapter(
            **{key: binding[key] for key in ("capability", "origin_source", "adapter", "source_version")},
            provider_envelope=SourceProviderEnvelope(provider_key=binding["candidate_provider_key"], provider_callable=forbidden),
        )
        assert result["status"] == "SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED"
        assert result["provider_called"] is False


def test_cumulative_values_need_baseline_and_cannot_be_summed_directly(records):
    rows = [deepcopy(row) for row in records if row["data_type"] == "fund_flow" and row["payload"]["code"] == "000001"]
    baseline = deepcopy(rows[0])
    baseline["event_time"] = f"{FIXTURE_DATE}T14:45:00+08:00"
    cumulative = [baseline, *rows]
    for index, row in enumerate(cumulative):
        row["payload"].update(aggregation_semantics="cumulative", main_net=1000 + index * 100, large_net=600 + index * 60, super_net=400 + index * 40)
    assert "fund_flow_not_verified_incremental" in validate_fund_window(cumulative[1:], FIXTURE_TIMES["feature_cutoff"])
    converted = _difference_cumulative_for_test(cumulative, target=FIXTURE_TIMES["feature_cutoff"])
    assert sum(row["payload"]["main_net"] for row in converted) == 500
    assert sum(row["payload"]["main_net"] for row in cumulative[1:]) == 6500
    assert validate_fund_window(converted, FIXTURE_TIMES["feature_cutoff"]) == []
    for invalid in (cumulative[1:], [*cumulative[:-1], cumulative[-2]]):
        with pytest.raises(ValueError):
            _difference_cumulative_for_test(invalid, target=FIXTURE_TIMES["feature_cutoff"])
    cumulative[0]["event_time"] = "2026-09-17T14:45:00+08:00"
    with pytest.raises(ValueError):
        _difference_cumulative_for_test(cumulative, target=FIXTURE_TIMES["feature_cutoff"])


def test_missing_target_and_duplicate_interval_never_use_older_row(records):
    absent = [row for row in records if not (row["data_type"] == "fund_flow" and row["event_time"] == FIXTURE_TIMES["feature_cutoff"])]
    result = run_offline_rehearsal(absent)
    assert "FUND_FLOW_TARGET_ROW_NOT_PUBLISHED" in result["errors"]
    _safe(result)
    fund = next(row for row in records if row["data_type"] == "fund_flow")
    result = run_offline_rehearsal([*records, deepcopy(fund)])
    assert "fund_flow_five_interval_window_invalid" in result["errors"]


def test_phase_budget_accounts_for_23_requests_wait_startup_and_freeze():
    params = {"started_at": "2026-09-18T14:39:30+08:00", "cutoff": "2026-09-18T14:40:00+08:00", "deadline": "2026-09-18T14:40:10+08:00"}
    serial = phase_budget(**params)
    assert serial["request_count"] == 23
    assert serial["worst_elapsed_ms"] == 48800
    assert serial["feasible_under_assumptions"] is False
    concurrent = phase_budget(**params, workers=4)
    assert concurrent["fund_stage_started_at"] == params["cutoff"]
    assert concurrent["remaining_margin_ms"] == 5300
    assert concurrent["scheduling_authorized"] is False
    assert phase_budget(**params, workers=4, publication_delay_ms=5301)["feasible_under_assumptions"] is False
    assert phase_budget(**params, workers=4, industry_pages=80)["feasible_under_assumptions"] is False
    with pytest.raises(ValueError):
        phase_budget(**{**params, "started_at": params["cutoff"]})


@pytest.mark.parametrize("durations,published,error", [
    ([100] * 23, "2026-09-18T14:40:00.200+08:00", ""),
    ([100] * 23, None, "FUND_FLOW_TARGET_ROW_NOT_PUBLISHED"),
    ([100] * 23, "2026-09-18T14:40:10.001+08:00", "FUND_FLOW_TARGET_ROW_NOT_PUBLISHED"),
    ([2001] + [100] * 22, "2026-09-18T14:40:00+08:00", "REQUEST_HARD_DEADLINE_EXCEEDED"),
    ([100] * 18 + [2000] * 5, "2026-09-18T14:40:00+08:00", "REQUEST_HARD_DEADLINE_EXCEEDED"),
])
def test_virtual_clock_executes_phases_and_rejects_missing_target_or_timeout(durations, published, error):
    result = simulate_phased_execution(
        started_at="2026-09-18T14:39:30+08:00", cutoff="2026-09-18T14:40:00+08:00",
        deadline="2026-09-18T14:40:10+08:00", durations_ms=durations, target_available_at=published,
    )
    assert result["error_code"] == error
    assert result["network_requests_made"] == 0
    assert result["data_ready"] is False
    if not error:
        assert [phase["phase"] for phase in result["phases"]] == [
            "market_and_industry", "wait_for_exact_fund_target", "five_stock_fund_flow", "validate_and_freeze",
        ]


def test_virtual_pagination_budget_is_not_silently_extended():
    result = simulate_phased_execution(
        started_at="2026-09-18T14:39:30+08:00", cutoff="2026-09-18T14:40:00+08:00",
        deadline="2026-09-18T14:40:10+08:00", durations_ms=[1900] * 23,
        target_available_at="2026-09-18T14:40:00+08:00",
    )
    assert result["error_code"] == "PHASE_BUDGET_EXHAUSTED"
    with pytest.raises(ValueError, match="request_plan_changed"):
        simulate_phased_execution(
            started_at="2026-09-18T14:39:30+08:00", cutoff="2026-09-18T14:40:00+08:00",
            deadline="2026-09-18T14:40:10+08:00", durations_ms=[100] * 20,
            target_available_at=None,
        )


def test_order_is_deterministic_and_cli_runs_from_any_directory(records, tmp_path):
    first = run_offline_rehearsal(records)
    random.Random(24).shuffle(records)
    second = run_offline_rehearsal(records)
    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first["feature_hash"] == second["feature_hash"]
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_t24_offline_rehearsal.py"
    outputs = [subprocess.run([sys.executable, str(script), "--synthetic-test-only"], cwd=tmp_path, capture_output=True, check=True).stdout for _ in range(2)]
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0])["network_requests_made"] == 0
    assert json.loads(outputs[0])["data_ready"] is False
    report = tmp_path / "synthetic_report.json"
    command = [sys.executable, str(script), "--synthetic-test-only", "--output", str(report)]
    subprocess.run(command, cwd=tmp_path, capture_output=True, check=True)
    original = report.read_bytes()
    repeated = subprocess.run(command, cwd=tmp_path, capture_output=True)
    assert repeated.returncode != 0
    assert report.read_bytes() == original
