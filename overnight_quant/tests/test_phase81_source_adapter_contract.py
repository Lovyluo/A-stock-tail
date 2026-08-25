from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys

import pytest

from overnight_quant.data.source_capability_adapters import (
    ADAPTER_REGISTRY_SCHEMA_VERSION,
    SOURCE_ADAPTER_AUDIT_COMPLETE,
    SOURCE_ADAPTER_BOUND,
    SOURCE_ADAPTER_NOT_IMPLEMENTED,
    SOURCE_ADAPTER_PROVIDER_EMPTY,
    SOURCE_ADAPTER_PROVIDER_FAILED,
    SOURCE_ADAPTER_PROVENANCE_REJECTED,
    SOURCE_ADAPTER_REQUEST_INVALID,
    SOURCE_ADAPTER_ROUTE_REJECTED,
    audit_source_adapters,
    canonicalize_source_adapter_bindings,
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


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "overnight_quant" / "scripts" / "run_source_adapter_audit.py"
QUOTE_IDENTITY = {
    "capability": "quote",
    "origin_source": "tencent",
    "adapter": "direct_http",
    "source_version": "qt.gtimg.cn~88_fields_v2026-07-30",
}


def _quote_record(**changes):
    row = {
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


def _run_quote(provider):
    return execute_source_adapter(
        **QUOTE_IDENTITY,
        provider=provider,
        environ={},
    )


def _assert_safe(result):
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["require_hard_gate"] is False
    assert result["automatic_configuration_change"] is False
    assert result["network_requests_made"] == 0
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def test_adapter_matrix_covers_all_28_capability_entries_deterministically():
    capability_registry = get_source_capability_registry()
    adapter_registry = get_source_adapter_registry()

    assert len(capability_registry) == len(adapter_registry) == 28
    expected_identities = {
        (
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        )
        for row in capability_registry
    }
    actual_identities = {
        (
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        )
        for row in adapter_registry
    }
    assert actual_identities == expected_identities
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
    assert sum(row["implementation_status"] == "bound" for row in adapter_registry) == 13
    assert ADAPTER_REGISTRY_SCHEMA_VERSION == "source_capability_adapter_registry_v1"


def test_adapter_registry_order_does_not_change_hash():
    rows = get_source_adapter_registry()
    shuffled = deepcopy(rows)
    random.Random(81).shuffle(shuffled)

    expected = "308cf19cbec50153d9de3b4f6f23cb4f1a79d19135004127933d470c73dfecd9"
    assert compute_source_adapter_registry_hash(rows) == expected
    assert compute_source_adapter_registry_hash(shuffled) == expected
    assert compute_source_adapter_registry_hash() == expected


def test_unknown_duplicate_and_version_mismatched_bindings_fail_closed():
    rows = get_source_adapter_registry()
    duplicate = [rows[0], rows[0]]
    unknown = deepcopy(rows[0])
    unknown["origin_source"] = "unknown_vendor"
    mismatched = deepcopy(rows[0])
    mismatched["source_version"] = "unknown_version"

    with pytest.raises(ValueError, match="source_adapter_binding_duplicate"):
        canonicalize_source_adapter_bindings(duplicate)
    with pytest.raises(ValueError, match="source_adapter_identity_unknown"):
        canonicalize_source_adapter_bindings([unknown])
    with pytest.raises(ValueError, match="source_adapter_identity_unknown"):
        canonicalize_source_adapter_bindings([mismatched])

    result = execute_source_adapter(
        "quote",
        origin_source="tencent",
        adapter="direct_http",
        source_version="unknown_version",
        provider=lambda: [_quote_record()],
        environ={},
    )
    assert result["status"] == SOURCE_ADAPTER_REQUEST_INVALID
    assert result["provider_called"] is False
    _assert_safe(result)


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
        provider=provider,
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_ROUTE_REJECTED
    assert calls == 0
    assert result["provider_called"] is False
    _assert_safe(result)


def test_not_implemented_binding_does_not_call_provider():
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return []

    result = execute_source_adapter(
        "research_report",
        origin_source="eastmoney",
        adapter="direct_http",
        source_version="eastmoney_reportapi_v2026-08-24",
        provider=provider,
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_NOT_IMPLEMENTED
    assert result["provider_called"] is False
    assert calls == 0
    _assert_safe(result)


def test_mootdx_binding_remains_audit_only_and_never_authorizes_hard_gate():
    row = next(
        item
        for item in audit_source_adapters(environ={})["adapter_matrix"]
        if item["capability"] == "minute_bar"
        and item["origin_source"] == "tongdaxin"
    )

    assert row["status"] == SOURCE_ADAPTER_BOUND
    assert row["role"] == "audit_only"
    assert row["qualification_status"] == "unqualified"
    assert row["qualification_progress"] == "0/3"
    assert row["hard_gate_authorized"] is False


def test_akshare_and_iwencai_are_reported_without_loading_dependencies_or_secrets():
    secret = "do-not-serialize-this-secret"
    audit = audit_source_adapters(
        environ={
            "IWENCAI_API_KEY": secret,
            "IWENCAI_BASE_URL": "https://example.invalid",
        }
    )
    serialized = json.dumps(audit, ensure_ascii=False, sort_keys=True)
    wrappers = [row for row in audit["adapter_matrix"] if row["adapter"] == "akshare"]
    iwencai = next(
        row for row in audit["adapter_matrix"] if row["adapter"] == "iwencai_openapi"
    )

    assert wrappers
    assert all(row["provider_key"] == "" for row in wrappers)
    assert all(row["hard_gate_authorized"] is False for row in wrappers)
    assert iwencai["provider_key"] == ""
    assert iwencai["hard_gate_authorized"] is False
    assert secret not in serialized
    assert "IWENCAI_API_KEY" not in serialized
    _assert_safe(audit)


def test_provider_empty_and_provider_failure_are_distinct_and_sanitized():
    empty = _run_quote(lambda: [])

    def failing_provider():
        raise RuntimeError("IWENCAI_API_KEY=secret-value")

    failed = _run_quote(failing_provider)

    assert empty["status"] == SOURCE_ADAPTER_PROVIDER_EMPTY
    assert empty["execution_ok"] is True
    assert failed["status"] == SOURCE_ADAPTER_PROVIDER_FAILED
    assert failed["execution_ok"] is False
    assert failed["provider_error_type"] == "RuntimeError"
    assert "secret-value" not in json.dumps(failed, sort_keys=True)
    _assert_safe(empty)
    _assert_safe(failed)


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
    result = _run_quote(lambda: records)

    assert result["status"] == SOURCE_ADAPTER_PROVENANCE_REJECTED
    assert result["provenance"]["status"] != "SOURCE_PROVENANCE_ACCEPTED"
    _assert_safe(result)


def test_provider_records_are_not_silently_repaired_or_mutated():
    records = [_quote_record()]
    original = deepcopy(records)

    result = _run_quote(lambda: records)

    assert result["status"] == SOURCE_ADAPTER_BOUND
    assert result["hard_gate_authorized"] is False
    assert result["provenance"]["hard_gate_authorized"] is False
    assert records == original
    _assert_safe(result)


def test_audit_is_complete_deterministic_and_safe():
    first = run_source_adapter_audit()
    second = run_source_adapter_audit()

    assert first == second
    assert first["status"] == SOURCE_ADAPTER_AUDIT_COMPLETE
    assert first["adapter_entry_count"] == 28
    assert first["adapter_registry_hash"] == compute_source_adapter_registry_hash()
    assert first["capability_registry_hash"] == compute_source_capability_registry_hash()
    _assert_safe(first)


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
    _assert_safe(payload)


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
    _assert_safe(result)
