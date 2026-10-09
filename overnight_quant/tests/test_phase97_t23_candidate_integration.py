from __future__ import annotations

from copy import deepcopy
import json

import pytest

from overnight_quant.data import market_source_go_nogo as go_nogo
from overnight_quant.data.eastmoney_derived_industry_breadth import SOURCE_VERSION
from overnight_quant.data.market_source_providers import (
    SOURCE_IDENTITIES, PROVIDER_KEYS, V4_EVIDENCE_SCHEMA_VERSION,
    V4_VERIFIER_CONTRACT_HASH, validate_market_source_records,
)
from overnight_quant.data.source_capability_adapters import (
    audit_source_adapters, execute_source_adapter, SourceProviderEnvelope,
)
from overnight_quant.data.source_capability_registry import validate_source_provenance_batch
from overnight_quant.scripts.run_market_source_evidence_verify import verify_market_source_evidence
from overnight_quant.scripts.run_market_source_validation import compute_evidence_hash
from overnight_quant.tests.test_phase89_market_source_qualification import (
    provider, industry_responses,
)


def test_new_candidate_records_and_provenance_use_native_derived_contract():
    batch = provider(industry_responses()).collect_industry_batch()
    assert len(batch.responses) == 9 and len(batch.records) == 5
    assert {row["source_version"] for row in batch.records} == {SOURCE_VERSION}
    assert SOURCE_IDENTITIES["industry_snapshot"][2] == SOURCE_VERSION
    assert validate_market_source_records("industry_snapshot", batch.records)["valid"]
    result = validate_source_provenance_batch("industry_snapshot", batch.records, require_hard_gate=False, environ={})
    assert result["status"] == "SOURCE_PROVENANCE_ACCEPTED"
    assert result["hard_gate_authorized"] is False
    assert all("change_pct" not in row["payload"] for row in batch.records)


def test_candidate_integration_cannot_activate_or_call_any_provider():
    calls = []
    result = execute_source_adapter(
        "industry_snapshot", origin_source="eastmoney", adapter="direct_http", source_version=SOURCE_VERSION,
        provider_envelope=SourceProviderEnvelope(PROVIDER_KEYS["industry_snapshot"], lambda: calls.append(1)),
    )
    assert result["status"] == "SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED"
    assert calls == [] and result["hard_gate_authorized"] is False
    audit = audit_source_adapters(environ={})
    assert audit["adapter_entry_count"] == 32
    assert audit["bound_count"] == 8 and audit["candidate_count"] == 7
    assert audit["network_requests_made"] == 0
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


@pytest.mark.parametrize("field,value", [
    ("invalid_count", 1), ("valid_count", 99), ("breadth_ratio", .25),
    ("mapping_time_semantics", "mapping_is_feature_event"),
    ("raw_board_event_time", "2026-09-18T14:50:00.001+08:00"),
    ("board_code", "BK9999"), ("page_raw_hashes", ["bad"]),
])
def test_resigned_new_candidate_record_contract_is_fail_closed(field, value):
    records = deepcopy(list(provider(industry_responses()).collect_industry_batch().records))
    records[0]["payload"][field] = value
    assert not validate_market_source_records("industry_snapshot", records)["valid"]


def test_go_nogo_requires_five_exact_mappings_and_four_constituent_routes():
    specs = go_nogo.OFFICIAL_ENDPOINT_REQUESTS["industry_snapshot"]
    assert len(specs) == 9
    assert [spec[2]["code"] for spec in specs[:5]] == [
        "SZ000001", "SZ000333", "SH600000", "SH600519", "SH601318"
    ]
    assert [spec[2]["fs"] for spec in specs[5:]] == [
        "b:BK0474", "b:BK0475", "b:BK1239", "b:BK1277"
    ]
    raw = json.dumps({"ssbk": [{"SECURITY_CODE": "000333", "BOARD_CODE": "1239", "BOARD_NAME": "白色家电", "BOARD_RANK": 2}]}).encode()
    assert go_nogo._preflight_response_valid("industry_mapping:000333", raw)
    assert not go_nogo._preflight_response_valid("industry_mapping:600519", raw)


def test_v4_keeps_frozen_audit_contract_and_cannot_qualify_new_source():
    evidence = {
        "evidence_schema_version": V4_EVIDENCE_SCHEMA_VERSION,
        "provider_verifier_contract_hash": V4_VERIFIER_CONTRACT_HASH,
        "capability_registry_hash": "09b82c2b542628c3eb920fb0c91dc50bcc5bdb3b601060c4c8edac1f15d5a79d",
        "provider_keys": PROVIDER_KEYS, "requested_codes": list(go_nogo.FIXED_CODES),
        "data_ready": False, "hard_gate_authorized": False,
        "automatic_configuration_change": False, "automatic_qualification_change": False,
        "candidates": [], "tickets": [], "orders": [],
    }
    evidence["evidence_hash"] = compute_evidence_hash(evidence)
    result = verify_market_source_evidence(evidence, expected_file_sha256="a" * 64)
    assert result["evidence_integrity_verified"] and result["audit_only"]
    assert result["qualification_eligible"] is False
