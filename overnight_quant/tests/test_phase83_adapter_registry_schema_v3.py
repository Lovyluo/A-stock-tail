from __future__ import annotations

from copy import deepcopy
import random

import pytest

from overnight_quant.data import source_capability_adapters as adapters
from overnight_quant.data.source_capability_adapters import (
    ADAPTER_REGISTRY_HASH_CHANGE_REASON,
    ADAPTER_REGISTRY_SCHEMA_VERSION,
    PREVIOUS_ADAPTER_REGISTRY_HASH,
    PREVIOUS_ADAPTER_REGISTRY_SCHEMA_VERSION,
    SOURCE_ADAPTER_BOUND,
    SourceAdapterBinding,
    SourceProviderEnvelope,
    audit_source_adapters,
    compute_source_adapter_registry_hash,
    execute_source_adapter,
    get_source_adapter_registry,
)
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
)


EXPECTED_B1_HASH = (
    "4f63ab273dc8cc98363d7043a47fad10f6dcb002a006a4c88cab118c3ff4ecb5"
)
EXPECTED_CANDIDATE_V3_HASH = (
    "1eb8114cf3aa68bf85473a67513dfdd7a3ab5b32a464a66d5c4664163a4f8b2d"
)
EXPECTED_SHADOW_V3_HASH = (
    "05adafdb3e3a70ddcdc16644673b299b66d8c09367d2ef3259b7fc97b7adb369"
)
LEGACY_TENCENT_KEY = "astock_client.AStockClient._tencent_quotes"
QUOTE_CANDIDATE_KEY = (
    "tencent_direct_http_providers.TencentDirectHttpProviders."
    "collect_quote_records"
)
VALUATION_CANDIDATE_KEY = (
    "tencent_direct_http_providers.TencentDirectHttpProviders."
    "collect_valuation_records"
)
QUOTE_IDENTITY = {
    "capability": "quote",
    "origin_source": "tencent",
    "adapter": "direct_http",
    "source_version": "qt.gtimg.cn~88_fields_v2026-07-30",
}


def _assert_safe(result, *, provider_called: bool) -> None:
    assert result["provider_called"] is provider_called
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["automatic_configuration_change"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def _quote_record() -> dict:
    return {
        "capability": "quote",
        "origin_source": "tencent",
        "adapter": "direct_http",
        "source_version": "qt.gtimg.cn~88_fields_v2026-07-30",
        "event_time": "2026-08-25T14:49:00+08:00",
        "observed_at": "2026-08-25T14:49:01+08:00",
        "available_at": "2026-08-25T14:49:02+08:00",
        "request_hash": "a" * 64,
        "raw_hash": "b" * 64,
        "payload": {"code": "000001", "price": 10.0},
    }


def _tencent_shadow_rows() -> list[dict]:
    return [
        row
        for row in get_source_adapter_registry()
        if row["implementation_status"] == "bound"
        and row["origin_source"] == "tencent"
        and row["capability"] in {"quote", "valuation"}
    ]


def test_schema_v3_audit_has_complete_three_slot_matrix_and_fixed_counts():
    audit = audit_source_adapters(environ={})
    rows = audit["adapter_matrix"]

    assert ADAPTER_REGISTRY_SCHEMA_VERSION == (
        "source_capability_adapter_registry_v5"
    )
    assert audit["adapter_entry_count"] == len(rows) == 28
    assert audit["bound_count"] == 8
    assert audit["candidate_count"] == 1
    assert audit["legacy_implementation_present_count"] == 13
    assert audit["contract_incompatible_count"] == 4
    assert audit["previous_adapter_registry_schema_version"] == (
        PREVIOUS_ADAPTER_REGISTRY_SCHEMA_VERSION
    )
    assert PREVIOUS_ADAPTER_REGISTRY_SCHEMA_VERSION == (
        "source_capability_adapter_registry_v4"
    )
    assert PREVIOUS_ADAPTER_REGISTRY_HASH == (
        "5596301dc27041f79bde85a8987526e6beb5970a7eb5386a2928df9b7e2452b5"
    )
    assert audit["previous_adapter_registry_hash"] == (
        "5596301dc27041f79bde85a8987526e6beb5970a7eb5386a2928df9b7e2452b5"
    )
    assert audit["adapter_registry_hash_change_reason"] == (
        ADAPTER_REGISTRY_HASH_CHANGE_REASON
    )
    assert audit["production_adapter_registry_hash"] == EXPECTED_SHADOW_V3_HASH
    assert audit["selection_adapter_registry_hash"] == EXPECTED_SHADOW_V3_HASH
    assert audit["capability_registry_hash"] == EXPECTED_B1_HASH
    assert compute_source_capability_registry_hash() == EXPECTED_B1_HASH
    assert sum(bool(row["provider_key"]) for row in rows) == 8
    assert all(
        row["legacy_implementation_present"]
        is bool(row["legacy_provider_key"])
        for row in rows
    )
    assert audit["network_requests_made"] == 0
    assert audit["data_ready"] is False
    assert audit["hard_gate_authorized"] is False
    assert audit["candidates"] == []
    assert audit["tickets"] == []
    assert audit["orders"] == []


def test_tencent_quote_and_valuation_shadow_bindings_have_exact_slot_identity():
    rows = _tencent_shadow_rows()

    assert [row["capability"] for row in rows] == ["quote", "valuation"]
    assert {row["provider_key"] for row in rows} == {
        QUOTE_CANDIDATE_KEY,
        VALUATION_CANDIDATE_KEY,
    }
    assert {row["legacy_provider_key"] for row in rows} == {
        LEGACY_TENCENT_KEY
    }
    assert all(row["candidate_provider_key"] == "" for row in rows)
    assert all(row["implementation_status"] == "bound" for row in rows)
    assert all(
        row["status"] == SOURCE_ADAPTER_BOUND
        for row in audit_source_adapters(environ={})["adapter_matrix"]
        if row["capability"] in {"quote", "valuation"}
        and row["origin_source"] == "tencent"
    )


@pytest.mark.parametrize(
    ("capability", "candidate_key"),
    [
        ("quote", QUOTE_CANDIDATE_KEY),
        ("valuation", VALUATION_CANDIDATE_KEY),
    ],
)
def test_public_shadow_binding_calls_only_the_explicit_matching_provider(
    capability,
    candidate_key,
):
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        record = _quote_record()
        record["capability"] = capability
        return [record]

    result = execute_source_adapter(
        capability,
        origin_source="tencent",
        adapter="direct_http",
        source_version="qt.gtimg.cn~88_fields_v2026-07-30",
        provider_envelope=SourceProviderEnvelope(
            provider_key=candidate_key,
            provider_callable=provider,
        ),
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_BOUND
    assert result["binding"]["provider_key"] == candidate_key
    assert result["binding"]["candidate_provider_key"] == ""
    assert calls == 1
    _assert_safe(result, provider_called=True)


def test_shadow_binding_cannot_be_downgraded_by_private_rehash_or_patch(
    monkeypatch,
):
    rows = deepcopy(get_source_adapter_registry())
    quote = next(row for row in rows if row["capability"] == "quote")
    quote["candidate_provider_key"] = quote["provider_key"]
    quote["provider_key"] = ""
    quote["implementation_status"] = "candidate_not_activated"
    with pytest.raises(
        ValueError,
        match="source_adapter_candidate_binding_mismatch",
    ):
        adapters._compute_source_adapter_registry_hash_for_test(rows)

    monkeypatch.setattr(adapters, "SOURCE_ADAPTER_BINDINGS", tuple(rows))
    with pytest.raises(
        ValueError,
        match="source_adapter_candidate_binding_mismatch",
    ):
        compute_source_adapter_registry_hash()


def test_candidate_and_legacy_slots_cannot_share_the_same_key():
    with pytest.raises(
        ValueError,
        match="source_adapter_provider_key_slots_duplicate",
    ):
        SourceAdapterBinding(
            **QUOTE_IDENTITY,
            provider_key="",
            candidate_provider_key=QUOTE_CANDIDATE_KEY,
            legacy_provider_key=QUOTE_CANDIDATE_KEY,
            implementation_status="candidate_not_activated",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_provider_key", " "),
        ("candidate_provider_key", "bad key"),
        ("legacy_provider_key", " legacy.Provider"),
        ("provider_key", "provider.Key "),
    ],
)
def test_noncanonical_provider_slot_keys_are_rejected(field, value):
    values = {
        **QUOTE_IDENTITY,
        "provider_key": QUOTE_CANDIDATE_KEY,
        "candidate_provider_key": "",
        "legacy_provider_key": LEGACY_TENCENT_KEY,
        "implementation_status": "bound",
    }
    values[field] = value

    with pytest.raises(
        ValueError,
        match="source_adapter_provider_key_not_canonical",
    ):
        SourceAdapterBinding(**values)


def test_legacy_presence_is_derived_and_cannot_be_supplied_as_a_fact():
    values = {
        **QUOTE_IDENTITY,
        "provider_key": QUOTE_CANDIDATE_KEY,
        "candidate_provider_key": "",
        "legacy_provider_key": LEGACY_TENCENT_KEY,
        "implementation_status": "bound",
    }
    binding = SourceAdapterBinding(**values)
    assert binding.as_dict()["legacy_implementation_present"] is True

    with pytest.raises(TypeError):
        SourceAdapterBinding(
            **values,
            legacy_implementation_present=False,
        )
    row = binding.as_dict()
    row["legacy_implementation_present"] = False
    with pytest.raises(
        ValueError,
        match="source_adapter_derived_legacy_flag_mismatch",
    ):
        adapters._canonicalize_source_adapter_bindings_for_test([row])


def test_non_tencent_identity_cannot_inject_tencent_shadow_provider():
    row = next(
        deepcopy(item)
        for item in get_source_adapter_registry()
        if item["capability"] == "daily_bar_qfq"
    )
    row["provider_key"] = QUOTE_CANDIDATE_KEY
    row["candidate_provider_key"] = ""
    row["implementation_status"] = "bound"

    with pytest.raises(
        ValueError,
        match="source_adapter_candidate_identity_mismatch",
    ):
        adapters._canonicalize_source_adapter_bindings_for_test([row])


def test_production_matrix_rejects_subset_duplicate_and_added_identity():
    rows = get_source_adapter_registry()
    with pytest.raises(
        ValueError,
        match="production_source_adapter_binding_missing",
    ):
        adapters._validate_production_source_adapter_bindings(rows[:2])
    with pytest.raises(ValueError, match="source_adapter_binding_duplicate"):
        adapters._validate_production_source_adapter_bindings(rows + [rows[0]])
    added = deepcopy(rows[0])
    added["origin_source"] = "unknown_vendor"
    with pytest.raises(ValueError, match="source_adapter_identity_unknown"):
        adapters._validate_production_source_adapter_bindings(rows + [added])


def test_registry_order_is_hash_invariant_but_slot_changes_are_not():
    rows = get_source_adapter_registry()
    shuffled = deepcopy(rows)
    random.Random(83).shuffle(shuffled)
    assert (
        adapters._compute_source_adapter_registry_hash_for_test(shuffled)
        == EXPECTED_SHADOW_V3_HASH
    )

    changed = deepcopy(rows)
    quote = next(row for row in changed if row["capability"] == "quote")
    quote["provider_key"] = (
        "tests.AlternateTencentProvider.collect_quote_records"
    )
    changed_hash = adapters._compute_source_adapter_registry_hash_for_test(changed)
    assert changed_hash != EXPECTED_SHADOW_V3_HASH
    with pytest.raises(
        ValueError,
        match="production_source_adapter_binding_modified",
    ):
        adapters._validate_production_source_adapter_bindings(changed)


def test_test_only_bound_selection_hash_is_isolated_from_production():
    binding = SourceAdapterBinding(
        **QUOTE_IDENTITY,
        provider_key=QUOTE_CANDIDATE_KEY,
        candidate_provider_key="",
        legacy_provider_key="",
        implementation_status="bound",
    )
    result = adapters._execute_source_adapter_with_bindings_for_test(
        **QUOTE_IDENTITY,
        provider_envelope=SourceProviderEnvelope(
            provider_key=QUOTE_CANDIDATE_KEY,
            provider_callable=lambda: [_quote_record()],
        ),
        entries=[binding],
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_BOUND
    assert result["selection_registry_scope"] == "test_only"
    assert result["production_adapter_registry_hash"] == EXPECTED_SHADOW_V3_HASH
    assert result["selection_adapter_registry_hash"] != EXPECTED_SHADOW_V3_HASH
    _assert_safe(result, provider_called=True)
