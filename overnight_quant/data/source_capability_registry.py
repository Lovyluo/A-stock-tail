from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any, Iterable, Mapping

from overnight_quant.data.point_in_time import stable_hash


REGISTRY_SCHEMA_VERSION = "source_capability_registry_v1"
QUALIFICATION_PROGRESS_MOOTDX = "0/3"

ROLES = {
    "primary",
    "secondary",
    "audit_only",
    "optional_enrichment",
    "retired",
}
QUALIFICATION_STATUSES = {
    "not_required",
    "qualified",
    "unqualified",
    "retired",
}
WRAPPER_ADAPTERS = {"akshare"}
RETIRED_SOURCES = {"tushare", "ashare"}
PUBLISHED_AT_CAPABILITIES = {
    "announcement",
    "global_news",
    "research_report",
    "stock_news",
}


@dataclass(frozen=True)
class SourceCapability:
    capability: str
    origin_source: str
    adapter: str
    role: str
    time_critical: bool
    requires_secret: bool
    hard_gate_eligible: bool
    qualification_required: bool
    point_in_time_required: bool
    fallback_group: str
    source_version: str
    enabled_by_policy: bool
    qualification_status: str = "not_required"
    qualification_progress: str = ""
    is_proxy: bool = False
    is_wrapper: bool = False

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"source_role_invalid:{self.role}")
        if self.qualification_status not in QUALIFICATION_STATUSES:
            raise ValueError(
                "source_qualification_status_invalid:"
                f"{self.qualification_status}"
            )
        for field in (
            "capability",
            "origin_source",
            "adapter",
            "fallback_group",
            "source_version",
        ):
            if not str(getattr(self, field) or "").strip():
                raise ValueError(f"source_capability_field_missing:{field}")
        if self.role == "retired" and self.enabled_by_policy:
            raise ValueError("retired_source_cannot_be_enabled")
        if self.is_wrapper and self.adapter not in WRAPPER_ADAPTERS:
            raise ValueError("source_wrapper_adapter_invalid")
        if self.is_wrapper and self.hard_gate_eligible:
            raise ValueError("source_wrapper_cannot_enter_hard_gate")
        if self.is_proxy and self.hard_gate_eligible:
            raise ValueError("source_proxy_cannot_enter_hard_gate")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


SOURCE_CAPABILITIES = (
    SourceCapability(
        "quote",
        "tencent",
        "direct_http",
        "primary",
        True,
        False,
        True,
        False,
        True,
        "quote",
        "qt.gtimg.cn~88_fields_v2026-07-30",
        True,
    ),
    SourceCapability(
        "valuation",
        "tencent",
        "direct_http",
        "primary",
        True,
        False,
        False,
        False,
        True,
        "valuation",
        "qt.gtimg.cn~88_fields_v2026-07-30",
        True,
    ),
    SourceCapability(
        "trading_calendar",
        "tencent",
        "direct_http",
        "primary",
        False,
        False,
        True,
        False,
        True,
        "trading_calendar",
        "ifzq_fqkline_day_v2026-07-30",
        True,
    ),
    SourceCapability(
        "daily_bar_qfq",
        "tencent",
        "direct_http",
        "primary",
        False,
        False,
        True,
        False,
        True,
        "daily_bar_qfq",
        "ifzq_fqkline_qfqday_v2026-07-30",
        True,
    ),
    SourceCapability(
        "minute_bar",
        "tongdaxin",
        "mootdx",
        "audit_only",
        True,
        False,
        False,
        True,
        True,
        "minute_bar",
        "mootdx_0.11.7_tdx_std_bars_1m_v2026-07-31",
        True,
        "unqualified",
        QUALIFICATION_PROGRESS_MOOTDX,
    ),
    SourceCapability(
        "transaction",
        "tongdaxin",
        "mootdx",
        "audit_only",
        True,
        False,
        False,
        True,
        True,
        "transaction",
        "mootdx_0.11.7_tdx_std_transaction_v2026-08-06",
        True,
        "unqualified",
        QUALIFICATION_PROGRESS_MOOTDX,
    ),
    SourceCapability(
        "order_book",
        "tongdaxin",
        "mootdx",
        "audit_only",
        True,
        False,
        False,
        True,
        True,
        "order_book",
        "mootdx_0.11.7_tdx_std_quote_v2026-08-24",
        True,
        "unqualified",
        QUALIFICATION_PROGRESS_MOOTDX,
    ),
    SourceCapability(
        "announcement_summary",
        "tongdaxin",
        "mootdx",
        "audit_only",
        False,
        False,
        False,
        False,
        True,
        "announcement",
        "mootdx_0.11.7_f10_summary_v2026-08-24",
        True,
    ),
    SourceCapability(
        "company_profile",
        "tongdaxin",
        "mootdx",
        "audit_only",
        False,
        False,
        False,
        False,
        True,
        "company_profile",
        "mootdx_0.11.7_f10_profile_v2026-08-24",
        True,
    ),
    SourceCapability(
        "financial_snapshot",
        "tongdaxin",
        "mootdx",
        "audit_only",
        False,
        False,
        False,
        False,
        True,
        "financial_snapshot",
        "mootdx_0.11.7_finance_v2026-08-24",
        True,
    ),
    SourceCapability(
        "research_report",
        "eastmoney",
        "direct_http",
        "primary",
        False,
        False,
        False,
        False,
        True,
        "research_report",
        "eastmoney_reportapi_v2026-08-24",
        True,
    ),
    SourceCapability(
        "stock_news",
        "eastmoney",
        "direct_http",
        "primary",
        True,
        False,
        False,
        False,
        True,
        "stock_news",
        "search_api_cms_old_v2026-07-30",
        True,
    ),
    SourceCapability(
        "global_news",
        "eastmoney",
        "direct_http",
        "primary",
        True,
        False,
        False,
        False,
        True,
        "global_news",
        "np_weblist_724_v2026-07-30",
        True,
    ),
    SourceCapability(
        "industry_snapshot",
        "eastmoney",
        "direct_http",
        "secondary",
        True,
        False,
        False,
        True,
        True,
        "industry_snapshot",
        "emweb_core+push2_board_v2026-07-30",
        True,
        "unqualified",
    ),
    SourceCapability(
        "fund_flow",
        "eastmoney",
        "direct_http",
        "secondary",
        True,
        False,
        False,
        True,
        True,
        "fund_flow",
        "push2_fflow_kline_v2026-07-30",
        True,
        "unqualified",
    ),
    SourceCapability(
        "announcement",
        "cninfo",
        "direct_http",
        "primary",
        True,
        False,
        True,
        False,
        True,
        "announcement",
        "cninfo_query_v2026-07-30",
        True,
    ),
    SourceCapability(
        "global_news",
        "cls",
        "direct_http",
        "secondary",
        True,
        False,
        False,
        False,
        True,
        "global_news",
        "cls_telegraph_direct_v2026-08-24",
        True,
    ),
    SourceCapability(
        "financial_statements",
        "sina",
        "direct_http",
        "secondary",
        False,
        False,
        False,
        False,
        True,
        "financial_statements",
        "sina_financial_statements_v2026-08-24",
        True,
    ),
    SourceCapability(
        "fund_flow",
        "sina",
        "direct_http",
        "audit_only",
        True,
        False,
        False,
        False,
        True,
        "fund_flow",
        "sina_moneyflow_current_v2026-07-30",
        True,
        is_proxy=True,
    ),
    SourceCapability(
        "research_report",
        "eastmoney",
        "akshare",
        "optional_enrichment",
        False,
        False,
        False,
        False,
        True,
        "research_report",
        "eastmoney_reportapi_via_akshare_unqualified_v1",
        True,
        is_wrapper=True,
    ),
    SourceCapability(
        "stock_news",
        "eastmoney",
        "akshare",
        "optional_enrichment",
        True,
        False,
        False,
        False,
        True,
        "stock_news",
        "eastmoney_stock_news_via_akshare_unqualified_v1",
        True,
        is_wrapper=True,
    ),
    SourceCapability(
        "global_news",
        "eastmoney",
        "akshare",
        "optional_enrichment",
        True,
        False,
        False,
        False,
        True,
        "global_news",
        "eastmoney_global_news_via_akshare_unqualified_v1",
        True,
        is_wrapper=True,
    ),
    SourceCapability(
        "global_news",
        "cls",
        "akshare",
        "optional_enrichment",
        True,
        False,
        False,
        False,
        True,
        "global_news",
        "cls_global_news_via_akshare_unqualified_v1",
        True,
        is_wrapper=True,
    ),
    SourceCapability(
        "announcement",
        "cninfo",
        "akshare",
        "optional_enrichment",
        True,
        False,
        False,
        False,
        True,
        "announcement",
        "cninfo_announcement_via_akshare_unqualified_v1",
        True,
        is_wrapper=True,
    ),
    SourceCapability(
        "company_profile",
        "eastmoney",
        "akshare",
        "optional_enrichment",
        False,
        False,
        False,
        False,
        True,
        "company_profile",
        "eastmoney_profile_via_akshare_unqualified_v1",
        True,
        is_wrapper=True,
    ),
    SourceCapability(
        "semantic_research",
        "iwencai",
        "iwencai_openapi",
        "optional_enrichment",
        False,
        True,
        False,
        False,
        True,
        "semantic_research",
        "iwencai_openapi_report_search_v2",
        True,
    ),
    SourceCapability(
        "legacy_market_data",
        "tushare",
        "tushare",
        "retired",
        False,
        True,
        False,
        False,
        False,
        "retired_market_data",
        "retired_by_policy_v1",
        False,
        "retired",
    ),
    SourceCapability(
        "legacy_market_data",
        "ashare",
        "ashare",
        "retired",
        False,
        False,
        False,
        False,
        False,
        "retired_market_data",
        "retired_by_policy_v1",
        False,
        "retired",
    ),
)


def get_source_capability_registry() -> list[dict[str, Any]]:
    return canonicalize_source_capabilities(SOURCE_CAPABILITIES)


def build_source_capability_registry() -> list[dict[str, Any]]:
    return get_source_capability_registry()


def canonicalize_source_capabilities(
    entries: Iterable[SourceCapability | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    identities = set()
    expected_fields = tuple(SourceCapability.__dataclass_fields__)
    for value in entries:
        row = value.as_dict() if isinstance(value, SourceCapability) else dict(value)
        normalized = {field: row.get(field) for field in expected_fields}
        validated = SourceCapability(**normalized).as_dict()
        identity = _entry_identity(validated)
        if identity in identities:
            raise ValueError("source_capability_duplicate:" + "|".join(identity))
        identities.add(identity)
        rows.append(validated)
    return sorted(rows, key=_registry_sort_key)


def compute_source_capability_registry_hash(
    entries: Iterable[SourceCapability | Mapping[str, Any]] | None = None,
) -> str:
    canonical = canonicalize_source_capabilities(
        SOURCE_CAPABILITIES if entries is None else entries
    )
    return stable_hash(
        {
            "registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "entries": canonical,
        }
    )


def audit_source_capabilities(
    *,
    environ: Mapping[str, str] | None = None,
    entries: Iterable[SourceCapability | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    registry = canonicalize_source_capabilities(
        SOURCE_CAPABILITIES if entries is None else entries
    )
    environment = os.environ if environ is None else environ
    source_statuses = [
        _source_runtime_status(row, environment)
        for row in registry
    ]
    return _safe_output(
        {
            "status": "SOURCE_CAPABILITY_AUDIT_COMPLETE",
            "execution_ok": True,
            "registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "registry_hash": compute_source_capability_registry_hash(registry),
            "registry_entry_count": len(registry),
            "network_requests_made": 0,
            "automatic_configuration_change": False,
            "registry": registry,
            "source_statuses": source_statuses,
        }
    )


def route_source_capability(
    capability: str,
    *,
    origin_source: str = "",
    adapter: str = "",
    require_hard_gate: bool = False,
    environ: Mapping[str, str] | None = None,
    entries: Iterable[SourceCapability | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    registry = canonicalize_source_capabilities(
        SOURCE_CAPABILITIES if entries is None else entries
    )
    environment = os.environ if environ is None else environ
    requested_capability = _normalize_identifier(capability)
    requested_source = _normalize_identifier(origin_source)
    requested_adapter = _normalize_identifier(adapter)
    known_capabilities = {row["capability"] for row in registry}
    known_sources = {row["origin_source"] for row in registry}

    if requested_capability not in known_capabilities:
        return _route_rejection_output(
            "UNKNOWN_CAPABILITY",
            requested_capability,
            requested_source,
            requested_adapter,
            require_hard_gate,
            ["capability_not_registered"],
            registry,
        )
    if requested_source and requested_source not in known_sources:
        return _route_rejection_output(
            "UNKNOWN_SOURCE",
            requested_capability,
            requested_source,
            requested_adapter,
            require_hard_gate,
            ["origin_source_not_registered"],
            registry,
        )

    matches = [
        row
        for row in registry
        if row["capability"] == requested_capability
        and (
            not requested_source
            or row["origin_source"] == requested_source
        )
        and (
            not requested_adapter
            or row["adapter"] == requested_adapter
        )
    ]
    if not matches:
        return _route_rejection_output(
            "SOURCE_CAPABILITY_UNAVAILABLE",
            requested_capability,
            requested_source,
            requested_adapter,
            require_hard_gate,
            ["requested_source_adapter_has_no_capability"],
            registry,
        )

    considered = []
    for row in sorted(matches, key=_routing_sort_key):
        rejection_status, reasons = _routing_rejection(
            row,
            require_hard_gate=require_hard_gate,
            environ=environment,
        )
        considered.append(
            {
                "origin_source": row["origin_source"],
                "adapter": row["adapter"],
                "source_version": row["source_version"],
                "status": rejection_status or "ELIGIBLE",
                "reasons": reasons,
            }
        )
        if rejection_status:
            continue
        route_scope = (
            "hard_gate"
            if require_hard_gate
            else "audit_only"
            if row["role"] == "audit_only"
            else "research"
        )
        return _safe_output(
            {
                "status": "SOURCE_ROUTE_SELECTED",
                "execution_ok": True,
                "capability": requested_capability,
                "requested_origin_source": requested_source,
                "requested_adapter": requested_adapter,
                "require_hard_gate": bool(require_hard_gate),
                "route_scope": route_scope,
                "hard_gate_authorized": bool(require_hard_gate),
                "selected_source": dict(row),
                "considered_sources": considered,
                "registry_hash": compute_source_capability_registry_hash(registry),
                "source_count": 1,
            }
        )

    statuses = {item["status"] for item in considered}
    status = (
        next(iter(statuses))
        if len(statuses) == 1
        else "NO_ELIGIBLE_SOURCE"
    )
    reasons = sorted(
        {
            reason
            for item in considered
            for reason in item["reasons"]
        }
    )
    result = _route_rejection_output(
        status,
        requested_capability,
        requested_source,
        requested_adapter,
        require_hard_gate,
        reasons,
        registry,
    )
    result["considered_sources"] = considered
    return result


def validate_source_provenance_batch(
    capability: str,
    records: Iterable[Mapping[str, Any]],
    *,
    require_hard_gate: bool = False,
    environ: Mapping[str, str] | None = None,
    entries: Iterable[SourceCapability | Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = [dict(row) for row in records]
    if not rows:
        return _safe_output(
            {
                "status": "PROVENANCE_BATCH_EMPTY",
                "execution_ok": True,
                "capability": _normalize_identifier(capability),
                "record_count": 0,
                "provenance": None,
            }
        )

    identity_fields = ("origin_source", "adapter", "source_version")
    missing = sorted(
        {
            field
            for row in rows
            for field in identity_fields
            if not str(row.get(field) or "").strip()
        }
    )
    if missing:
        return _safe_output(
            {
                "status": "PROVENANCE_FIELDS_MISSING",
                "execution_ok": True,
                "capability": _normalize_identifier(capability),
                "record_count": len(rows),
                "missing_fields": missing,
                "provenance": None,
            }
        )

    identities = sorted(
        {
            tuple(str(row[field]).strip().lower() for field in identity_fields)
            for row in rows
        }
    )
    if len(identities) != 1:
        return _safe_output(
            {
                "status": "MIXED_SOURCE_PROVENANCE_REJECTED",
                "execution_ok": True,
                "capability": _normalize_identifier(capability),
                "record_count": len(rows),
                "source_identity_count": len(identities),
                "provenance": None,
            }
        )

    origin_source, adapter, source_version = identities[0]
    route = route_source_capability(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        require_hard_gate=require_hard_gate,
        environ=environ,
        entries=entries,
    )
    selected = route.get("selected_source") or {}
    if route["status"] != "SOURCE_ROUTE_SELECTED":
        return _safe_output(
            {
                "status": route["status"],
                "execution_ok": True,
                "capability": _normalize_identifier(capability),
                "record_count": len(rows),
                "route_reasons": route.get("rejection_reasons") or [],
                "provenance": None,
            }
        )
    if selected.get("source_version") != source_version:
        return _safe_output(
            {
                "status": "SOURCE_VERSION_REJECTED",
                "execution_ok": True,
                "capability": _normalize_identifier(capability),
                "record_count": len(rows),
                "provenance": None,
            }
        )

    required_fields = {"request_hash", "raw_hash"}
    if selected.get("point_in_time_required"):
        required_fields.update({"event_time", "observed_at", "available_at"})
    if _normalize_identifier(capability) in PUBLISHED_AT_CAPABILITIES:
        required_fields.add("published_at")
    missing_contract = sorted(
        {
            field
            for row in rows
            for field in required_fields
            if not str(row.get(field) or "").strip()
        }
    )
    if missing_contract:
        return _safe_output(
            {
                "status": "PROVENANCE_CONTRACT_INCOMPLETE",
                "execution_ok": True,
                "capability": _normalize_identifier(capability),
                "record_count": len(rows),
                "missing_fields": missing_contract,
                "provenance": None,
            }
        )

    return _safe_output(
        {
            "status": "SOURCE_PROVENANCE_ACCEPTED",
            "execution_ok": True,
            "capability": _normalize_identifier(capability),
            "record_count": len(rows),
            "hard_gate_authorized": route["hard_gate_authorized"],
            "provenance": {
                "origin_source": origin_source,
                "adapter": adapter,
                "source_version": source_version,
                "record_hash": stable_hash(rows),
            },
        }
    )


def _source_runtime_status(
    row: Mapping[str, Any],
    environ: Mapping[str, str],
) -> dict[str, Any]:
    status, reasons = _routing_rejection(
        row,
        require_hard_gate=False,
        environ=environ,
    )
    if not status:
        status = (
            "UNQUALIFIED"
            if row["qualification_required"]
            and row["qualification_status"] != "qualified"
            else "OPTIONAL_CONFIGURED"
            if row["adapter"] == "iwencai_openapi"
            else "AUDIT_ONLY"
            if row["role"] == "audit_only"
            else "POLICY_REGISTERED"
        )
    result = {
        "capability": row["capability"],
        "origin_source": row["origin_source"],
        "adapter": row["adapter"],
        "role": row["role"],
        "status": status,
        "reasons": reasons,
        "hard_gate_authorized": False,
    }
    if row["adapter"] == "iwencai_openapi":
        result["secret_configured"] = bool(
            str(environ.get("IWENCAI_API_KEY") or "").strip()
        )
        result["base_url_configured"] = bool(
            str(environ.get("IWENCAI_BASE_URL") or "").strip()
        )
    return result


def _routing_rejection(
    row: Mapping[str, Any],
    *,
    require_hard_gate: bool,
    environ: Mapping[str, str],
) -> tuple[str, list[str]]:
    if (
        not row["enabled_by_policy"]
        or row["role"] == "retired"
        or row["origin_source"] in RETIRED_SOURCES
    ):
        return "SOURCE_DISABLED_BY_POLICY", ["retired_or_disabled_by_policy"]
    if row["adapter"] == "akshare":
        return "OPTIONAL_ADAPTER_UNAVAILABLE", [
            "akshare_not_installed_or_enabled_in_b1"
        ]
    if row["adapter"] == "iwencai_openapi":
        key_ready = bool(str(environ.get("IWENCAI_API_KEY") or "").strip())
        url_ready = bool(str(environ.get("IWENCAI_BASE_URL") or "").strip())
        if not key_ready or not url_ready:
            missing = []
            if not key_ready:
                missing.append("iwencai_api_key_missing")
            if not url_ready:
                missing.append("iwencai_base_url_missing")
            return "OPTIONAL_UNCONFIGURED", missing
    if not require_hard_gate:
        return "", []

    reasons = []
    if (
        row["qualification_required"]
        and row["qualification_status"] != "qualified"
    ):
        reasons.append("source_unqualified")
    if row["role"] not in {"primary", "secondary"}:
        reasons.append("source_role_not_hard_gate_eligible")
    if not row["hard_gate_eligible"]:
        reasons.append("hard_gate_eligible_false")
    if row["is_proxy"]:
        reasons.append("proxy_source_forbidden")
    if row["is_wrapper"]:
        reasons.append("wrapper_source_forbidden")
    if reasons:
        status = (
            "SOURCE_UNQUALIFIED"
            if "source_unqualified" in reasons
            else "SOURCE_NOT_HARD_GATE_ELIGIBLE"
        )
        return status, sorted(set(reasons))
    return "", []


def _route_rejection_output(
    status: str,
    capability: str,
    origin_source: str,
    adapter: str,
    require_hard_gate: bool,
    reasons: list[str],
    registry: list[dict[str, Any]],
) -> dict[str, Any]:
    return _safe_output(
        {
            "status": status,
            "execution_ok": True,
            "capability": capability,
            "requested_origin_source": origin_source,
            "requested_adapter": adapter,
            "require_hard_gate": bool(require_hard_gate),
            "hard_gate_authorized": False,
            "selected_source": None,
            "considered_sources": [],
            "rejection_reasons": sorted(set(reasons)),
            "registry_hash": compute_source_capability_registry_hash(registry),
            "source_count": 0,
        }
    )


def _safe_output(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.setdefault("automatic_configuration_change", False)
    result.setdefault("network_requests_made", 0)
    result["data_ready"] = False
    result["candidates"] = []
    result["tickets"] = []
    result["orders"] = []
    return result


def _normalize_identifier(value: Any) -> str:
    return str(value or "").strip().lower()


def _entry_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row["capability"]),
        str(row["origin_source"]),
        str(row["adapter"]),
        str(row["source_version"]),
    )


def _registry_sort_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row["capability"]),
        str(row["fallback_group"]),
        str(row["origin_source"]),
        str(row["adapter"]),
        str(row["source_version"]),
    )


def _routing_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    role_priority = {
        "primary": 0,
        "secondary": 1,
        "optional_enrichment": 2,
        "audit_only": 3,
        "retired": 4,
    }
    adapter_priority = {
        "direct_http": 0,
        "mootdx": 1,
        "iwencai_openapi": 2,
        "akshare": 3,
    }
    return (
        role_priority.get(str(row["role"]), 99),
        adapter_priority.get(str(row["adapter"]), 99),
        str(row["origin_source"]),
        str(row["source_version"]),
    )


__all__ = [
    "QUALIFICATION_PROGRESS_MOOTDX",
    "REGISTRY_SCHEMA_VERSION",
    "SOURCE_CAPABILITIES",
    "SourceCapability",
    "audit_source_capabilities",
    "build_source_capability_registry",
    "canonicalize_source_capabilities",
    "compute_source_capability_registry_hash",
    "get_source_capability_registry",
    "route_source_capability",
    "validate_source_provenance_batch",
]
