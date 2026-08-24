from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from overnight_quant.data.source_capability_registry import (
    QUALIFICATION_PROGRESS_MOOTDX,
    REGISTRY_SCHEMA_VERSION,
    audit_source_capabilities,
    compute_source_capability_registry_hash,
    get_source_capability_registry,
    route_source_capability,
    validate_source_provenance_batch,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "overnight_quant"
    / "scripts"
    / "run_source_capability_audit.py"
)
REQUIRED_FIELDS = {
    "capability",
    "origin_source",
    "adapter",
    "role",
    "time_critical",
    "requires_secret",
    "hard_gate_eligible",
    "qualification_required",
    "point_in_time_required",
    "fallback_group",
    "source_version",
    "enabled_by_policy",
}


def test_registry_order_does_not_change_hash():
    registry = get_source_capability_registry()

    forward = compute_source_capability_registry_hash(registry)
    reverse = compute_source_capability_registry_hash(reversed(registry))

    assert len(forward) == 64
    assert reverse == forward
    assert all(REQUIRED_FIELDS <= set(row) for row in registry)
    assert REGISTRY_SCHEMA_VERSION == "source_capability_registry_v1"


@pytest.mark.parametrize("origin_source", ["tushare", "ashare"])
def test_retired_sources_never_enter_route(origin_source):
    registry = get_source_capability_registry()
    retired = [
        row for row in registry if row["origin_source"] == origin_source
    ]

    result = route_source_capability(
        "legacy_market_data",
        origin_source=origin_source,
        adapter=origin_source,
        require_hard_gate=False,
    )

    assert len(retired) == 1
    assert retired[0]["role"] == "retired"
    assert retired[0]["enabled_by_policy"] is False
    assert result["status"] == "SOURCE_DISABLED_BY_POLICY"
    assert result["selected_source"] is None
    _assert_safe(result)


def test_retired_source_modules_are_not_imported_by_registry():
    source = (
        ROOT
        / "overnight_quant"
        / "data"
        / "source_capability_registry.py"
    ).read_text(encoding="utf-8").lower()

    assert "import tushare" not in source
    assert "from tushare" not in source
    assert "import ashare" not in source
    assert "from ashare" not in source


def test_akshare_preserves_origin_and_cannot_satisfy_hard_gate():
    registry = get_source_capability_registry()
    wrappers = [row for row in registry if row["adapter"] == "akshare"]

    result = route_source_capability(
        "announcement",
        origin_source="cninfo",
        adapter="akshare",
        require_hard_gate=True,
    )

    assert wrappers
    assert all(row["origin_source"] != "akshare" for row in wrappers)
    assert all(row["is_wrapper"] is True for row in wrappers)
    assert all(row["hard_gate_eligible"] is False for row in wrappers)
    assert result["status"] == "OPTIONAL_ADAPTER_UNAVAILABLE"
    assert result["selected_source"] is None
    assert result["hard_gate_authorized"] is False
    _assert_safe(result)


@pytest.mark.parametrize(
    "capability",
    ["minute_bar", "transaction", "order_book"],
)
def test_unqualified_mootdx_cannot_become_formal_source(capability):
    registry = get_source_capability_registry()
    row = next(
        item
        for item in registry
        if item["capability"] == capability
        and item["adapter"] == "mootdx"
    )

    result = route_source_capability(
        capability,
        origin_source="tongdaxin",
        adapter="mootdx",
        require_hard_gate=True,
    )

    assert row["role"] == "audit_only"
    assert row["qualification_status"] == "unqualified"
    assert row["qualification_progress"] == QUALIFICATION_PROGRESS_MOOTDX
    assert result["status"] == "SOURCE_UNQUALIFIED"
    assert "source_unqualified" in result["rejection_reasons"]
    assert result["selected_source"] is None
    _assert_safe(result)


def test_sina_proxy_cannot_satisfy_hard_gate():
    result = route_source_capability(
        "fund_flow",
        origin_source="sina",
        adapter="direct_http",
        require_hard_gate=True,
    )

    assert result["status"] == "SOURCE_NOT_HARD_GATE_ELIGIBLE"
    assert "proxy_source_forbidden" in result["rejection_reasons"]
    assert result["selected_source"] is None
    assert result["hard_gate_authorized"] is False
    _assert_safe(result)


def test_iwencai_missing_key_is_optional_unconfigured():
    audit = audit_source_capabilities(environ={})
    status = _source_status(
        audit,
        capability="semantic_research",
        origin_source="iwencai",
        adapter="iwencai_openapi",
    )
    route = route_source_capability(
        "semantic_research",
        origin_source="iwencai",
        adapter="iwencai_openapi",
        environ={},
    )

    assert audit["execution_ok"] is True
    assert status["status"] == "OPTIONAL_UNCONFIGURED"
    assert status["secret_configured"] is False
    assert route["status"] == "OPTIONAL_UNCONFIGURED"
    assert route["selected_source"] is None
    _assert_safe(audit)
    _assert_safe(route)


def test_iwencai_environment_values_are_never_serialized():
    secret = "unit-secret-that-must-not-leak"
    base_url = "https://unit.invalid/private-path"

    audit = audit_source_capabilities(
        environ={
            "IWENCAI_API_KEY": secret,
            "IWENCAI_BASE_URL": base_url,
        }
    )
    serialized = json.dumps(audit, ensure_ascii=False, sort_keys=True)
    status = _source_status(
        audit,
        capability="semantic_research",
        origin_source="iwencai",
        adapter="iwencai_openapi",
    )

    assert status["status"] == "OPTIONAL_CONFIGURED"
    assert status["secret_configured"] is True
    assert status["base_url_configured"] is True
    assert secret not in serialized
    assert base_url not in serialized


def test_cninfo_is_primary_announcement_source():
    result = route_source_capability(
        "announcement",
        origin_source="cninfo",
        adapter="direct_http",
        require_hard_gate=True,
    )

    assert result["status"] == "SOURCE_ROUTE_SELECTED"
    assert result["source_count"] == 1
    assert result["hard_gate_authorized"] is True
    assert result["selected_source"]["origin_source"] == "cninfo"
    assert result["selected_source"]["adapter"] == "direct_http"
    assert result["selected_source"]["role"] == "primary"
    _assert_safe(result)


def test_tencent_is_primary_for_fixed_current_capabilities():
    for capability in (
        "quote",
        "valuation",
        "trading_calendar",
        "daily_bar_qfq",
    ):
        result = route_source_capability(
            capability,
            origin_source="tencent",
            adapter="direct_http",
        )
        assert result["status"] == "SOURCE_ROUTE_SELECTED"
        assert result["selected_source"]["role"] == "primary"
        assert result["selected_source"]["origin_source"] == "tencent"
        _assert_safe(result)


def test_unknown_capability_and_source_fail_closed():
    capability = route_source_capability("not_registered")
    source = route_source_capability(
        "quote",
        origin_source="unknown_vendor",
    )

    assert capability["status"] == "UNKNOWN_CAPABILITY"
    assert source["status"] == "UNKNOWN_SOURCE"
    assert capability["selected_source"] is None
    assert source["selected_source"] is None
    _assert_safe(capability)
    _assert_safe(source)


def test_mixed_origin_records_cannot_form_one_provenance_batch():
    records = [
        {
            "origin_source": "eastmoney",
            "adapter": "direct_http",
            "source_version": "np_weblist_724_v2026-07-30",
        },
        {
            "origin_source": "cls",
            "adapter": "direct_http",
            "source_version": "cls_telegraph_direct_v2026-08-24",
        },
    ]

    result = validate_source_provenance_batch("global_news", records)

    assert result["status"] == "MIXED_SOURCE_PROVENANCE_REJECTED"
    assert result["source_identity_count"] == 2
    assert result["provenance"] is None
    _assert_safe(result)


def test_direct_and_wrapper_records_cannot_be_counted_as_two_sources():
    records = [
        {
            "origin_source": "cninfo",
            "adapter": "direct_http",
            "source_version": "cninfo_query_v2026-07-30",
        },
        {
            "origin_source": "cninfo",
            "adapter": "akshare",
            "source_version": "cninfo_announcement_via_akshare_unqualified_v1",
        },
    ]

    result = validate_source_provenance_batch("announcement", records)

    assert result["status"] == "MIXED_SOURCE_PROVENANCE_REJECTED"
    assert result["source_identity_count"] == 2
    _assert_safe(result)


def test_single_origin_provenance_contract_is_auditable_but_not_data_ready():
    record = {
        "origin_source": "cninfo",
        "adapter": "direct_http",
        "source_version": "cninfo_query_v2026-07-30",
        "event_time": "2026-08-24T14:00:00+08:00",
        "published_at": "2026-08-24T14:00:00+08:00",
        "observed_at": "2026-08-24T14:00:01+08:00",
        "available_at": "2026-08-24T14:00:01+08:00",
        "request_hash": "a" * 64,
        "raw_hash": "b" * 64,
    }

    result = validate_source_provenance_batch(
        "announcement",
        [record],
        require_hard_gate=True,
    )

    assert result["status"] == "SOURCE_PROVENANCE_ACCEPTED"
    assert result["hard_gate_authorized"] is True
    assert result["provenance"]["origin_source"] == "cninfo"
    assert len(result["provenance"]["record_hash"]) == 64
    _assert_safe(result)


def test_announcement_without_published_at_fails_provenance_contract():
    record = {
        "origin_source": "cninfo",
        "adapter": "direct_http",
        "source_version": "cninfo_query_v2026-07-30",
        "event_time": "2026-08-24T14:00:00+08:00",
        "observed_at": "2026-08-24T14:00:01+08:00",
        "available_at": "2026-08-24T14:00:01+08:00",
        "request_hash": "a" * 64,
        "raw_hash": "b" * 64,
    }

    result = validate_source_provenance_batch("announcement", [record])

    assert result["status"] == "PROVENANCE_CONTRACT_INCOMPLETE"
    assert result["missing_fields"] == ["published_at"]
    _assert_safe(result)


def test_audit_output_is_deterministic_read_only_json(tmp_path):
    environment = dict(os.environ)
    environment.pop("IWENCAI_API_KEY", None)
    environment.pop("IWENCAI_BASE_URL", None)
    command = [sys.executable, str(SCRIPT)]

    first = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        command,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
    )

    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stderr == b""
    payload = json.loads(first.stdout.decode("utf-8"))
    assert payload["status"] == "SOURCE_CAPABILITY_AUDIT_COMPLETE"
    assert payload["network_requests_made"] == 0
    assert len(payload["registry_hash"]) == 64
    _assert_safe(payload)


def test_every_public_result_keeps_trading_outputs_empty():
    registry = get_source_capability_registry()
    reordered = deepcopy(registry)
    reordered.reverse()
    outputs = [
        audit_source_capabilities(environ={}, entries=reordered),
        route_source_capability("quote", require_hard_gate=True),
        route_source_capability(
            "minute_bar",
            origin_source="tongdaxin",
            require_hard_gate=True,
        ),
        validate_source_provenance_batch("quote", []),
    ]

    for result in outputs:
        _assert_safe(result)


def _source_status(audit, *, capability, origin_source, adapter):
    return next(
        row
        for row in audit["source_statuses"]
        if row["capability"] == capability
        and row["origin_source"] == origin_source
        and row["adapter"] == adapter
    )


def _assert_safe(result):
    assert result["data_ready"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []
