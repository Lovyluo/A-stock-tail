from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable, Mapping

from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    REGISTRY_SCHEMA_VERSION,
    compute_source_capability_registry_hash,
    get_source_capability_registry,
    route_source_capability,
    validate_source_provenance_batch,
)


ADAPTER_REGISTRY_SCHEMA_VERSION = "source_capability_adapter_registry_v2"
EXPECTED_CAPABILITY_REGISTRY_ENTRY_COUNT = 28
EXPECTED_CAPABILITY_REGISTRY_HASH = (
    "303db7cd50d8cc53e3729d69c7aeb3c053e203ba1885cecda1b10f0cdd321c69"
)

SOURCE_ADAPTER_AUDIT_COMPLETE = "SOURCE_ADAPTER_AUDIT_COMPLETE"
SOURCE_ADAPTER_BOUND = "SOURCE_ADAPTER_BOUND"
SOURCE_ADAPTER_NOT_IMPLEMENTED = "SOURCE_ADAPTER_NOT_IMPLEMENTED"
SOURCE_ADAPTER_REQUEST_INVALID = "SOURCE_ADAPTER_REQUEST_INVALID"
SOURCE_ADAPTER_ROUTE_REJECTED = "SOURCE_ADAPTER_ROUTE_REJECTED"
SOURCE_ADAPTER_PROVIDER_EMPTY = "SOURCE_ADAPTER_PROVIDER_EMPTY"
SOURCE_ADAPTER_PROVIDER_FAILED = "SOURCE_ADAPTER_PROVIDER_FAILED"
SOURCE_ADAPTER_PROVENANCE_REJECTED = "SOURCE_ADAPTER_PROVENANCE_REJECTED"

IMPLEMENTATION_BOUND = "bound"
IMPLEMENTATION_CONTRACT_INCOMPATIBLE = "contract_incompatible"
IMPLEMENTATION_NOT_IMPLEMENTED = "not_implemented"
IMPLEMENTATION_OPTIONAL_UNCONFIGURED = "optional_unconfigured"
IMPLEMENTATION_RETIRED = "retired"
IMPLEMENTATION_STATUSES = {
    IMPLEMENTATION_BOUND,
    IMPLEMENTATION_CONTRACT_INCOMPATIBLE,
    IMPLEMENTATION_NOT_IMPLEMENTED,
    IMPLEMENTATION_OPTIONAL_UNCONFIGURED,
    IMPLEMENTATION_RETIRED,
}

UPSTREAM_ACTIVITY_NOT_CALLED = "not_called"
UPSTREAM_ACTIVITY_NOT_APPLICABLE = "not_applicable"
UPSTREAM_ACTIVITY_UNKNOWN = "unknown"


def _is_canonical_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and value == value.lower()
    )


@dataclass(frozen=True)
class SourceAdapterBinding:
    capability: str
    origin_source: str
    adapter: str
    source_version: str
    provider_key: str
    implementation_status: str
    legacy_implementation_present: bool

    def __post_init__(self) -> None:
        for field in (
            "capability",
            "origin_source",
            "adapter",
            "source_version",
            "implementation_status",
        ):
            value = getattr(self, field)
            if not _is_canonical_identifier(value):
                raise ValueError(f"source_adapter_identity_not_canonical:{field}")
        if not isinstance(self.provider_key, str):
            raise ValueError("source_adapter_field_invalid:provider_key")
        if self.provider_key != self.provider_key.strip():
            raise ValueError("source_adapter_provider_key_not_canonical")
        if type(self.legacy_implementation_present) is not bool:
            raise ValueError(
                "source_adapter_bool_invalid:legacy_implementation_present"
            )
        if self.implementation_status not in IMPLEMENTATION_STATUSES:
            raise ValueError(
                "source_adapter_implementation_status_invalid:"
                f"{self.implementation_status}"
            )
        if self.implementation_status == IMPLEMENTATION_BOUND:
            if not self.provider_key:
                raise ValueError("source_adapter_bound_provider_key_missing")
            if self.legacy_implementation_present:
                raise ValueError("source_adapter_bound_cannot_be_legacy")
        elif self.implementation_status == IMPLEMENTATION_CONTRACT_INCOMPATIBLE:
            if not self.provider_key or not self.legacy_implementation_present:
                raise ValueError("source_adapter_legacy_contract_invalid")
        elif self.provider_key or self.legacy_implementation_present:
            raise ValueError("source_adapter_unbound_provider_contract_invalid")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceProviderEnvelope:
    provider_key: str
    provider_callable: Callable[[], Iterable[Mapping[str, Any]]]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider_key, str)
            or not self.provider_key
            or self.provider_key != self.provider_key.strip()
        ):
            raise ValueError("source_provider_envelope_key_invalid")
        if not callable(self.provider_callable):
            raise ValueError("source_provider_envelope_callable_invalid")


def _identity(
    capability: str,
    origin_source: str,
    adapter: str,
    source_version: str,
) -> tuple[str, str, str, str]:
    return capability, origin_source, adapter, source_version


# These symbolic references prove only that a legacy implementation exists.
# None currently satisfies the zero-argument provider envelope plus complete B1
# provenance record contract, so none is executable through the production
# adapter registry.
_LEGACY_PROVIDER_KEYS = {
    _identity(
        "quote",
        "tencent",
        "direct_http",
        "qt.gtimg.cn~88_fields_v2026-07-30",
    ): "astock_client.AStockClient._tencent_quotes",
    _identity(
        "valuation",
        "tencent",
        "direct_http",
        "qt.gtimg.cn~88_fields_v2026-07-30",
    ): "astock_client.AStockClient._tencent_quotes",
    _identity(
        "trading_calendar",
        "tencent",
        "direct_http",
        "ifzq_fqkline_day_v2026-07-30",
    ): (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_trading_calendar"
    ),
    _identity(
        "daily_bar_qfq",
        "tencent",
        "direct_http",
        "ifzq_fqkline_qfqday_v2026-07-30",
    ): (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_qfq_daily_bars"
    ),
    _identity(
        "industry_snapshot",
        "eastmoney",
        "direct_http",
        "emweb_core+push2_board_v2026-07-30",
    ): (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_industry"
    ),
    _identity(
        "fund_flow",
        "eastmoney",
        "direct_http",
        "push2_fflow_kline_v2026-07-30",
    ): (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_eastmoney_fund_flow"
    ),
    _identity(
        "fund_flow",
        "sina",
        "direct_http",
        "sina_moneyflow_current_v2026-07-30",
    ): (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_sina_fund_flow"
    ),
    _identity(
        "global_news",
        "eastmoney",
        "direct_http",
        "np_weblist_724_v2026-07-30",
    ): (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_global_news"
    ),
    _identity(
        "global_news",
        "cls",
        "direct_http",
        "cls_telegraph_direct_v2026-08-24",
    ): "news_briefing.fetch_cls_telegraph",
    _identity(
        "stock_news",
        "eastmoney",
        "direct_http",
        "search_api_cms_old_v2026-07-30",
    ): (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_stock_news"
    ),
    _identity(
        "announcement",
        "cninfo",
        "direct_http",
        "cninfo_query_v2026-07-30",
    ): (
        "real_point_in_time_collectors.RealPointInTimeCollectors."
        "collect_announcements"
    ),
    _identity(
        "minute_bar",
        "tongdaxin",
        "mootdx",
        "mootdx_0.11.7_tdx_std_bars_1m_v2026-07-31",
    ): "minute_probe_sources.MootdxMinuteProbeCollectors.collect_minute_bars",
    _identity(
        "transaction",
        "tongdaxin",
        "mootdx",
        "mootdx_0.11.7_tdx_std_transaction_v2026-08-06",
    ): (
        "minute_probe_sources.MootdxMinuteProbeCollectors."
        "collect_transaction_evidence"
    ),
}


def _get_fixed_capability_registry() -> list[dict[str, Any]]:
    rows = get_source_capability_registry()
    registry_hash = compute_source_capability_registry_hash()
    if len(rows) != EXPECTED_CAPABILITY_REGISTRY_ENTRY_COUNT:
        raise ValueError("capability_registry_entry_count_drift")
    if registry_hash != EXPECTED_CAPABILITY_REGISTRY_HASH:
        raise ValueError("capability_registry_hash_drift")
    return rows


def _build_expected_production_bindings() -> tuple[SourceAdapterBinding, ...]:
    bindings = []
    for row in _get_fixed_capability_registry():
        identity = _identity(
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        )
        provider_key = _LEGACY_PROVIDER_KEYS.get(identity, "")
        if provider_key:
            implementation_status = IMPLEMENTATION_CONTRACT_INCOMPATIBLE
            legacy_present = True
        elif row["role"] == "retired":
            implementation_status = IMPLEMENTATION_RETIRED
            legacy_present = False
        elif row["adapter"] == "iwencai_openapi":
            implementation_status = IMPLEMENTATION_OPTIONAL_UNCONFIGURED
            legacy_present = False
        else:
            implementation_status = IMPLEMENTATION_NOT_IMPLEMENTED
            legacy_present = False
        bindings.append(
            SourceAdapterBinding(
                capability=row["capability"],
                origin_source=row["origin_source"],
                adapter=row["adapter"],
                source_version=row["source_version"],
                provider_key=provider_key,
                implementation_status=implementation_status,
                legacy_implementation_present=legacy_present,
            )
        )
    return tuple(bindings)


_EXPECTED_SOURCE_ADAPTER_BINDINGS = _build_expected_production_bindings()
SOURCE_ADAPTER_BINDINGS = _EXPECTED_SOURCE_ADAPTER_BINDINGS


def _canonicalize_source_adapter_bindings_for_test(
    entries: Iterable[SourceAdapterBinding | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    capability_index = _capability_index()
    fields = tuple(SourceAdapterBinding.__dataclass_fields__)
    rows = []
    seen = set()
    for value in entries:
        raw = value.as_dict() if isinstance(value, SourceAdapterBinding) else dict(value)
        binding = SourceAdapterBinding(
            **{field: raw.get(field) for field in fields}
        )
        row = binding.as_dict()
        identity = _binding_identity(row)
        if identity in seen:
            raise ValueError("source_adapter_binding_duplicate:" + "|".join(identity))
        seen.add(identity)
        capability = capability_index.get(identity)
        if capability is None:
            raise ValueError("source_adapter_identity_unknown:" + "|".join(identity))
        _validate_binding_policy(row, capability)
        rows.append(row)
    return sorted(rows, key=_adapter_sort_key)


def _validate_production_source_adapter_bindings(
    entries: Iterable[SourceAdapterBinding | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    actual = _canonicalize_source_adapter_bindings_for_test(entries)
    expected = _canonicalize_source_adapter_bindings_for_test(
        _EXPECTED_SOURCE_ADAPTER_BINDINGS
    )
    actual_identities = {_binding_identity(row) for row in actual}
    expected_identities = {_binding_identity(row) for row in expected}
    missing = sorted(expected_identities - actual_identities)
    added = sorted(actual_identities - expected_identities)
    if missing:
        raise ValueError(
            "production_source_adapter_binding_missing:"
            + ",".join("|".join(item) for item in missing)
        )
    if added:
        raise ValueError(
            "production_source_adapter_binding_added:"
            + ",".join("|".join(item) for item in added)
        )
    if len(actual) != len(expected):
        raise ValueError("production_source_adapter_binding_count_invalid")
    expected_by_identity = {_binding_identity(row): row for row in expected}
    for row in actual:
        identity = _binding_identity(row)
        if row != expected_by_identity[identity]:
            raise ValueError(
                "production_source_adapter_binding_modified:"
                + "|".join(identity)
            )
    return actual


def _validate_binding_policy(
    binding: Mapping[str, Any],
    capability: Mapping[str, Any],
) -> None:
    status = binding["implementation_status"]
    if capability["role"] == "retired":
        if status != IMPLEMENTATION_RETIRED:
            raise ValueError("retired_source_adapter_status_invalid")
        return
    if status == IMPLEMENTATION_RETIRED:
        raise ValueError("active_source_adapter_marked_retired")
    if capability["adapter"] == "iwencai_openapi":
        if status != IMPLEMENTATION_OPTIONAL_UNCONFIGURED:
            raise ValueError("iwencai_adapter_status_invalid")
        return
    if status == IMPLEMENTATION_OPTIONAL_UNCONFIGURED:
        raise ValueError("optional_unconfigured_adapter_identity_invalid")
    if status == IMPLEMENTATION_CONTRACT_INCOMPATIBLE:
        identity = _binding_identity(binding)
        if _LEGACY_PROVIDER_KEYS.get(identity) != binding["provider_key"]:
            raise ValueError("source_adapter_legacy_provider_binding_mismatch")


def get_source_adapter_registry() -> list[dict[str, Any]]:
    return _validate_production_source_adapter_bindings(
        SOURCE_ADAPTER_BINDINGS
    )


def compute_source_adapter_registry_hash() -> str:
    return _hash_adapter_bindings(get_source_adapter_registry())


def _compute_source_adapter_registry_hash_for_test(
    entries: Iterable[SourceAdapterBinding | Mapping[str, Any]],
) -> str:
    return _hash_adapter_bindings(
        _canonicalize_source_adapter_bindings_for_test(entries)
    )


def _hash_adapter_bindings(entries: Iterable[Mapping[str, Any]]) -> str:
    return stable_hash(
        {
            "adapter_registry_schema_version": ADAPTER_REGISTRY_SCHEMA_VERSION,
            "capability_registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "capability_registry_hash": compute_source_capability_registry_hash(),
            "bindings": list(entries),
        }
    )


def audit_source_adapters(
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = {} if environ is None else dict(environ)
    capability_rows = _capability_index()
    matrix = []
    for binding in get_source_adapter_registry():
        identity = _binding_identity(binding)
        capability = capability_rows[identity]
        route = route_source_capability(
            binding["capability"],
            origin_source=binding["origin_source"],
            adapter=binding["adapter"],
            require_hard_gate=False,
            environ=environment,
        )
        if route["status"] != "SOURCE_ROUTE_SELECTED":
            status = SOURCE_ADAPTER_ROUTE_REJECTED
        elif binding["implementation_status"] == IMPLEMENTATION_BOUND:
            status = SOURCE_ADAPTER_BOUND
        else:
            status = SOURCE_ADAPTER_NOT_IMPLEMENTED
        matrix.append(
            {
                **binding,
                "role": capability["role"],
                "enabled_by_policy": capability["enabled_by_policy"],
                "qualification_status": capability["qualification_status"],
                "qualification_progress": capability["qualification_progress"],
                "route_status": route["status"],
                "status": status,
                "hard_gate_authorized": False,
            }
        )
    return _safe_output(
        {
            "status": SOURCE_ADAPTER_AUDIT_COMPLETE,
            "execution_ok": True,
            "adapter_registry_schema_version": ADAPTER_REGISTRY_SCHEMA_VERSION,
            "adapter_registry_hash": compute_source_adapter_registry_hash(),
            "capability_registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "capability_registry_hash": compute_source_capability_registry_hash(),
            "adapter_entry_count": len(matrix),
            "bound_count": sum(
                row["implementation_status"] == IMPLEMENTATION_BOUND
                for row in matrix
            ),
            "legacy_implementation_present_count": sum(
                row["legacy_implementation_present"] for row in matrix
            ),
            "contract_incompatible_count": sum(
                row["implementation_status"]
                == IMPLEMENTATION_CONTRACT_INCOMPATIBLE
                for row in matrix
            ),
            "not_implemented_count": sum(
                row["implementation_status"] == IMPLEMENTATION_NOT_IMPLEMENTED
                for row in matrix
            ),
            "route_rejected_count": sum(
                row["status"] == SOURCE_ADAPTER_ROUTE_REJECTED for row in matrix
            ),
            "adapter_matrix": matrix,
        },
        provider_called=False,
        audit_only=True,
    )


def execute_source_adapter(
    capability: str,
    *,
    origin_source: str,
    adapter: str,
    source_version: str,
    provider_envelope: object,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return _execute_source_adapter_core(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        source_version=source_version,
        provider_envelope=provider_envelope,
        environ=environ,
        bindings=get_source_adapter_registry(),
        test_only=False,
    )


def _execute_source_adapter_with_bindings_for_test(
    capability: str,
    *,
    origin_source: str,
    adapter: str,
    source_version: str,
    provider_envelope: object,
    entries: Iterable[SourceAdapterBinding | Mapping[str, Any]],
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return _execute_source_adapter_core(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        source_version=source_version,
        provider_envelope=provider_envelope,
        environ=environ,
        bindings=_canonicalize_source_adapter_bindings_for_test(entries),
        test_only=True,
    )


def _execute_source_adapter_core(
    capability: str,
    *,
    origin_source: str,
    adapter: str,
    source_version: str,
    provider_envelope: object,
    environ: Mapping[str, str] | None,
    bindings: list[dict[str, Any]],
    test_only: bool,
) -> dict[str, Any]:
    requested = (capability, origin_source, adapter, source_version)
    if not all(_is_canonical_identifier(value) for value in requested):
        return _adapter_output(
            SOURCE_ADAPTER_REQUEST_INVALID,
            execution_ok=False,
            provider_called=False,
            capability=str(capability or ""),
            requested_identity=[str(value or "") for value in requested],
            rejection_reasons=["source_adapter_identity_not_canonical"],
            test_only=test_only,
        )

    route = route_source_capability(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        require_hard_gate=False,
        environ={} if environ is None else environ,
    )
    if route["status"] != "SOURCE_ROUTE_SELECTED":
        return _adapter_output(
            SOURCE_ADAPTER_ROUTE_REJECTED,
            execution_ok=True,
            provider_called=False,
            capability=capability,
            requested_identity=list(requested),
            route_status=route["status"],
            test_only=test_only,
        )

    binding = next(
        (row for row in bindings if _binding_identity(row) == requested),
        None,
    )
    if binding is None:
        return _adapter_output(
            SOURCE_ADAPTER_REQUEST_INVALID,
            execution_ok=False,
            provider_called=False,
            capability=capability,
            requested_identity=list(requested),
            rejection_reasons=["source_adapter_version_or_identity_mismatch"],
            route_status=route["status"],
            test_only=test_only,
        )
    if not isinstance(provider_envelope, SourceProviderEnvelope):
        return _adapter_output(
            SOURCE_ADAPTER_REQUEST_INVALID,
            execution_ok=False,
            provider_called=False,
            capability=capability,
            requested_identity=list(requested),
            rejection_reasons=["source_provider_envelope_required"],
            route_status=route["status"],
            test_only=test_only,
        )
    if (
        binding["provider_key"]
        and provider_envelope.provider_key != binding["provider_key"]
    ):
        return _adapter_output(
            SOURCE_ADAPTER_REQUEST_INVALID,
            execution_ok=False,
            provider_called=False,
            capability=capability,
            requested_identity=list(requested),
            requested_provider_key=provider_envelope.provider_key,
            expected_provider_key=binding["provider_key"],
            rejection_reasons=["source_adapter_provider_key_mismatch"],
            route_status=route["status"],
            test_only=test_only,
        )
    if binding["implementation_status"] != IMPLEMENTATION_BOUND:
        return _adapter_output(
            SOURCE_ADAPTER_NOT_IMPLEMENTED,
            execution_ok=True,
            provider_called=False,
            capability=capability,
            requested_identity=list(requested),
            binding=binding,
            rejection_reasons=[
                "source_adapter_contract_incompatible"
                if binding["implementation_status"]
                == IMPLEMENTATION_CONTRACT_INCOMPATIBLE
                else "source_adapter_not_implemented"
            ],
            route_status=route["status"],
            test_only=test_only,
        )
    try:
        payload = provider_envelope.provider_callable()
        if isinstance(payload, Mapping) or isinstance(payload, (str, bytes)):
            raise TypeError("provider_records_iterable_required")
        records = list(payload)
        if any(not isinstance(row, Mapping) for row in records):
            raise TypeError("provider_record_mapping_required")
    except Exception as exc:
        return _adapter_output(
            SOURCE_ADAPTER_PROVIDER_FAILED,
            execution_ok=False,
            provider_called=True,
            capability=capability,
            requested_identity=list(requested),
            binding=binding,
            provider_error_type=type(exc).__name__,
            route_status=route["status"],
            test_only=test_only,
        )

    if not records:
        return _adapter_output(
            SOURCE_ADAPTER_PROVIDER_EMPTY,
            execution_ok=True,
            provider_called=True,
            capability=capability,
            requested_identity=list(requested),
            record_count=0,
            binding=binding,
            route_status=route["status"],
            test_only=test_only,
        )

    provenance = validate_source_provenance_batch(
        capability,
        records,
        require_hard_gate=False,
        environ={} if environ is None else environ,
    )
    if provenance["status"] != "SOURCE_PROVENANCE_ACCEPTED":
        return _adapter_output(
            SOURCE_ADAPTER_PROVENANCE_REJECTED,
            execution_ok=True,
            provider_called=True,
            capability=capability,
            requested_identity=list(requested),
            record_count=len(records),
            binding=binding,
            provenance_validation=provenance,
            route_status=route["status"],
            test_only=test_only,
        )
    return _adapter_output(
        SOURCE_ADAPTER_BOUND,
        execution_ok=True,
        provider_called=True,
        capability=capability,
        requested_identity=list(requested),
        record_count=len(records),
        binding=binding,
        provenance_validation=provenance,
        route_status=route["status"],
        test_only=test_only,
    )


def _adapter_output(
    status: str,
    *,
    execution_ok: bool,
    provider_called: bool,
    **payload: Any,
) -> dict[str, Any]:
    return _safe_output(
        {
            "status": status,
            "execution_ok": bool(execution_ok),
            "adapter_registry_schema_version": ADAPTER_REGISTRY_SCHEMA_VERSION,
            "adapter_registry_hash": compute_source_adapter_registry_hash(),
            "capability_registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "capability_registry_hash": compute_source_capability_registry_hash(),
            "provider_called": provider_called,
            **payload,
        },
        provider_called=provider_called,
        audit_only=False,
    )


def _safe_output(
    payload: Mapping[str, Any],
    *,
    provider_called: bool,
    audit_only: bool,
) -> dict[str, Any]:
    result = dict(payload)
    result["require_hard_gate"] = False
    result["hard_gate_authorized"] = False
    result["automatic_configuration_change"] = False
    result["adapter_network_requests_made"] = 0
    if audit_only:
        result["network_requests_made"] = 0
        result["upstream_network_activity"] = (
            UPSTREAM_ACTIVITY_NOT_APPLICABLE
        )
    elif provider_called:
        result["network_requests_made"] = None
        result["upstream_network_activity"] = UPSTREAM_ACTIVITY_UNKNOWN
    else:
        result["network_requests_made"] = 0
        result["upstream_network_activity"] = UPSTREAM_ACTIVITY_NOT_CALLED
    result["data_ready"] = False
    result["candidates"] = []
    result["tickets"] = []
    result["orders"] = []
    return result


def _capability_index() -> dict[tuple[str, str, str, str], dict[str, Any]]:
    return {
        _identity(
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        ): row
        for row in _get_fixed_capability_registry()
    }


def _binding_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return _identity(
        str(row["capability"]),
        str(row["origin_source"]),
        str(row["adapter"]),
        str(row["source_version"]),
    )


def _adapter_sort_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row["capability"]),
        str(row["origin_source"]),
        str(row["adapter"]),
        str(row["source_version"]),
        str(row["provider_key"]),
    )


__all__ = [
    "ADAPTER_REGISTRY_SCHEMA_VERSION",
    "SOURCE_ADAPTER_AUDIT_COMPLETE",
    "SOURCE_ADAPTER_BINDINGS",
    "SOURCE_ADAPTER_BOUND",
    "SOURCE_ADAPTER_NOT_IMPLEMENTED",
    "SOURCE_ADAPTER_PROVIDER_EMPTY",
    "SOURCE_ADAPTER_PROVIDER_FAILED",
    "SOURCE_ADAPTER_PROVENANCE_REJECTED",
    "SOURCE_ADAPTER_REQUEST_INVALID",
    "SOURCE_ADAPTER_ROUTE_REJECTED",
    "SourceAdapterBinding",
    "SourceProviderEnvelope",
    "audit_source_adapters",
    "compute_source_adapter_registry_hash",
    "execute_source_adapter",
    "get_source_adapter_registry",
]
