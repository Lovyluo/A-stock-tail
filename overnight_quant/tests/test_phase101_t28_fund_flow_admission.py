from __future__ import annotations

from copy import deepcopy
import json

import pytest

from overnight_quant.data.close_confirmation_readiness import (
    materialize_point_in_time_records, normalize_point_in_time_records,
)
from overnight_quant.data.market_source_providers import (
    SOURCE_IDENTITIES, V5_CAPABILITY_REGISTRY_HASH, V5_VERIFIER_CONTRACT_HASH,
    MarketSourceContractError, validate_market_source_records,
)
from overnight_quant.data.source_capability_adapters import (
    SourceAdapterBinding, SourceProviderEnvelope, execute_source_adapter,
    _execute_source_adapter_with_bindings_for_test,
)
from overnight_quant.data.t24_offline_rehearsal import run_offline_rehearsal, synthetic_fixture
from overnight_quant.strategy.close_confirmation_v1.features import build_close_confirmation_features
from overnight_quant.scripts.run_market_source_evidence_verify import (
    EVIDENCE_INVALID, EVIDENCE_VERIFIED, verify_file, verify_market_source_evidence,
)
from overnight_quant.scripts.run_market_source_validation import file_sha256
from overnight_quant.tests.test_phase89_market_source_qualification import (
    CUTOFF, DEADLINE, TRADE_DATE, complete_evidence, fund_responses,
    provider, resign_evidence, response,
)


def _safe(result):
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_normal_response_only_emits_raw_audit_no_incremental_or_formal_records():
    batch = provider(fund_responses()).collect_fund_flow_batch()
    assert batch.records == ()
    assert batch.status == "FUND_FLOW_SEMANTICS_UNPROVEN"
    assert len(batch.audit_records) == 5
    for row in batch.audit_records:
        payload = row["payload"]
        assert row["data_type"] == "fund_flow_audit"
        assert payload["amount_unit"] == "CNY"
        assert payload["raw_values"] == ["100", "10", "20", "30", "40"]
        assert payload["eligible_for_hard_gate"] is False
        for field in ("value_semantics", "aggregation_semantics", "minute_interval_semantics", "accumulation_start", "revision_semantics"):
            assert payload[field] == "UNPROVEN"
    assert not validate_market_source_records("fund_flow", batch.audit_records)["valid"]
    assert provider(fund_responses()).collect_fund_flow_records() == []


@pytest.mark.parametrize("value", [-100, 0, 100])
def test_signed_values_preserved_without_sum_or_difference(value):
    values = fund_responses()
    from overnight_quant.data.market_source_providers import EASTMONEY_FUND_FLOW_URL
    values[0] = response({"data": {"klines": [f"{TRADE_DATE} 14:50,{value},10,20,30,40,0"]}}, EASTMONEY_FUND_FLOW_URL)
    payload = provider(values).collect_fund_flow_batch().audit_records[0]["payload"]
    assert payload["main_net"] == value
    assert payload["raw_values"][0] == str(value)
    assert payload["value_semantics"] == "UNPROVEN"


@pytest.mark.parametrize("value", ["", "-", "null", "nan"])
def test_missing_value_not_zero_filled(value):
    from overnight_quant.data.market_source_providers import EASTMONEY_FUND_FLOW_URL
    values = fund_responses()
    values[0] = response({"data": {"klines": [f"{TRADE_DATE} 14:50,{value},10,20,30,40,0"]}}, EASTMONEY_FUND_FLOW_URL)
    with pytest.raises(MarketSourceContractError):
        provider(values).collect_fund_flow_batch()


@pytest.mark.parametrize("field,value", [
    ("value_semantics", "incremental"), ("aggregation_semantics", "incremental"),
    ("eligible_for_hard_gate", True), ("minute_interval_semantics", "one_minute"),
    ("accumulation_start", "09:30"),
])
def test_resigned_audit_semantics_cannot_promote_admission(tmp_path, monkeypatch, field, value):
    evidence = deepcopy(complete_evidence(tmp_path, monkeypatch))
    evidence["audit_records_by_capability"]["fund_flow"][0]["payload"][field] = value
    result = verify_market_source_evidence(resign_evidence(evidence), expected_file_sha256="e" * 64)
    assert result["status"] == EVIDENCE_INVALID
    assert "fund_flow:audit_records_replay_mismatch" in result["errors"]
    assert result["qualification_eligible"] is False
    _safe(result)


@pytest.mark.parametrize("mutation", ["formal", "summary", "status"])
def test_resigned_formal_or_qualification_claim_rejected(tmp_path, monkeypatch, mutation):
    evidence = deepcopy(complete_evidence(tmp_path, monkeypatch))
    if mutation == "formal":
        evidence["records_by_capability"]["fund_flow"] = evidence["audit_records_by_capability"]["fund_flow"]
    elif mutation == "summary":
        evidence["provider_validation_passed"] = evidence["provider_contract_complete"] = True
        evidence["qualification_status"] = "PM_REVIEW_REQUIRED"
    else:
        evidence["status"] = "MARKET_SOURCE_NETWORK_VALIDATED"
    result = verify_market_source_evidence(resign_evidence(evidence), expected_file_sha256="e" * 64)
    assert result["status"] == EVIDENCE_INVALID
    _safe(result)


def test_integrity_double_replay_is_distinct_from_provider_admission(tmp_path, monkeypatch):
    evidence = complete_evidence(tmp_path, monkeypatch)
    path = tmp_path / "complete.json"
    anchor = file_sha256(path)
    first = verify_file(path, expected_file_sha256=anchor)
    second = verify_file(path, expected_file_sha256=anchor)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["status"] == EVIDENCE_VERIFIED
    assert first["provider_validation_passed"] is False
    assert first["provider_admission_passed"] is False
    assert first["qualification_eligible"] is False
    assert evidence["transport_qualified"] is True
    assert evidence["records_by_capability"]["fund_flow"] == []
    assert file_sha256(path) == anchor
    _safe(first)


def test_historical_v5_integrity_is_audit_only_never_current_admission(tmp_path, monkeypatch):
    evidence = deepcopy(complete_evidence(tmp_path, monkeypatch))
    evidence["evidence_schema_version"] = "market_source_evidence_v5"
    evidence["provider_verifier_contract_hash"] = V5_VERIFIER_CONTRACT_HASH
    evidence["capability_registry_hash"] = V5_CAPABILITY_REGISTRY_HASH
    result = verify_market_source_evidence(resign_evidence(evidence), expected_file_sha256="e" * 64)
    assert result["status"] == EVIDENCE_VERIFIED
    assert result["audit_only"] is True
    assert result["provider_admission_passed"] is False
    assert result["qualification_eligible"] is False
    _safe(result)


@pytest.mark.parametrize("version", [SOURCE_IDENTITIES["fund_flow"][2], "push2_fflow_kline_dotnet_v2026-09-30", "forged_verified_v999"])
def test_old_and_current_records_excluded_from_normalization_and_materialization(version):
    records = deepcopy(list(provider(fund_responses()).collect_fund_flow_batch().audit_records))
    for row in records:
        row["data_type"] = "fund_flow"
        row["source_version"] = version
        row["payload"]["value_semantics"] = "incremental"
        row["payload"]["eligible_for_hard_gate"] = True
    accepted, rejected, _ = normalize_point_in_time_records(records, DEADLINE)
    assert accepted == []
    assert len(rejected) == 5
    assert {row["pit_reject_reason"] for row in rejected} == {"fund_flow_semantics_unproven"}
    materialized = materialize_point_in_time_records(records)
    assert materialized["stocks"] == []
    assert len(materialized["audit_records"]) == 5


def test_public_candidate_no_provider_call_or_formal_output():
    calls = []
    origin, adapter, version = SOURCE_IDENTITIES["fund_flow"]
    envelope = SourceProviderEnvelope("test.Provider.collect", lambda: calls.append(1))
    result = execute_source_adapter("fund_flow", origin_source=origin, adapter=adapter, source_version=version, provider_envelope=envelope)
    assert result["status"] != "SOURCE_ADAPTER_BOUND"
    assert result["provider_called"] is False
    assert calls == []
    _safe(result)


def test_test_binding_cannot_promote_real_unproven_records():
    origin, adapter, version = SOURCE_IDENTITIES["fund_flow"]
    key = "test.Provider.collect"
    binding = SourceAdapterBinding("fund_flow", origin, adapter, version, key, "", "", implementation_status="bound")
    records = deepcopy(list(provider(fund_responses()).collect_fund_flow_batch().audit_records))
    for row in records:
        row["payload"]["value_semantics"] = "incremental"
        row["payload"]["eligible_for_hard_gate"] = True
    result = _execute_source_adapter_with_bindings_for_test("fund_flow", origin_source=origin, adapter=adapter, source_version=version, provider_envelope=SourceProviderEnvelope(key, lambda: records), entries=[binding])
    assert result["status"] == "SOURCE_ADAPTER_PROVENANCE_REJECTED"
    assert result["provider_called"] is True
    assert result["record_count"] == 0
    _safe(result)


def test_synthetic_rehearsal_stays_test_only_not_real_readiness():
    result = run_offline_rehearsal(synthetic_fixture())
    assert result["synthetic_readiness_passed"] is True
    assert result["test_only"] is True
    assert result["network_requests_made"] == 0
    _safe(result)


def test_direct_features_cannot_sum_unproven_historical_values():
    row = provider(fund_responses()).collect_fund_flow_batch().audit_records[0]
    flat = {**row["payload"], **{key: value for key, value in row.items() if key != "payload"}}
    flat["aggregation_semantics"] = "incremental"
    flat["eligible_for_hard_gate"] = True
    result = build_close_confirmation_features({"fund_flow": [flat] * 5}, decision_time=DEADLINE)
    assert result["formal_fund_flow_count"] == 0


def test_production_adapter_rejects_explicit_synthetic_records():
    row = next(row for row in synthetic_fixture() if row["capability"] == "quote")
    result = execute_source_adapter("quote", origin_source=row["origin_source"], adapter=row["adapter"], source_version=row["source_version"], provider_envelope=SourceProviderEnvelope("tencent_direct_http_providers.TencentDirectHttpProviders.collect_quote_records", lambda: [row]))
    assert result["status"] == "SOURCE_ADAPTER_PROVENANCE_REJECTED"
    assert result["rejection_reasons"][0]["reason"] == "synthetic_record_forbidden_in_production"
    _safe(result)
