from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Callable, Iterable, Mapping

from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    REGISTRY_SCHEMA_VERSION,
    compute_source_capability_registry_hash,
    get_source_capability_registry,
    route_source_capability,
    validate_source_provenance_batch,
)


ADAPTER_REGISTRY_SCHEMA_VERSION = "source_capability_adapter_registry_v3"
PREVIOUS_ADAPTER_REGISTRY_SCHEMA_VERSION = (
    "source_capability_adapter_registry_v3"
)
PREVIOUS_ADAPTER_REGISTRY_HASH = (
    "1eb8114cf3aa68bf85473a67513dfdd7a3ab5b32a464a66d5c4664163a4f8b2d"
)
ADAPTER_REGISTRY_HASH_CHANGE_REASON = (
    "schema_v3_activate_tencent_quote_valuation_shadow_bindings"
)
EXPECTED_CAPABILITY_REGISTRY_ENTRY_COUNT = 28
EXPECTED_CAPABILITY_REGISTRY_HASH = (
    "303db7cd50d8cc53e3729d69c7aeb3c053e203ba1885cecda1b10f0cdd321c69"
)

SOURCE_ADAPTER_AUDIT_COMPLETE = "SOURCE_ADAPTER_AUDIT_COMPLETE"
SOURCE_ADAPTER_BOUND = "SOURCE_ADAPTER_BOUND"
SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED = (
    "SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED"
)
SOURCE_ADAPTER_NOT_IMPLEMENTED = "SOURCE_ADAPTER_NOT_IMPLEMENTED"
SOURCE_ADAPTER_REQUEST_INVALID = "SOURCE_ADAPTER_REQUEST_INVALID"
SOURCE_ADAPTER_ROUTE_REJECTED = "SOURCE_ADAPTER_ROUTE_REJECTED"
SOURCE_ADAPTER_PROVIDER_EMPTY = "SOURCE_ADAPTER_PROVIDER_EMPTY"
SOURCE_ADAPTER_PROVIDER_FAILED = "SOURCE_ADAPTER_PROVIDER_FAILED"
SOURCE_ADAPTER_PROVENANCE_REJECTED = "SOURCE_ADAPTER_PROVENANCE_REJECTED"

IMPLEMENTATION_BOUND = "bound"
IMPLEMENTATION_CANDIDATE_NOT_ACTIVATED = "candidate_not_activated"
IMPLEMENTATION_CONTRACT_INCOMPATIBLE = "contract_incompatible"
IMPLEMENTATION_NOT_IMPLEMENTED = "not_implemented"
IMPLEMENTATION_OPTIONAL_UNCONFIGURED = "optional_unconfigured"
IMPLEMENTATION_RETIRED = "retired"
IMPLEMENTATION_STATUSES = {
    IMPLEMENTATION_BOUND,
    IMPLEMENTATION_CANDIDATE_NOT_ACTIVATED,
    IMPLEMENTATION_CONTRACT_INCOMPATIBLE,
    IMPLEMENTATION_NOT_IMPLEMENTED,
    IMPLEMENTATION_OPTIONAL_UNCONFIGURED,
    IMPLEMENTATION_RETIRED,
}

_PROVIDER_KEY_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)

UPSTREAM_ACTIVITY_NOT_CALLED = "not_called"
UPSTREAM_ACTIVITY_NOT_APPLICABLE = "not_applicable"
UPSTREAM_ACTIVITY_UNKNOWN = "unknown"

SELECTION_REGISTRY_SCOPE_PRODUCTION = "production"
SELECTION_REGISTRY_SCOPE_TEST_ONLY = "test_only"
SELECTION_REGISTRY_SCOPES = {
    SELECTION_REGISTRY_SCOPE_PRODUCTION,
    SELECTION_REGISTRY_SCOPE_TEST_ONLY,
}


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
    candidate_provider_key: str
    legacy_provider_key: str
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
            if not _is_canonical_identifier(value):
                raise ValueError(f"source_adapter_identity_not_canonical:{field}")
        provider_fields = (
            "provider_key",
            "candidate_provider_key",
            "legacy_provider_key",
        )
        for field in provider_fields:
            value = getattr(self, field)
            if not isinstance(value, str):
                raise ValueError(f"source_adapter_field_invalid:{field}")
            if value != value.strip() or (
                value and _PROVIDER_KEY_PATTERN.fullmatch(value) is None
            ):
                raise ValueError(
                    f"source_adapter_provider_key_not_canonical:{field}"
                )
        nonempty_keys = [getattr(self, field) for field in provider_fields]
        nonempty_keys = [value for value in nonempty_keys if value]
        if len(nonempty_keys) != len(set(nonempty_keys)):
            raise ValueError("source_adapter_provider_key_slots_duplicate")
        if self.implementation_status not in IMPLEMENTATION_STATUSES:
            raise ValueError(
                "source_adapter_implementation_status_invalid:"
                f"{self.implementation_status}"
            )
        if self.implementation_status == IMPLEMENTATION_BOUND:
            if not self.provider_key:
                raise ValueError("source_adapter_bound_provider_key_missing")
            if self.candidate_provider_key:
                raise ValueError("source_adapter_bound_candidate_key_invalid")
        elif (
            self.implementation_status
            == IMPLEMENTATION_CANDIDATE_NOT_ACTIVATED
        ):
            if self.provider_key or not self.candidate_provider_key:
                raise ValueError("source_adapter_candidate_contract_invalid")
        elif self.implementation_status == IMPLEMENTATION_CONTRACT_INCOMPATIBLE:
            if (
                self.provider_key
                or self.candidate_provider_key
                or not self.legacy_provider_key
            ):
                raise ValueError("source_adapter_legacy_contract_invalid")
        elif (
            self.provider_key
            or self.candidate_provider_key
            or self.legacy_provider_key
        ):
            raise ValueError("source_adapter_unbound_provider_contract_invalid")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["legacy_implementation_present"] = bool(
            self.legacy_provider_key
        )
        return result


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

_TENCENT_SHADOW_PROVIDER_KEYS = {
    _identity(
        "quote",
        "tencent",
        "direct_http",
        "qt.gtimg.cn~88_fields_v2026-07-30",
    ): (
        "tencent_direct_http_providers.TencentDirectHttpProviders."
        "collect_quote_records"
    ),
    _identity(
        "valuation",
        "tencent",
        "direct_http",
        "qt.gtimg.cn~88_fields_v2026-07-30",
    ): (
        "tencent_direct_http_providers.TencentDirectHttpProviders."
        "collect_valuation_records"
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
        legacy_provider_key = _LEGACY_PROVIDER_KEYS.get(identity, "")
        shadow_provider_key = _TENCENT_SHADOW_PROVIDER_KEYS.get(identity, "")
        if shadow_provider_key:
            implementation_status = IMPLEMENTATION_BOUND
        elif legacy_provider_key:
            implementation_status = IMPLEMENTATION_CONTRACT_INCOMPATIBLE
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
                provider_key=shadow_provider_key,
                candidate_provider_key="",
                legacy_provider_key=legacy_provider_key,
                implementation_status=implementation_status,
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
    derived_flag_not_supplied = object()
    for value in entries:
        if isinstance(value, SourceAdapterBinding):
            raw = {field: getattr(value, field) for field in fields}
            supplied_derived_legacy = derived_flag_not_supplied
        else:
            raw = dict(value)
            supplied_derived_legacy = raw.pop(
                "legacy_implementation_present",
                derived_flag_not_supplied,
            )
            if set(raw) != set(fields):
                raise ValueError("source_adapter_binding_fields_invalid")
        binding = SourceAdapterBinding(
            **{field: raw.get(field) for field in fields}
        )
        row = binding.as_dict()
        if (
            supplied_derived_legacy is not derived_flag_not_supplied
            and supplied_derived_legacy
            is not row["legacy_implementation_present"]
        ):
            raise ValueError(
                "source_adapter_derived_legacy_flag_mismatch"
            )
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
    identity = _binding_identity(binding)
    candidate_provider_key = binding["candidate_provider_key"]
    provider_key = binding["provider_key"]
    expected_shadow_key = _TENCENT_SHADOW_PROVIDER_KEYS.get(identity, "")
    for shadow_identity, reserved_key in _TENCENT_SHADOW_PROVIDER_KEYS.items():
        if (
            reserved_key in {provider_key, candidate_provider_key}
            and identity != shadow_identity
        ):
            raise ValueError("source_adapter_candidate_identity_mismatch")
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
    if status == IMPLEMENTATION_CANDIDATE_NOT_ACTIVATED:
        if (
            not expected_shadow_key
            or candidate_provider_key != expected_shadow_key
            or binding["legacy_provider_key"]
            != _LEGACY_PROVIDER_KEYS.get(identity, "")
        ):
            raise ValueError("source_adapter_candidate_binding_mismatch")
        return
    if status == IMPLEMENTATION_CONTRACT_INCOMPATIBLE:
        if (
            _LEGACY_PROVIDER_KEYS.get(identity)
            != binding["legacy_provider_key"]
        ):
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
    production_registry_hash = compute_source_adapter_registry_hash()
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
        elif (
            binding["implementation_status"]
            == IMPLEMENTATION_CANDIDATE_NOT_ACTIVATED
        ):
            status = SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
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
            "adapter_registry_hash": production_registry_hash,
            "production_adapter_registry_hash": production_registry_hash,
            "selection_adapter_registry_hash": production_registry_hash,
            "selection_registry_scope": SELECTION_REGISTRY_SCOPE_PRODUCTION,
            "previous_adapter_registry_schema_version": (
                PREVIOUS_ADAPTER_REGISTRY_SCHEMA_VERSION
            ),
            "previous_adapter_registry_hash": PREVIOUS_ADAPTER_REGISTRY_HASH,
            "adapter_registry_hash_change_reason": (
                ADAPTER_REGISTRY_HASH_CHANGE_REASON
            ),
            "capability_registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "capability_registry_hash": compute_source_capability_registry_hash(),
            "adapter_entry_count": len(matrix),
            "bound_count": sum(
                row["implementation_status"] == IMPLEMENTATION_BOUND
                for row in matrix
            ),
            "candidate_count": sum(
                row["implementation_status"]
                == IMPLEMENTATION_CANDIDATE_NOT_ACTIVATED
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
    production_registry_hash = compute_source_adapter_registry_hash()
    selection_registry_hash = _hash_adapter_bindings(bindings)
    selection_registry_scope = (
        SELECTION_REGISTRY_SCOPE_TEST_ONLY
        if test_only
        else SELECTION_REGISTRY_SCOPE_PRODUCTION
    )
    if (
        selection_registry_scope == SELECTION_REGISTRY_SCOPE_PRODUCTION
        and selection_registry_hash != production_registry_hash
    ):
        raise ValueError("production_adapter_selection_hash_mismatch")

    def output(
        status: str,
        *,
        execution_ok: bool,
        provider_called: bool,
        **payload: Any,
    ) -> dict[str, Any]:
        return _adapter_output(
            status,
            execution_ok=execution_ok,
            provider_called=provider_called,
            production_adapter_registry_hash=production_registry_hash,
            selection_adapter_registry_hash=selection_registry_hash,
            selection_registry_scope=selection_registry_scope,
            **payload,
        )

    requested = (capability, origin_source, adapter, source_version)
    if not all(_is_canonical_identifier(value) for value in requested):
        return output(
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
        return output(
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
        return output(
            SOURCE_ADAPTER_REQUEST_INVALID,
            execution_ok=False,
            provider_called=False,
            capability=capability,
            requested_identity=list(requested),
            rejection_reasons=["source_adapter_version_or_identity_mismatch"],
            route_status=route["status"],
            test_only=test_only,
        )
    if (
        binding["implementation_status"]
        == IMPLEMENTATION_CANDIDATE_NOT_ACTIVATED
    ):
        return output(
            SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED,
            execution_ok=True,
            provider_called=False,
            capability=capability,
            requested_identity=list(requested),
            binding=binding,
            rejection_reasons=["source_adapter_candidate_not_activated"],
            route_status=route["status"],
            test_only=test_only,
        )
    if not isinstance(provider_envelope, SourceProviderEnvelope):
        return output(
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
        return output(
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
        reason_by_status = {
            IMPLEMENTATION_CONTRACT_INCOMPATIBLE: (
                "source_adapter_contract_incompatible"
            ),
            IMPLEMENTATION_NOT_IMPLEMENTED: "source_adapter_not_implemented",
            IMPLEMENTATION_OPTIONAL_UNCONFIGURED: (
                "source_adapter_optional_unconfigured"
            ),
            IMPLEMENTATION_RETIRED: "source_adapter_retired",
        }
        return output(
            SOURCE_ADAPTER_NOT_IMPLEMENTED,
            execution_ok=True,
            provider_called=False,
            capability=capability,
            requested_identity=list(requested),
            binding=binding,
            rejection_reasons=[
                reason_by_status.get(
                    binding["implementation_status"],
                    "source_adapter_not_implemented",
                )
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
        return output(
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
        return output(
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

    record_capability_errors = []
    for index, record in enumerate(records):
        record_capability = record.get("capability")
        if record_capability is None or record_capability == "":
            record_capability_errors.append(
                {
                    "index": index,
                    "reason": "source_adapter_record_capability_missing",
                }
            )
        elif record_capability != capability:
            record_capability_errors.append(
                {
                    "index": index,
                    "reason": "source_adapter_record_capability_mismatch",
                    "actual_capability": record_capability,
                    "expected_capability": capability,
                }
            )
    if record_capability_errors:
        return output(
            SOURCE_ADAPTER_PROVENANCE_REJECTED,
            execution_ok=True,
            provider_called=True,
            capability=capability,
            requested_identity=list(requested),
            record_count=len(records),
            binding=binding,
            rejection_reasons=record_capability_errors,
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
        return output(
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
    return output(
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
    production_adapter_registry_hash: str,
    selection_adapter_registry_hash: str,
    selection_registry_scope: str,
    **payload: Any,
) -> dict[str, Any]:
    if selection_registry_scope not in SELECTION_REGISTRY_SCOPES:
        raise ValueError("selection_registry_scope_invalid")
    return _safe_output(
        {
            "status": status,
            "execution_ok": bool(execution_ok),
            "adapter_registry_schema_version": ADAPTER_REGISTRY_SCHEMA_VERSION,
            "adapter_registry_hash": selection_adapter_registry_hash,
            "production_adapter_registry_hash": (
                production_adapter_registry_hash
            ),
            "selection_adapter_registry_hash": selection_adapter_registry_hash,
            "selection_registry_scope": selection_registry_scope,
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
        str(row["candidate_provider_key"]),
        str(row["legacy_provider_key"]),
        str(row["implementation_status"]),
    )


__all__ = [
    "ADAPTER_REGISTRY_SCHEMA_VERSION",
    "ADAPTER_REGISTRY_HASH_CHANGE_REASON",
    "PREVIOUS_ADAPTER_REGISTRY_HASH",
    "PREVIOUS_ADAPTER_REGISTRY_SCHEMA_VERSION",
    "SOURCE_ADAPTER_AUDIT_COMPLETE",
    "SOURCE_ADAPTER_BINDINGS",
    "SOURCE_ADAPTER_BOUND",
    "SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED",
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
