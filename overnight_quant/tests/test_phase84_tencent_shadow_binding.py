from __future__ import annotations

import pytest

from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_BOUND,
    SOURCE_ADAPTER_REQUEST_INVALID,
    SourceProviderEnvelope,
    audit_source_adapters,
    execute_source_adapter,
    get_source_adapter_registry,
)


SOURCE_VERSION = "qt.gtimg.cn~88_fields_v2026-07-30"
QUOTE_PROVIDER_KEY = (
    "tencent_direct_http_providers.TencentDirectHttpProviders."
    "collect_quote_records"
)
VALUATION_PROVIDER_KEY = (
    "tencent_direct_http_providers.TencentDirectHttpProviders."
    "collect_valuation_records"
)
LEGACY_PROVIDER_KEY = "astock_client.AStockClient._tencent_quotes"
EXPECTED_REGISTRY_HASH = (
    "61758c08282a12b0c68d07bc46155dfe5848d1e44bf9a42ac96276e51642bd39"
)


def _record(capability: str) -> dict:
    return {
        "capability": capability,
        "origin_source": "tencent",
        "adapter": "direct_http",
        "source_version": SOURCE_VERSION,
        "event_time": "2026-08-25T14:49:00+08:00",
        "observed_at": "2026-08-25T14:49:01+08:00",
        "available_at": "2026-08-25T14:49:02+08:00",
        "request_hash": "a" * 64,
        "raw_hash": "b" * 64,
        "payload": {"code": "000001"},
    }


def _assert_safe(result: dict, *, provider_called: bool | None) -> None:
    if provider_called is not None:
        assert result["provider_called"] is provider_called
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["automatic_configuration_change"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def test_shadow_registry_activates_only_two_tencent_bindings():
    audit = audit_source_adapters(environ={})
    bindings = [
        row
        for row in get_source_adapter_registry()
        if row["origin_source"] == "tencent"
        and row["capability"] in {"quote", "valuation"}
    ]

    assert audit["adapter_registry_hash"] == EXPECTED_REGISTRY_HASH
    assert audit["bound_count"] == 2
    assert audit["candidate_count"] == 0
    assert audit["legacy_implementation_present_count"] == 13
    assert [row["capability"] for row in bindings] == ["quote", "valuation"]
    assert {row["provider_key"] for row in bindings} == {
        QUOTE_PROVIDER_KEY,
        VALUATION_PROVIDER_KEY,
    }
    assert all(row["candidate_provider_key"] == "" for row in bindings)
    assert {row["legacy_provider_key"] for row in bindings} == {
        LEGACY_PROVIDER_KEY
    }
    assert all(row["implementation_status"] == "bound" for row in bindings)
    _assert_safe(audit, provider_called=None)


@pytest.mark.parametrize(
    ("capability", "provider_key"),
    [
        ("quote", QUOTE_PROVIDER_KEY),
        ("valuation", VALUATION_PROVIDER_KEY),
    ],
)
def test_exact_explicit_envelope_calls_shadow_provider_once(
    capability: str,
    provider_key: str,
):
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return [_record(capability)]

    result = execute_source_adapter(
        capability,
        origin_source="tencent",
        adapter="direct_http",
        source_version=SOURCE_VERSION,
        provider_envelope=SourceProviderEnvelope(
            provider_key=provider_key,
            provider_callable=provider,
        ),
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_BOUND
    assert calls == 1
    _assert_safe(result, provider_called=True)


@pytest.mark.parametrize(
    "wrong_key",
    [LEGACY_PROVIDER_KEY, VALUATION_PROVIDER_KEY, "tests.Unrelated.provider"],
)
def test_wrong_or_legacy_provider_key_is_rejected_before_call(wrong_key: str):
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return [_record("quote")]

    result = execute_source_adapter(
        "quote",
        origin_source="tencent",
        adapter="direct_http",
        source_version=SOURCE_VERSION,
        provider_envelope=SourceProviderEnvelope(
            provider_key=wrong_key,
            provider_callable=provider,
        ),
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_REQUEST_INVALID
    assert result["rejection_reasons"] == [
        "source_adapter_provider_key_mismatch"
    ]
    assert calls == 0
    _assert_safe(result, provider_called=False)


@pytest.mark.parametrize("provider_envelope", [None, lambda: [_record("quote")]])
def test_missing_or_bare_provider_envelope_cannot_trigger_network(
    provider_envelope,
):
    result = execute_source_adapter(
        "quote",
        origin_source="tencent",
        adapter="direct_http",
        source_version=SOURCE_VERSION,
        provider_envelope=provider_envelope,
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_REQUEST_INVALID
    assert result["rejection_reasons"] == ["source_provider_envelope_required"]
    assert result["network_requests_made"] == 0
    _assert_safe(result, provider_called=False)
