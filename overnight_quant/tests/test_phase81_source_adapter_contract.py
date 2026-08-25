from __future__ import annotations

from copy import deepcopy
import inspect
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys

import pytest

from overnight_quant.data import source_capability_adapters as adapters
from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.minute_probe_sources import MootdxMinuteProbeCollectors
from overnight_quant.data.real_point_in_time_collectors import (
    RealPointInTimeCollectors,
)
from overnight_quant.data.source_capability_adapters import (
    ADAPTER_REGISTRY_SCHEMA_VERSION,
    SOURCE_ADAPTER_AUDIT_COMPLETE,
    SOURCE_ADAPTER_BOUND,
    SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED,
    SOURCE_ADAPTER_NOT_IMPLEMENTED,
    SOURCE_ADAPTER_PROVIDER_EMPTY,
    SOURCE_ADAPTER_PROVIDER_FAILED,
    SOURCE_ADAPTER_PROVENANCE_REJECTED,
    SOURCE_ADAPTER_REQUEST_INVALID,
    SOURCE_ADAPTER_ROUTE_REJECTED,
    SourceAdapterBinding,
    SourceProviderEnvelope,
    audit_source_adapters,
    compute_source_adapter_registry_hash,
    execute_source_adapter,
    get_source_adapter_registry,
)
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
    get_source_capability_registry,
)
from overnight_quant.scripts.run_source_adapter_audit import (
    run_source_adapter_audit,
)
from overnight_quant.strategy.news_briefing import fetch_cls_telegraph


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "overnight_quant" / "scripts" / "run_source_adapter_audit.py"
ADAPTER_REGISTRY_HASH = (
    "1eb8114cf3aa68bf85473a67513dfdd7a3ab5b32a464a66d5c4664163a4f8b2d"
)
QUOTE_IDENTITY = {
    "capability": "quote",
    "origin_source": "tencent",
    "adapter": "direct_http",
    "source_version": "qt.gtimg.cn~88_fields_v2026-07-30",
}
TEST_PROVIDER_KEY = "tests.contract_compatible_quote_v1"
VALUATION_IDENTITY = {
    "capability": "valuation",
    "origin_source": "tencent",
    "adapter": "direct_http",
    "source_version": "qt.gtimg.cn~88_fields_v2026-07-30",
}
VALUATION_TEST_PROVIDER_KEY = "tests.contract_compatible_valuation_v1"


LEGACY_PROVIDER_OBJECTS = {
    "astock_client.AStockClient._tencent_quotes": (
        AStockClient._tencent_quotes,
        "dict[str, dict]",
    ),
    (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_trading_calendar"
    ): (RealPointInTimeCollectors.collect_trading_calendar, "ProviderBatch"),
    (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_qfq_daily_bars"
    ): (RealPointInTimeCollectors.collect_qfq_daily_bars, "ProviderBatch"),
    (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_industry"
    ): (RealPointInTimeCollectors.collect_industry, "ProviderBatch"),
    (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_eastmoney_fund_flow"
    ): (RealPointInTimeCollectors.collect_eastmoney_fund_flow, "ProviderBatch"),
    (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_sina_fund_flow"
    ): (RealPointInTimeCollectors.collect_sina_fund_flow, "ProviderBatch"),
    (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_global_news"
    ): (RealPointInTimeCollectors.collect_global_news, "ProviderBatch"),
    "news_briefing.fetch_cls_telegraph": (
        fetch_cls_telegraph,
        "list[dict]",
    ),
    (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_stock_news"
    ): (RealPointInTimeCollectors.collect_stock_news, "ProviderBatch"),
    (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_announcements"
    ): (RealPointInTimeCollectors.collect_announcements, "ProviderBatch"),
    (
        "minute_probe_sources.MootdxMinuteProbeCollectors."
        "collect_minute_bars"
    ): (MootdxMinuteProbeCollectors.collect_minute_bars, "ProviderBatch"),
    (
        "minute_probe_sources.MootdxMinuteProbeCollectors."
        "collect_transaction_evidence"
    ): (
        MootdxMinuteProbeCollectors.collect_transaction_evidence,
        "dict[str, Any]",
    ),
}


def _quote_record(**changes):
    row = {
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
    row.update(changes)
    return row


def _test_bound_binding(provider_key=TEST_PROVIDER_KEY):
    return SourceAdapterBinding(
        **QUOTE_IDENTITY,
        provider_key=provider_key,
        candidate_provider_key="",
        legacy_provider_key="",
        implementation_status="bound",
    )


def _test_envelope(provider, provider_key=TEST_PROVIDER_KEY):
    return SourceProviderEnvelope(
        provider_key=provider_key,
        provider_callable=provider,
    )


def _run_test_quote(provider, *, provider_key=TEST_PROVIDER_KEY):
    return adapters._execute_source_adapter_with_bindings_for_test(
        **QUOTE_IDENTITY,
        provider_envelope=_test_envelope(provider, provider_key),
        entries=[_test_bound_binding()],
        environ={},
    )


def _run_test_valuation(provider):
    binding = SourceAdapterBinding(
        **VALUATION_IDENTITY,
        provider_key=VALUATION_TEST_PROVIDER_KEY,
        candidate_provider_key="",
        legacy_provider_key="",
        implementation_status="bound",
    )
    return adapters._execute_source_adapter_with_bindings_for_test(
        **VALUATION_IDENTITY,
        provider_envelope=SourceProviderEnvelope(
            provider_key=VALUATION_TEST_PROVIDER_KEY,
            provider_callable=provider,
        ),
        entries=[binding],
        environ={},
    )


def _assert_safe(result, *, provider_called=None, audit=False):
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["require_hard_gate"] is False
    assert result["automatic_configuration_change"] is False
    assert result["adapter_network_requests_made"] == 0
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []
    if audit:
        assert result["network_requests_made"] == 0
        assert result["upstream_network_activity"] == "not_applicable"
    elif provider_called is True:
        assert result["provider_called"] is True
        assert result["network_requests_made"] is None
        assert result["upstream_network_activity"] == "unknown"
    elif provider_called is False:
        assert result["provider_called"] is False
        assert result["network_requests_made"] == 0
        assert result["upstream_network_activity"] == "not_called"


def test_production_adapter_matrix_exactly_covers_b1_28_identities():
    capability_registry = get_source_capability_registry()
    adapter_registry = get_source_adapter_registry()

    assert len(capability_registry) == len(adapter_registry) == 28
    expected = {
        (
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        )
        for row in capability_registry
    }
    actual = {
        (
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        )
        for row in adapter_registry
    }
    assert actual == expected
    assert adapter_registry == sorted(
        adapter_registry,
        key=lambda row: (
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
            row["provider_key"],
        ),
    )
    assert ADAPTER_REGISTRY_SCHEMA_VERSION == "source_capability_adapter_registry_v3"
    assert compute_source_capability_registry_hash() == (
        adapters.EXPECTED_CAPABILITY_REGISTRY_HASH
    )


def test_production_registry_has_zero_bound_two_candidates_and_13_legacy_entries():
    audit = audit_source_adapters(environ={})
    legacy = [
        row
        for row in audit["adapter_matrix"]
        if row["legacy_implementation_present"]
    ]

    assert audit["bound_count"] == 0
    assert audit["candidate_count"] == 2
    assert audit["legacy_implementation_present_count"] == 13
    assert audit["contract_incompatible_count"] == 11
    assert len(legacy) == 13
    assert {
        row["implementation_status"] for row in legacy
    } == {"candidate_not_activated", "contract_incompatible"}
    assert all(row["status"] != SOURCE_ADAPTER_BOUND for row in legacy)
    _assert_safe(audit, audit=True)


def test_real_legacy_provider_signatures_and_return_types_are_not_reported_bound():
    legacy_rows = [
        row
        for row in get_source_adapter_registry()
        if row["legacy_implementation_present"]
    ]

    assert len(legacy_rows) == 13
    for row in legacy_rows:
        provider, expected_return = LEGACY_PROVIDER_OBJECTS[
            row["legacy_provider_key"]
        ]
        signature = inspect.signature(provider)
        assert signature.return_annotation == expected_return
        assert row["implementation_status"] != "bound"
        if row["legacy_provider_key"] != "news_briefing.fetch_cls_telegraph":
            assert next(iter(signature.parameters)) == "self"


def test_public_registry_hash_is_fixed_and_custom_hash_entry_is_private():
    rows = get_source_adapter_registry()
    shuffled = deepcopy(rows)
    random.Random(81).shuffle(shuffled)

    assert compute_source_adapter_registry_hash() == ADAPTER_REGISTRY_HASH
    assert (
        adapters._compute_source_adapter_registry_hash_for_test(shuffled)
        == ADAPTER_REGISTRY_HASH
    )
    assert "entries" not in inspect.signature(
        compute_source_adapter_registry_hash
    ).parameters
    assert not hasattr(adapters, "canonicalize_source_adapter_bindings")


def test_production_registry_rejects_deleted_duplicate_and_added_entries():
    rows = get_source_adapter_registry()

    with pytest.raises(
        ValueError,
        match="production_source_adapter_binding_missing",
    ):
        adapters._validate_production_source_adapter_bindings(rows[:-1])
    with pytest.raises(ValueError, match="source_adapter_binding_duplicate"):
        adapters._validate_production_source_adapter_bindings(rows + [rows[0]])
    added = deepcopy(rows[0])
    added["origin_source"] = "unknown_vendor"
    with pytest.raises(ValueError, match="source_adapter_identity_unknown"):
        adapters._validate_production_source_adapter_bindings(rows + [added])


def test_production_registry_rejects_downgrade_even_after_private_resign():
    rows = get_source_adapter_registry()
    changed = deepcopy(rows)
    target = next(row for row in changed if row["legacy_implementation_present"])
    target.update(
        {
            "provider_key": "",
            "candidate_provider_key": "",
            "legacy_provider_key": "",
            "implementation_status": "not_implemented",
            "legacy_implementation_present": False,
        }
    )

    resigned_hash = adapters._compute_source_adapter_registry_hash_for_test(changed)
    assert len(resigned_hash) == 64
    assert resigned_hash != ADAPTER_REGISTRY_HASH
    with pytest.raises(
        ValueError,
        match="production_source_adapter_binding_modified",
    ):
        adapters._validate_production_source_adapter_bindings(changed)


def test_public_registry_and_hash_fail_closed_when_fixed_matrix_is_removed(
    monkeypatch,
):
    rows = tuple(adapters.SOURCE_ADAPTER_BINDINGS[:-1])
    monkeypatch.setattr(adapters, "SOURCE_ADAPTER_BINDINGS", rows)

    with pytest.raises(
        ValueError,
        match="production_source_adapter_binding_missing",
    ):
        get_source_adapter_registry()
    with pytest.raises(
        ValueError,
        match="production_source_adapter_binding_missing",
    ):
        compute_source_adapter_registry_hash()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability", "Quote"),
        ("capability", " quote"),
        ("origin_source", "Tencent"),
        ("adapter", "direct_http "),
        ("source_version", "QT.GTIMG.CN~88_FIELDS_V2026-07-30"),
    ],
)
def test_noncanonical_production_identity_variants_are_rejected(field, value):
    row = deepcopy(get_source_adapter_registry()[0])
    row[field] = value

    with pytest.raises(ValueError, match="source_adapter_identity_not_canonical"):
        adapters._validate_production_source_adapter_bindings([row])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability", "Quote"),
        ("capability", "quote "),
        ("origin_source", "Tencent"),
        ("adapter", " direct_http"),
        ("source_version", "QT.GTIMG.CN~88_FIELDS_V2026-07-30"),
    ],
)
def test_noncanonical_execution_identity_is_rejected_before_provider(field, value):
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return [_quote_record()]

    request = dict(QUOTE_IDENTITY)
    request[field] = value
    result = execute_source_adapter(
        **request,
        provider_envelope=_test_envelope(provider),
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_REQUEST_INVALID
    assert calls == 0
    _assert_safe(result, provider_called=False)


def test_bare_callable_cannot_bypass_candidate_not_activated_gate():
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return [_quote_record()]

    result = execute_source_adapter(
        **QUOTE_IDENTITY,
        provider_envelope=provider,
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
    assert result["rejection_reasons"] == [
        "source_adapter_candidate_not_activated"
    ]
    assert calls == 0
    _assert_safe(result, provider_called=False)


def test_public_candidate_rejects_unrelated_provider_without_calling_it():
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return [_quote_record()]

    result = execute_source_adapter(
        **QUOTE_IDENTITY,
        provider_envelope=_test_envelope(
            provider,
            provider_key="tests.unrelated_provider_v1",
        ),
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
    assert result["rejection_reasons"] == [
        "source_adapter_candidate_not_activated"
    ]
    assert calls == 0
    _assert_safe(result, provider_called=False)


def test_matching_candidate_or_legacy_provider_key_is_not_called_in_production():
    row = next(
        item
        for item in get_source_adapter_registry()
        if item["capability"] == "quote"
        and item["origin_source"] == "tencent"
    )
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return [_quote_record()]

    result = execute_source_adapter(
        **QUOTE_IDENTITY,
        provider_envelope=_test_envelope(
            provider,
            row["candidate_provider_key"],
        ),
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
    assert result["binding"]["implementation_status"] == (
        "candidate_not_activated"
    )
    assert result["binding"]["provider_key"] == ""
    assert result["binding"]["legacy_provider_key"] == (
        "astock_client.AStockClient._tencent_quotes"
    )
    assert calls == 0
    _assert_safe(result, provider_called=False)


@pytest.mark.parametrize(
    ("capability", "origin_source", "adapter", "source_version"),
    [
        ("unknown_capability", "tencent", "direct_http", "v1"),
        ("quote", "unknown_vendor", "direct_http", "v1"),
        ("legacy_market_data", "tushare", "tushare", "retired_by_policy_v1"),
        ("legacy_market_data", "ashare", "ashare", "retired_by_policy_v1"),
    ],
)
def test_unknown_and_retired_routes_do_not_call_provider(
    capability,
    origin_source,
    adapter,
    source_version,
):
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return [_quote_record()]

    result = execute_source_adapter(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        source_version=source_version,
        provider_envelope=_test_envelope(provider),
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_ROUTE_REJECTED
    assert calls == 0
    _assert_safe(result, provider_called=False)


def test_existing_source_field_is_rejected_without_identity_repair():
    records = [
        {
            "capability": "quote",
            "source": "tencent_quote",
            "source_version": QUOTE_IDENTITY["source_version"],
            "event_time": "2026-08-25T14:49:00+08:00",
            "observed_at": "2026-08-25T14:49:01+08:00",
            "available_at": "2026-08-25T14:49:02+08:00",
            "request_hash": "a" * 64,
            "raw_hash": "b" * 64,
        }
    ]
    original = deepcopy(records)

    result = _run_test_quote(lambda: records)

    assert result["status"] == SOURCE_ADAPTER_PROVENANCE_REJECTED
    assert result["provenance_validation"]["status"] == "PROVENANCE_FIELDS_MISSING"
    assert records == original
    _assert_safe(result, provider_called=True)


def test_called_provider_reports_upstream_network_activity_unknown_not_zero():
    result = _run_test_quote(lambda: [_quote_record()])

    assert result["status"] == SOURCE_ADAPTER_BOUND
    assert result["test_only"] is True
    assert result["network_requests_made"] is None
    assert result["upstream_network_activity"] == "unknown"
    _assert_safe(result, provider_called=True)


def test_quote_request_rejects_valuation_capability_record():
    result = _run_test_quote(
        lambda: [_quote_record(capability="valuation")]
    )

    assert result["status"] == SOURCE_ADAPTER_PROVENANCE_REJECTED
    assert result["rejection_reasons"] == [
        {
            "index": 0,
            "reason": "source_adapter_record_capability_mismatch",
            "actual_capability": "valuation",
            "expected_capability": "quote",
        }
    ]
    _assert_safe(result, provider_called=True)


def test_valuation_request_rejects_quote_capability_record():
    result = _run_test_valuation(lambda: [_quote_record()])

    assert result["status"] == SOURCE_ADAPTER_PROVENANCE_REJECTED
    assert result["rejection_reasons"] == [
        {
            "index": 0,
            "reason": "source_adapter_record_capability_mismatch",
            "actual_capability": "quote",
            "expected_capability": "valuation",
        }
    ]
    _assert_safe(result, provider_called=True)


def test_provider_record_without_capability_is_rejected():
    record = _quote_record()
    del record["capability"]

    result = _run_test_quote(lambda: [record])

    assert result["status"] == SOURCE_ADAPTER_PROVENANCE_REJECTED
    assert result["rejection_reasons"] == [
        {
            "index": 0,
            "reason": "source_adapter_record_capability_missing",
        }
    ]
    _assert_safe(result, provider_called=True)


def test_test_only_execution_reports_actual_selection_registry_hash():
    binding = _test_bound_binding()
    selection_rows = adapters._canonicalize_source_adapter_bindings_for_test(
        [binding]
    )
    expected_selection_hash = (
        adapters._compute_source_adapter_registry_hash_for_test([binding])
    )

    result = _run_test_quote(lambda: [_quote_record()])

    assert result["selection_registry_scope"] == "test_only"
    assert result["production_adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    assert result["selection_adapter_registry_hash"] == expected_selection_hash
    assert result["adapter_registry_hash"] == expected_selection_hash
    assert result["selection_adapter_registry_hash"] != ADAPTER_REGISTRY_HASH
    assert selection_rows[0] not in get_source_adapter_registry()
    _assert_safe(result, provider_called=True)


def test_test_only_selection_hash_changes_with_provider_key():
    first_key = "tests.contract_compatible_quote_v1"
    second_key = "tests.contract_compatible_quote_v2"

    first = adapters._execute_source_adapter_with_bindings_for_test(
        **QUOTE_IDENTITY,
        provider_envelope=_test_envelope(
            lambda: [_quote_record()],
            first_key,
        ),
        entries=[_test_bound_binding(first_key)],
        environ={},
    )
    second = adapters._execute_source_adapter_with_bindings_for_test(
        **QUOTE_IDENTITY,
        provider_envelope=_test_envelope(
            lambda: [_quote_record()],
            second_key,
        ),
        entries=[_test_bound_binding(second_key)],
        environ={},
    )

    assert first["selection_registry_scope"] == "test_only"
    assert second["selection_registry_scope"] == "test_only"
    assert (
        first["selection_adapter_registry_hash"]
        != second["selection_adapter_registry_hash"]
    )
    assert first["production_adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    assert second["production_adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    _assert_safe(first, provider_called=True)
    _assert_safe(second, provider_called=True)


def test_public_execution_uses_production_registry_hash_and_scope():
    binding = next(
        row
        for row in get_source_adapter_registry()
        if row["capability"] == "quote" and row["origin_source"] == "tencent"
    )

    result = execute_source_adapter(
        **QUOTE_IDENTITY,
        provider_envelope=_test_envelope(
            lambda: [_quote_record()],
            binding["candidate_provider_key"],
        ),
        environ={},
    )

    assert result["selection_registry_scope"] == "production"
    assert result["status"] == SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
    assert result["production_adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    assert result["selection_adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    assert result["adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    _assert_safe(result, provider_called=False)


def test_provider_empty_and_provider_failure_are_distinct_and_network_unknown():
    empty = _run_test_quote(lambda: [])

    def failing_provider():
        raise RuntimeError("IWENCAI_API_KEY=secret-value")

    failed = _run_test_quote(failing_provider)

    assert empty["status"] == SOURCE_ADAPTER_PROVIDER_EMPTY
    assert empty["execution_ok"] is True
    assert failed["status"] == SOURCE_ADAPTER_PROVIDER_FAILED
    assert failed["execution_ok"] is False
    assert failed["provider_error_type"] == "RuntimeError"
    assert "secret-value" not in json.dumps(failed, sort_keys=True)
    _assert_safe(empty, provider_called=True)
    _assert_safe(failed, provider_called=True)


@pytest.mark.parametrize(
    "records",
    [
        [
            _quote_record(),
            _quote_record(
                origin_source="sina",
                source_version="sina_moneyflow_current_v2026-07-30",
            ),
        ],
        [{key: value for key, value in _quote_record().items() if key != "raw_hash"}],
        [_quote_record(available_at="2026-08-25T14:48:59+08:00")],
        [_quote_record(request_hash="not-a-sha256")],
    ],
    ids=["mixed-source", "missing-field", "time-reversed", "hash-invalid"],
)
def test_provider_provenance_contract_failures_are_rejected(records):
    result = _run_test_quote(lambda: records)

    assert result["status"] == SOURCE_ADAPTER_PROVENANCE_REJECTED
    assert (
        result["provenance_validation"]["status"]
        != "SOURCE_PROVENANCE_ACCEPTED"
    )
    _assert_safe(result, provider_called=True)


def test_provider_records_are_not_silently_repaired_or_mutated():
    records = [_quote_record()]
    original = deepcopy(records)

    result = _run_test_quote(lambda: records)

    assert result["status"] == SOURCE_ADAPTER_BOUND
    assert result["provenance_validation"]["hard_gate_authorized"] is False
    assert records == original
    _assert_safe(result, provider_called=True)


def test_akshare_iwencai_and_mootdx_never_become_hard_gate_sources():
    secret = "do-not-serialize-this-secret"
    audit = audit_source_adapters(
        environ={
            "IWENCAI_API_KEY": secret,
            "IWENCAI_BASE_URL": "https://example.invalid",
        }
    )
    serialized = json.dumps(audit, ensure_ascii=False, sort_keys=True)
    wrappers = [
        row for row in audit["adapter_matrix"] if row["adapter"] == "akshare"
    ]
    iwencai = next(
        row
        for row in audit["adapter_matrix"]
        if row["adapter"] == "iwencai_openapi"
    )
    mootdx = [
        row for row in audit["adapter_matrix"] if row["adapter"] == "mootdx"
    ]

    assert wrappers and mootdx
    assert all(row["status"] != SOURCE_ADAPTER_BOUND for row in wrappers)
    assert all(row["hard_gate_authorized"] is False for row in wrappers + mootdx)
    assert iwencai["hard_gate_authorized"] is False
    assert secret not in serialized
    assert "IWENCAI_API_KEY" not in serialized
    _assert_safe(audit, audit=True)


def test_audit_is_complete_deterministic_and_safe():
    first = run_source_adapter_audit()
    second = run_source_adapter_audit()

    assert first == second
    assert first["status"] == SOURCE_ADAPTER_AUDIT_COMPLETE
    assert first["adapter_entry_count"] == 28
    assert first["bound_count"] == 0
    assert first["adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    assert first["production_adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    assert first["selection_adapter_registry_hash"] == ADAPTER_REGISTRY_HASH
    assert first["selection_registry_scope"] == "production"
    assert first["capability_registry_hash"] == (
        compute_source_capability_registry_hash()
    )
    _assert_safe(first, audit=True)


def test_audit_command_is_byte_deterministic_and_does_not_read_secret_environment():
    environment = dict(os.environ)
    environment["IWENCAI_API_KEY"] = "must-not-appear"
    environment["IWENCAI_BASE_URL"] = "https://example.invalid"
    command = [sys.executable, str(SCRIPT)]

    first = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
    ).stdout
    second = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
    ).stdout

    assert first == second
    assert b"must-not-appear" not in first
    payload = json.loads(first.decode("utf-8"))
    assert payload["network_requests_made"] == 0
    assert payload["bound_count"] == 0
    _assert_safe(payload, audit=True)


def test_audit_never_opens_http_or_tcp(monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError("network access is forbidden in B2.1 audit")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    try:
        import requests

        monkeypatch.setattr(requests.sessions.Session, "request", fail_network)
    except ImportError:
        pass

    result = audit_source_adapters(environ={})

    assert result["network_requests_made"] == 0
    assert result["status"] == SOURCE_ADAPTER_AUDIT_COMPLETE
    _assert_safe(result, audit=True)
