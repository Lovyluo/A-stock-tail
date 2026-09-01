from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_BOUND,
    SourceProviderEnvelope,
    execute_source_adapter,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_ADAPTER,
    TENCENT_ORIGIN_SOURCE,
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_SOURCE_VERSION,
    TENCENT_VALUATION_PROVIDER_KEY,
    TencentDirectHttpProviders,
    TencentProviderContractError,
    TencentTransport,
    TencentUrllibTransport,
)


TENCENT_SHADOW_SNAPSHOT_CONTRACT_VERSION = (
    "tencent_read_only_shadow_snapshot_v1"
)
TENCENT_SHADOW_SNAPSHOT_READY = "TENCENT_SHADOW_SNAPSHOT_READY"
TENCENT_SHADOW_SNAPSHOT_INCOMPLETE = "TENCENT_SHADOW_SNAPSHOT_INCOMPLETE"
TENCENT_SHADOW_SNAPSHOT_NETWORK_NOT_REQUESTED = (
    "TENCENT_SHADOW_SNAPSHOT_NETWORK_NOT_REQUESTED"
)
TENCENT_SHADOW_SNAPSHOT_REQUEST_INVALID = (
    "TENCENT_SHADOW_SNAPSHOT_REQUEST_INVALID"
)


def build_tencent_shadow_snapshot(
    codes: Iterable[str],
    *,
    network: bool,
    transport: TencentTransport | None = None,
    clock: Callable[[], datetime] | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Build one display-only Tencent quote and valuation snapshot."""
    requested_codes = tuple(str(code) for code in codes)
    if type(network) is not bool:
        return _safe_result(
            TENCENT_SHADOW_SNAPSHOT_REQUEST_INVALID,
            execution_ok=False,
            network_requested=False,
            requested_codes=sorted(requested_codes),
            reason="network_flag_must_be_bool",
            network_requests_made=0,
        )
    if not network:
        return _safe_result(
            TENCENT_SHADOW_SNAPSHOT_NETWORK_NOT_REQUESTED,
            execution_ok=True,
            network_requested=False,
            requested_codes=sorted(requested_codes),
            reason="explicit_network_flag_required",
            network_requests_made=0,
        )

    active_transport = transport or TencentUrllibTransport()
    active_clock = clock or (lambda: datetime.now(CN_TZ))
    try:
        provider = TencentDirectHttpProviders(
            requested_codes,
            transport=active_transport,
            clock=active_clock,
            timeout_seconds=timeout_seconds,
        )
    except (TencentProviderContractError, TypeError, ValueError) as exc:
        return _safe_result(
            TENCENT_SHADOW_SNAPSHOT_REQUEST_INVALID,
            execution_ok=False,
            network_requested=True,
            requested_codes=sorted(requested_codes),
            reason=getattr(exc, "code", type(exc).__name__),
            network_requests_made=_request_count(active_transport),
        )

    records_by_capability: dict[str, list[dict[str, Any]]] = {}

    def collect(capability: str) -> list[dict[str, Any]]:
        method = (
            provider.collect_quote_records
            if capability == "quote"
            else provider.collect_valuation_records
        )
        records = method()
        records_by_capability[capability] = records
        return records

    adapter_results = {
        "quote": execute_source_adapter(
            "quote",
            origin_source=TENCENT_ORIGIN_SOURCE,
            adapter=TENCENT_ADAPTER,
            source_version=TENCENT_SOURCE_VERSION,
            provider_envelope=SourceProviderEnvelope(
                provider_key=TENCENT_QUOTE_PROVIDER_KEY,
                provider_callable=lambda: collect("quote"),
            ),
            environ={},
        ),
        "valuation": execute_source_adapter(
            "valuation",
            origin_source=TENCENT_ORIGIN_SOURCE,
            adapter=TENCENT_ADAPTER,
            source_version=TENCENT_SOURCE_VERSION,
            provider_envelope=SourceProviderEnvelope(
                provider_key=TENCENT_VALUATION_PROVIDER_KEY,
                provider_callable=lambda: collect("valuation"),
            ),
            environ={},
        ),
    }
    ready = all(
        result.get("status") == SOURCE_ADAPTER_BOUND
        for result in adapter_results.values()
    )
    rows = _merge_records(records_by_capability) if ready else []
    expected_codes = sorted(provider.codes)
    ready = ready and [row["code"] for row in rows] == expected_codes
    status = (
        TENCENT_SHADOW_SNAPSHOT_READY
        if ready
        else TENCENT_SHADOW_SNAPSHOT_INCOMPLETE
    )
    collected_at = max(
        (
            str(record.get("available_at") or "")
            for records in records_by_capability.values()
            for record in records
        ),
        default="",
    )
    result = _safe_result(
        status,
        execution_ok=True,
        network_requested=True,
        requested_codes=expected_codes,
        covered_codes=[row["code"] for row in rows],
        snapshot_available=ready,
        collected_at=collected_at,
        source={
            "origin_source": TENCENT_ORIGIN_SOURCE,
            "adapter": TENCENT_ADAPTER,
            "source_version": TENCENT_SOURCE_VERSION,
        },
        provider_keys={
            "quote": TENCENT_QUOTE_PROVIDER_KEY,
            "valuation": TENCENT_VALUATION_PROVIDER_KEY,
        },
        adapter_statuses={
            capability: result.get("status", "")
            for capability, result in adapter_results.items()
        },
        network_requests_made=_request_count(active_transport),
        rows=rows,
    )
    result["snapshot_hash"] = stable_hash(
        {
            "contract_version": TENCENT_SHADOW_SNAPSHOT_CONTRACT_VERSION,
            "requested_codes": result["requested_codes"],
            "collected_at": collected_at,
            "source": result["source"],
            "rows": rows,
        }
    ) if ready else ""
    return result


def _merge_records(
    records_by_capability: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    quote_by_code = _payloads_by_code(records_by_capability.get("quote", []))
    valuation_by_code = _payloads_by_code(
        records_by_capability.get("valuation", [])
    )
    if set(quote_by_code) != set(valuation_by_code):
        return []
    rows = []
    for code in sorted(quote_by_code):
        quote = quote_by_code[code]
        valuation = valuation_by_code[code]
        if quote.get("name") != valuation.get("name"):
            return []
        rows.append(
            {
                "code": code,
                "name": quote.get("name"),
                "price": quote.get("price"),
                "pe_ttm": valuation.get("pe_ttm"),
                "pb": valuation.get("pb"),
                "market_cap_yi": valuation.get("market_cap"),
                "float_market_cap_yi": valuation.get("float_market_cap"),
                "event_time": quote.get("_event_time"),
            }
        )
    return rows


def _payloads_by_code(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            return {}
        code = str(payload.get("code") or "")
        if not code or code in result:
            return {}
        item = dict(payload)
        item["_event_time"] = record.get("event_time")
        result[code] = item
    return result


def _request_count(transport: object) -> int | None:
    value = getattr(transport, "request_count", None)
    return value if type(value) is int and value >= 0 else None


def _safe_result(
    status: str,
    *,
    execution_ok: bool,
    network_requested: bool,
    requested_codes: list[str],
    network_requests_made: int | None,
    **payload: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "execution_ok": bool(execution_ok),
        "snapshot_contract_version": TENCENT_SHADOW_SNAPSHOT_CONTRACT_VERSION,
        "network_requested": network_requested,
        "requested_codes": requested_codes,
        "network_requests_made": network_requests_made,
        "snapshot_available": False,
        "read_only": True,
        "strategy_integration": False,
        "readiness_integration": False,
        "decision_hash_integration": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "automatic_configuration_change": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
        "rows": [],
        **payload,
    }


__all__ = [
    "TENCENT_SHADOW_SNAPSHOT_CONTRACT_VERSION",
    "TENCENT_SHADOW_SNAPSHOT_INCOMPLETE",
    "TENCENT_SHADOW_SNAPSHOT_NETWORK_NOT_REQUESTED",
    "TENCENT_SHADOW_SNAPSHOT_READY",
    "TENCENT_SHADOW_SNAPSHOT_REQUEST_INVALID",
    "build_tencent_shadow_snapshot",
]
