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


ADAPTER_REGISTRY_SCHEMA_VERSION = "source_capability_adapter_registry_v1"

SOURCE_ADAPTER_AUDIT_COMPLETE = "SOURCE_ADAPTER_AUDIT_COMPLETE"
SOURCE_ADAPTER_BOUND = "SOURCE_ADAPTER_BOUND"
SOURCE_ADAPTER_NOT_IMPLEMENTED = "SOURCE_ADAPTER_NOT_IMPLEMENTED"
SOURCE_ADAPTER_REQUEST_INVALID = "SOURCE_ADAPTER_REQUEST_INVALID"
SOURCE_ADAPTER_ROUTE_REJECTED = "SOURCE_ADAPTER_ROUTE_REJECTED"
SOURCE_ADAPTER_PROVIDER_EMPTY = "SOURCE_ADAPTER_PROVIDER_EMPTY"
SOURCE_ADAPTER_PROVIDER_FAILED = "SOURCE_ADAPTER_PROVIDER_FAILED"
SOURCE_ADAPTER_PROVENANCE_REJECTED = "SOURCE_ADAPTER_PROVENANCE_REJECTED"

IMPLEMENTATION_BOUND = "bound"
IMPLEMENTATION_NOT_IMPLEMENTED = "not_implemented"
IMPLEMENTATION_OPTIONAL_UNCONFIGURED = "optional_unconfigured"
IMPLEMENTATION_RETIRED = "retired"
IMPLEMENTATION_STATUSES = {
    IMPLEMENTATION_BOUND,
    IMPLEMENTATION_NOT_IMPLEMENTED,
    IMPLEMENTATION_OPTIONAL_UNCONFIGURED,
    IMPLEMENTATION_RETIRED,
}


@dataclass(frozen=True)
class SourceAdapterBinding:
    capability: str
    origin_source: str
    adapter: str
    source_version: str
    provider_key: str
    implementation_status: str

    def __post_init__(self) -> None:
        for field in (
            "capability",
            "origin_source",
            "adapter",
            "source_version",
            "implementation_status",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"source_adapter_field_invalid:{field}")
        if not isinstance(self.provider_key, str):
            raise ValueError("source_adapter_field_invalid:provider_key")
        if self.implementation_status not in IMPLEMENTATION_STATUSES:
            raise ValueError(
                "source_adapter_implementation_status_invalid:"
                f"{self.implementation_status}"
            )
        if self.implementation_status == IMPLEMENTATION_BOUND:
            if not self.provider_key.strip():
                raise ValueError("source_adapter_bound_provider_key_missing")
        elif self.provider_key.strip():
            raise ValueError("source_adapter_unbound_provider_key_forbidden")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _identity(
    capability: str,
    origin_source: str,
    adapter: str,
    source_version: str,
) -> tuple[str, str, str, str]:
    return (
        str(capability).strip().lower(),
        str(origin_source).strip().lower(),
        str(adapter).strip().lower(),
        str(source_version).strip().lower(),
    )


# These are symbolic references to implementations already present in the
# repository. This module never imports, instantiates, or calls them directly.
_BOUND_PROVIDER_KEYS = {
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


def _default_bindings() -> tuple[SourceAdapterBinding, ...]:
    bindings = []
    for row in get_source_capability_registry():
        identity = _identity(
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        )
        provider_key = _BOUND_PROVIDER_KEYS.get(identity, "")
        if provider_key:
            implementation_status = IMPLEMENTATION_BOUND
        elif row["role"] == "retired":
            implementation_status = IMPLEMENTATION_RETIRED
        elif row["adapter"] == "iwencai_openapi":
            implementation_status = IMPLEMENTATION_OPTIONAL_UNCONFIGURED
        else:
            implementation_status = IMPLEMENTATION_NOT_IMPLEMENTED
        bindings.append(
            SourceAdapterBinding(
                capability=row["capability"],
                origin_source=row["origin_source"],
                adapter=row["adapter"],
                source_version=row["source_version"],
                provider_key=provider_key,
                implementation_status=implementation_status,
            )
        )
    return tuple(bindings)


SOURCE_ADAPTER_BINDINGS = _default_bindings()


def canonicalize_source_adapter_bindings(
    entries: Iterable[SourceAdapterBinding | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    capability_index = {
        _identity(
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        ): row
        for row in get_source_capability_registry()
    }
    fields = tuple(SourceAdapterBinding.__dataclass_fields__)
    rows = []
    seen = set()
    for value in entries:
        raw = value.as_dict() if isinstance(value, SourceAdapterBinding) else dict(value)
        binding = SourceAdapterBinding(
            **{field: raw.get(field) for field in fields}
        )
        row = binding.as_dict()
        identity = _identity(
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        )
        if identity in seen:
            raise ValueError("source_adapter_binding_duplicate:" + "|".join(identity))
        seen.add(identity)
        capability = capability_index.get(identity)
        if capability is None:
            raise ValueError("source_adapter_identity_unknown:" + "|".join(identity))
        _validate_binding_policy(row, capability)
        rows.append(row)
    return sorted(rows, key=_adapter_sort_key)


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
    if status == IMPLEMENTATION_BOUND:
        identity = _identity(
            binding["capability"],
            binding["origin_source"],
            binding["adapter"],
            binding["source_version"],
        )
        if _BOUND_PROVIDER_KEYS.get(identity) != binding["provider_key"]:
            raise ValueError("source_adapter_provider_binding_mismatch")


def get_source_adapter_registry() -> list[dict[str, Any]]:
    return canonicalize_source_adapter_bindings(SOURCE_ADAPTER_BINDINGS)


def compute_source_adapter_registry_hash(
    entries: Iterable[SourceAdapterBinding | Mapping[str, Any]] | None = None,
) -> str:
    rows = canonicalize_source_adapter_bindings(
        SOURCE_ADAPTER_BINDINGS if entries is None else entries
    )
    return stable_hash(
        {
            "adapter_registry_schema_version": ADAPTER_REGISTRY_SCHEMA_VERSION,
            "capability_registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "capability_registry_hash": compute_source_capability_registry_hash(),
            "bindings": rows,
        }
    )


def audit_source_adapters(
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = {} if environ is None else dict(environ)
    capability_rows = {
        _identity(
            row["capability"],
            row["origin_source"],
            row["adapter"],
            row["source_version"],
        ): row
        for row in get_source_capability_registry()
    }
    matrix = []
    for binding in get_source_adapter_registry():
        identity = _identity(
            binding["capability"],
            binding["origin_source"],
            binding["adapter"],
            binding["source_version"],
        )
        capability = capability_rows[identity]
        route = route_source_capability(
            binding["capability"],
            origin_source=binding["origin_source"],
            adapter=binding["adapter"],
            require_hard_gate=False,
            environ=environment,
        )
        if binding["implementation_status"] == IMPLEMENTATION_BOUND:
            status = (
                SOURCE_ADAPTER_BOUND
                if route["status"] == "SOURCE_ROUTE_SELECTED"
                else SOURCE_ADAPTER_ROUTE_REJECTED
            )
        elif route["status"] != "SOURCE_ROUTE_SELECTED":
            status = SOURCE_ADAPTER_ROUTE_REJECTED
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
                row["status"] == SOURCE_ADAPTER_BOUND for row in matrix
            ),
            "not_implemented_count": sum(
                row["status"] == SOURCE_ADAPTER_NOT_IMPLEMENTED for row in matrix
            ),
            "route_rejected_count": sum(
                row["status"] == SOURCE_ADAPTER_ROUTE_REJECTED for row in matrix
            ),
            "adapter_matrix": matrix,
        }
    )


def execute_source_adapter(
    capability: str,
    *,
    origin_source: str,
    adapter: str,
    source_version: str,
    provider: Callable[[], Iterable[Mapping[str, Any]]] | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    requested = _identity(
        capability,
        origin_source,
        adapter,
        source_version,
    )
    if not all(requested):
        return _adapter_output(
            SOURCE_ADAPTER_REQUEST_INVALID,
            execution_ok=False,
            capability=requested[0],
            requested_identity=list(requested),
            rejection_reasons=["source_adapter_identity_incomplete"],
        )

    route = route_source_capability(
        requested[0],
        origin_source=requested[1],
        adapter=requested[2],
        require_hard_gate=False,
        environ={} if environ is None else environ,
    )
    if route["status"] != "SOURCE_ROUTE_SELECTED":
        return _adapter_output(
            SOURCE_ADAPTER_ROUTE_REJECTED,
            execution_ok=True,
            capability=requested[0],
            requested_identity=list(requested),
            provider_called=False,
            route_status=route["status"],
            route=route,
        )

    binding = next(
        (
            row
            for row in get_source_adapter_registry()
            if _identity(
                row["capability"],
                row["origin_source"],
                row["adapter"],
                row["source_version"],
            )
            == requested
        ),
        None,
    )
    if binding is None:
        return _adapter_output(
            SOURCE_ADAPTER_REQUEST_INVALID,
            execution_ok=False,
            capability=requested[0],
            requested_identity=list(requested),
            provider_called=False,
            rejection_reasons=["source_adapter_version_or_identity_mismatch"],
            route=route,
        )
    if binding["implementation_status"] != IMPLEMENTATION_BOUND:
        return _adapter_output(
            SOURCE_ADAPTER_NOT_IMPLEMENTED,
            execution_ok=True,
            capability=requested[0],
            requested_identity=list(requested),
            provider_called=False,
            binding=binding,
            route=route,
        )
    if not callable(provider):
        return _adapter_output(
            SOURCE_ADAPTER_REQUEST_INVALID,
            execution_ok=False,
            capability=requested[0],
            requested_identity=list(requested),
            provider_called=False,
            binding=binding,
            rejection_reasons=["provider_callable_required"],
            route=route,
        )

    try:
        payload = provider()
        if isinstance(payload, Mapping) or isinstance(payload, (str, bytes)):
            raise TypeError("provider_records_iterable_required")
        records = list(payload)
        if any(not isinstance(row, Mapping) for row in records):
            raise TypeError("provider_record_mapping_required")
    except Exception as exc:
        return _adapter_output(
            SOURCE_ADAPTER_PROVIDER_FAILED,
            execution_ok=False,
            capability=requested[0],
            requested_identity=list(requested),
            provider_called=True,
            binding=binding,
            provider_error_type=type(exc).__name__,
            route=route,
        )

    if not records:
        return _adapter_output(
            SOURCE_ADAPTER_PROVIDER_EMPTY,
            execution_ok=True,
            capability=requested[0],
            requested_identity=list(requested),
            provider_called=True,
            record_count=0,
            binding=binding,
            route=route,
        )

    provenance = validate_source_provenance_batch(
        requested[0],
        records,
        require_hard_gate=False,
        environ={} if environ is None else environ,
    )
    if provenance["status"] != "SOURCE_PROVENANCE_ACCEPTED":
        return _adapter_output(
            SOURCE_ADAPTER_PROVENANCE_REJECTED,
            execution_ok=True,
            capability=requested[0],
            requested_identity=list(requested),
            provider_called=True,
            record_count=len(records),
            binding=binding,
            provenance=provenance,
            route=route,
        )
    return _adapter_output(
        SOURCE_ADAPTER_BOUND,
        execution_ok=True,
        capability=requested[0],
        requested_identity=list(requested),
        provider_called=True,
        record_count=len(records),
        binding=binding,
        provenance=provenance,
        route=route,
    )


def _adapter_output(
    status: str,
    *,
    execution_ok: bool,
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
            **payload,
        }
    )


def _safe_output(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["require_hard_gate"] = False
    result["hard_gate_authorized"] = False
    result["automatic_configuration_change"] = False
    result["network_requests_made"] = 0
    result["data_ready"] = False
    result["candidates"] = []
    result["tickets"] = []
    result["orders"] = []
    return result


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
    "audit_source_adapters",
    "canonicalize_source_adapter_bindings",
    "compute_source_adapter_registry_hash",
    "execute_source_adapter",
    "get_source_adapter_registry",
]
