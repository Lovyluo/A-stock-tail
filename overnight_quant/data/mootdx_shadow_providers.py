from __future__ import annotations

from datetime import datetime, time
import math
from typing import Any, Callable, Iterable, Mapping

from overnight_quant.data.close_time_contract import CloseTimeContract
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_probe_sources import (
    MOOTDX_MINUTE_SOURCE_VERSION,
    MOOTDX_TRANSACTION_SOURCE_VERSION,
    MootdxMinuteProbeCollectors,
)
from overnight_quant.data.point_in_time import (
    parse_cn_datetime,
    records_available_at,
    stable_hash,
)
from overnight_quant.data.probe_worker_process import (
    WORKER_TIMEOUT_ERROR,
    run_probe_worker_process,
)
from overnight_quant.data.real_point_in_time_collectors import SourceContractError
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_BOUND,
    SourceProviderEnvelope,
    execute_source_adapter,
)


MOOTDX_QUALIFIED_ENDPOINT = {
    "id": "mootdx_locked@59.36.5.11:7709",
    "name": "mootdx_qualified_fixed",
    "host": "59.36.5.11",
    "port": 7709,
}
MOOTDX_QUALIFIED_CODES = (
    "000001",
    "000333",
    "600000",
    "600519",
    "601318",
)
MOOTDX_ORIGIN_SOURCE = "tongdaxin"
MOOTDX_ADAPTER = "mootdx"
MOOTDX_MINUTE_PROVIDER_KEY = (
    "mootdx_shadow_providers.MootdxQualifiedShadowProviders."
    "collect_minute_records"
)
MOOTDX_TRANSACTION_PROVIDER_KEY = (
    "mootdx_shadow_providers.MootdxQualifiedShadowProviders."
    "collect_transaction_records"
)
MOOTDX_QUALIFICATION_RECORD_SHA256 = (
    "0c772e6a900c87281a0e7c6336f5a66a49d32b7d793f1582b3caf0fd1e694f69"
)
MOOTDX_SHADOW_CONTRACT_VERSION = "mootdx_qualified_shadow_records_v2"
MOOTDX_SHADOW_RECORDS_READY = "MOOTDX_SHADOW_RECORDS_READY"
MOOTDX_SHADOW_DATA_UNAVAILABLE = "MOOTDX_SHADOW_DATA_UNAVAILABLE"
MOOTDX_SHADOW_NETWORK_NOT_REQUESTED = "MOOTDX_SHADOW_NETWORK_NOT_REQUESTED"
MOOTDX_SHADOW_REQUEST_INVALID = "MOOTDX_SHADOW_REQUEST_INVALID"
MOOTDX_SHADOW_BATCH_DEADLINE_MS = 2000


class MootdxQualifiedShadowProviders:
    """Explicit fixed-endpoint providers for qualified shadow records only."""

    def __init__(
        self,
        codes: Iterable[str],
        *,
        observed_at: datetime,
        clock: Callable[[], datetime] | None = None,
        client_factory: Callable[[], Any] | None = None,
        worker_runner: (
            Callable[[dict[str, Any], int], dict[str, Any]] | None
        ) = None,
        request_timeout_seconds: float = 2.0,
    ) -> None:
        normalized_codes = tuple(sorted(str(code).strip() for code in codes))
        if normalized_codes != tuple(sorted(MOOTDX_QUALIFIED_CODES)):
            raise SourceContractError("mootdx_qualified_stock_scope_mismatch")
        if float(request_timeout_seconds) != 2.0:
            raise SourceContractError("mootdx_qualified_deadline_mismatch")
        observed = parse_cn_datetime(observed_at)
        if observed is None:
            raise SourceContractError("mootdx_shadow_observed_at_invalid")
        if MOOTDX_MINUTE_SOURCE_VERSION != (
            "mootdx_0.11.7_tdx_std_bars_1m_v2026-07-31"
        ):
            raise SourceContractError("mootdx_minute_source_version_mismatch")
        if MOOTDX_TRANSACTION_SOURCE_VERSION != (
            "mootdx_0.11.7_tdx_std_transaction_v2026-08-06"
        ):
            raise SourceContractError("mootdx_transaction_source_version_mismatch")

        self.codes = normalized_codes
        self.observed_at = observed.astimezone(CN_TZ)
        self.clock = clock or (lambda: datetime.now(CN_TZ))
        self.time_contract = _qualified_time_contract(self.observed_at)
        self.worker_runner = worker_runner or run_probe_worker_process
        self.process_isolated = (
            client_factory is None or worker_runner is not None
        )
        self.batch_audit: dict[str, Any] = {}
        self.collector = MootdxMinuteProbeCollectors(
            self.codes,
            clock=self.clock,
            client_factory=client_factory,
            time_contract=self.time_contract,
            endpoint=dict(MOOTDX_QUALIFIED_ENDPOINT),
            request_timeout_seconds=2.0,
            minute_offset=800,
        )
        if self.collector.endpoint_id != MOOTDX_QUALIFIED_ENDPOINT["id"]:
            raise SourceContractError("mootdx_qualified_endpoint_mismatch")

    def collect_minute_records(self) -> list[dict[str, Any]]:
        if self.process_isolated:
            payload = self._run_worker_batch("shadow_minute_batch")
            source_version = str(payload.get("source_version") or "")
            source_records = list(payload.get("records") or [])
            if payload.get("endpoint_id") != MOOTDX_QUALIFIED_ENDPOINT["id"]:
                raise SourceContractError("mootdx_minute_endpoint_mismatch")
        else:
            batch = self.collector.collect_minute_bars(self.observed_at)
            source_version = batch.source_version
            source_records = list(batch.records or [])
        if source_version != MOOTDX_MINUTE_SOURCE_VERSION:
            raise SourceContractError("mootdx_minute_source_version_mismatch")
        records = self._validate_and_finalize_minute_records(source_records)
        return sorted(
            records,
            key=lambda row: (
                str((row.get("payload") or {}).get("code") or ""),
                str(row.get("event_time") or ""),
                str(row.get("raw_hash") or ""),
            ),
        )

    def collect_transaction_records(self) -> list[dict[str, Any]]:
        if self.process_isolated:
            payload = self._run_worker_batch("shadow_transaction_batch")
            evidence = dict(payload.get("transaction_evidence") or {})
        else:
            evidence = self.collector.collect_transaction_evidence(
                self.observed_at
            )
        self._validate_transaction_time_contract(evidence)
        if evidence.get("endpoint_id") != MOOTDX_QUALIFIED_ENDPOINT["id"]:
            raise SourceContractError("mootdx_transaction_endpoint_mismatch")
        if evidence.get("source_version") != MOOTDX_TRANSACTION_SOURCE_VERSION:
            raise SourceContractError("mootdx_transaction_source_version_mismatch")
        if sorted(evidence.get("requested_codes") or []) != list(self.codes):
            raise SourceContractError("mootdx_transaction_scope_mismatch")

        records: list[dict[str, Any]] = []
        for code in self.codes:
            item = (evidence.get("by_code") or {}).get(code)
            if not isinstance(item, Mapping):
                raise SourceContractError(
                    f"mootdx_transaction_code_missing:{code}"
                )
            if item.get("error") or item.get("coverage_complete") is not True:
                raise SourceContractError(
                    f"mootdx_transaction_coverage_incomplete:{code}"
                )
            raw_hash = stable_hash(item.get("raw_response_hashes") or [])
            for row in item.get("records") or []:
                records.append(
                    self._transaction_record(
                        code,
                        row,
                        observed_at=str(item.get("observed_at") or ""),
                        available_at=str(item.get("available_at") or ""),
                        raw_hash=raw_hash,
                    )
                )
        if not records:
            raise SourceContractError("mootdx_transaction_records_empty")
        return sorted(
            records,
            key=lambda row: (
                str((row.get("payload") or {}).get("code") or ""),
                str(row.get("event_time") or ""),
                int((row.get("payload") or {}).get("source_position") or 0),
            ),
        )

    def close(self) -> None:
        self.collector.close()

    def _run_worker_batch(self, operation: str) -> dict[str, Any]:
        result = self.worker_runner(
            {
                "operation": operation,
                "source": "mootdx",
                "codes": list(self.codes),
                "observed_at": self.observed_at.isoformat(),
                "endpoint": dict(MOOTDX_QUALIFIED_ENDPOINT),
                "provider_timeout_seconds": 2.0,
            },
            MOOTDX_SHADOW_BATCH_DEADLINE_MS,
        )
        self.batch_audit[operation] = {
            "request_deadline_ms": MOOTDX_SHADOW_BATCH_DEADLINE_MS,
            "request_timed_out": bool(result.get("request_timed_out")),
            "worker_terminated": bool(result.get("worker_terminated")),
            "elapsed_ms": result.get("elapsed_ms"),
            "error_code": str(result.get("error_code") or ""),
        }
        if result.get("ok") is not True:
            error_code = str(result.get("error_code") or "WORKER_PROCESS_FAILED")
            if error_code == WORKER_TIMEOUT_ERROR:
                raise SourceContractError(
                    f"mootdx_{operation}_deadline_exceeded"
                )
            raise SourceContractError(
                f"mootdx_{operation}_failed:{error_code}"
            )
        payload = dict(result.get("payload") or {})
        worker_started = parse_cn_datetime(
            payload.get("worker_request_started_at")
        )
        worker_completed = parse_cn_datetime(
            payload.get("worker_request_completed_at")
        )
        collection_deadline = parse_cn_datetime(
            self.time_contract.collection_deadline
        )
        if (
            worker_started is None
            or worker_completed is None
            or worker_started > worker_completed
            or worker_completed > collection_deadline
        ):
            raise SourceContractError(
                f"mootdx_{operation}_collection_time_invalid"
            )
        return payload

    def _validate_and_finalize_minute_records(
        self,
        source_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        cutoff = parse_cn_datetime(self.time_contract.feature_event_cutoff)
        deadline = parse_cn_datetime(self.time_contract.collection_deadline)
        trade_date = self.observed_at.date()
        by_code: dict[str, list[dict[str, Any]]] = {
            code: [] for code in self.codes
        }
        for row in source_records:
            payload = row.get("payload") or {}
            code = str(payload.get("code") or "")
            event = parse_cn_datetime(row.get("event_time"))
            observed = parse_cn_datetime(row.get("observed_at"))
            available = parse_cn_datetime(row.get("available_at"))
            if code not in by_code or event is None:
                raise SourceContractError("mootdx_minute_identity_invalid")
            if event.date() != trade_date:
                raise SourceContractError(
                    f"mootdx_minute_trade_date_mismatch:{code}"
                )
            if observed is None or available is None or observed > available:
                raise SourceContractError(
                    f"mootdx_minute_time_contract_invalid:{code}"
                )
            if available > deadline:
                raise SourceContractError(
                    f"mootdx_minute_collection_deadline_exceeded:{code}"
                )
            if event <= cutoff:
                self._validate_ohlcv(code, payload)
                by_code[code].append(dict(row))

        finalized: list[dict[str, Any]] = []
        for code in self.codes:
            rows = by_code[code]
            unique_minutes = {
                parse_cn_datetime(row.get("event_time")).replace(
                    second=0, microsecond=0
                )
                for row in rows
            }
            if len(unique_minutes) != len(rows):
                raise SourceContractError(
                    f"mootdx_minute_duplicate_event_minute:{code}"
                )
            required_minute = datetime.combine(
                trade_date, time(14, 50), tzinfo=CN_TZ
            )
            if required_minute not in unique_minutes:
                raise SourceContractError(
                    f"mootdx_minute_1450_missing:{code}"
                )
            if len(unique_minutes) < 12:
                raise SourceContractError(
                    f"mootdx_minute_unique_minutes_insufficient:{code}"
                )
            finalized.extend(self._minute_record(row) for row in rows)
        return finalized

    @staticmethod
    def _validate_ohlcv(code: str, payload: Mapping[str, Any]) -> None:
        values: dict[str, float] = {}
        for field in ("open", "high", "low", "close", "volume"):
            value = payload.get(field)
            if isinstance(value, bool):
                raise SourceContractError(
                    f"mootdx_minute_ohlcv_invalid:{code}:{field}"
                )
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                raise SourceContractError(
                    f"mootdx_minute_ohlcv_invalid:{code}:{field}"
                ) from None
            if not math.isfinite(numeric):
                raise SourceContractError(
                    f"mootdx_minute_ohlcv_invalid:{code}:{field}"
                )
            values[field] = numeric
        if any(values[field] <= 0 for field in ("open", "high", "low", "close")):
            raise SourceContractError(
                f"mootdx_minute_ohlcv_invalid:{code}:price"
            )
        if values["volume"] < 0:
            raise SourceContractError(
                f"mootdx_minute_ohlcv_invalid:{code}:volume"
            )
        if (
            values["high"] < max(values["open"], values["close"])
            or values["low"] > min(values["open"], values["close"])
            or values["high"] < values["low"]
        ):
            raise SourceContractError(
                f"mootdx_minute_ohlcv_invalid:{code}:range"
            )

    def _validate_transaction_time_contract(
        self,
        evidence: Mapping[str, Any],
    ) -> None:
        started = parse_cn_datetime(evidence.get("request_started_at"))
        completed = parse_cn_datetime(evidence.get("request_completed_at"))
        deadline = parse_cn_datetime(self.time_contract.collection_deadline)
        if started is None or completed is None or started > completed:
            raise SourceContractError(
                "mootdx_transaction_request_time_invalid"
            )
        if completed > deadline:
            raise SourceContractError(
                "mootdx_transaction_collection_deadline_exceeded"
            )
        for code in self.codes:
            item = (evidence.get("by_code") or {}).get(code)
            if not isinstance(item, Mapping):
                continue
            observed = parse_cn_datetime(item.get("observed_at"))
            available = parse_cn_datetime(item.get("available_at"))
            if (
                observed is None
                or available is None
                or observed > available
                or available > completed
            ):
                raise SourceContractError(
                    f"mootdx_transaction_time_contract_invalid:{code}"
                )

    def _minute_record(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row.get("payload") or {})
        payload.update(
            {
                "endpoint_id": MOOTDX_QUALIFIED_ENDPOINT["id"],
                "endpoint": dict(MOOTDX_QUALIFIED_ENDPOINT),
                "is_final": True,
                "minute_label_semantics": "minute_end",
                "qualification_record_sha256": (
                    MOOTDX_QUALIFICATION_RECORD_SHA256
                ),
                "shadow_only": True,
            }
        )
        result = dict(row)
        result.update(
            {
                "capability": "minute_bar",
                "origin_source": MOOTDX_ORIGIN_SOURCE,
                "adapter": MOOTDX_ADAPTER,
                "source_version": MOOTDX_MINUTE_SOURCE_VERSION,
                "source": "mootdx_tdx_std_minute",
                "is_final": True,
                "payload": payload,
                **_time_contract_fields(self.time_contract),
            }
        )
        return result

    def _transaction_record(
        self,
        code: str,
        row: Mapping[str, Any],
        *,
        observed_at: str,
        available_at: str,
        raw_hash: str,
    ) -> dict[str, Any]:
        payload = dict(row)
        payload.update(
            {
                "code": code,
                "endpoint_id": MOOTDX_QUALIFIED_ENDPOINT["id"],
                "endpoint": dict(MOOTDX_QUALIFIED_ENDPOINT),
                "qualification_record_sha256": (
                    MOOTDX_QUALIFICATION_RECORD_SHA256
                ),
                "shadow_only": True,
                "attribution_audit_only": True,
                "feature_scoring_eligible": False,
            }
        )
        request = {
            "capability": "transaction",
            "codes": list(self.codes),
            "endpoint": dict(MOOTDX_QUALIFIED_ENDPOINT),
            "page_size": 800,
            "max_pages": 20,
        }
        return {
            "capability": "transaction",
            "origin_source": MOOTDX_ORIGIN_SOURCE,
            "adapter": MOOTDX_ADAPTER,
            "source": "mootdx_tdx_std_transaction",
            "source_version": MOOTDX_TRANSACTION_SOURCE_VERSION,
            "event_time": str(row.get("event_time") or ""),
            "published_at": "",
            "observed_at": observed_at,
            "available_at": available_at,
            "request_hash": stable_hash(request),
            "raw_hash": raw_hash,
            "data_type": "transaction",
            "audit_only": True,
            "feature_scoring_eligible": False,
            "payload": payload,
            **_time_contract_fields(self.time_contract),
        }


def build_mootdx_shadow_records(
    codes: Iterable[str],
    *,
    network: bool,
    observed_at: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    client_factory: Callable[[], Any] | None = None,
    worker_runner: (
        Callable[[dict[str, Any], int], dict[str, Any]] | None
    ) = None,
) -> dict[str, Any]:
    requested_codes = sorted(str(code).strip() for code in codes)
    if type(network) is not bool:
        return _safe_result(
            MOOTDX_SHADOW_REQUEST_INVALID,
            execution_ok=False,
            requested_codes=requested_codes,
            reason="network_flag_must_be_bool",
        )
    if not network:
        return _safe_result(
            MOOTDX_SHADOW_NETWORK_NOT_REQUESTED,
            execution_ok=True,
            requested_codes=requested_codes,
            reason="explicit_network_flag_required",
        )

    current = parse_cn_datetime(observed_at or datetime.now(CN_TZ))
    if current is None:
        return _safe_result(
            MOOTDX_SHADOW_REQUEST_INVALID,
            execution_ok=False,
            requested_codes=requested_codes,
            reason="observed_at_invalid",
        )
    provider: MootdxQualifiedShadowProviders | None = None
    try:
        provider = MootdxQualifiedShadowProviders(
            requested_codes,
            observed_at=current,
            clock=clock,
            client_factory=client_factory,
            worker_runner=worker_runner,
        )
        minute_records: list[dict[str, Any]] = []
        transaction_records: list[dict[str, Any]] = []
        minute_result = execute_source_adapter(
            "minute_bar",
            origin_source=MOOTDX_ORIGIN_SOURCE,
            adapter=MOOTDX_ADAPTER,
            source_version=MOOTDX_MINUTE_SOURCE_VERSION,
            provider_envelope=SourceProviderEnvelope(
                provider_key=MOOTDX_MINUTE_PROVIDER_KEY,
                provider_callable=lambda: _capture(
                    minute_records,
                    provider.collect_minute_records(),
                ),
            ),
            environ={},
        )
        if minute_result.get("status") != SOURCE_ADAPTER_BOUND:
            return _safe_result(
                MOOTDX_SHADOW_DATA_UNAVAILABLE,
                execution_ok=bool(minute_result.get("execution_ok")),
                requested_codes=requested_codes,
                adapter_statuses={"minute_bar": minute_result.get("status")},
                reason="minute_provider_not_ready",
                batch_audit=dict(provider.batch_audit),
            )
        transaction_result = execute_source_adapter(
            "transaction",
            origin_source=MOOTDX_ORIGIN_SOURCE,
            adapter=MOOTDX_ADAPTER,
            source_version=MOOTDX_TRANSACTION_SOURCE_VERSION,
            provider_envelope=SourceProviderEnvelope(
                provider_key=MOOTDX_TRANSACTION_PROVIDER_KEY,
                provider_callable=lambda: _capture(
                    transaction_records,
                    provider.collect_transaction_records(),
                ),
            ),
            environ={},
        )
        if transaction_result.get("status") != SOURCE_ADAPTER_BOUND:
            return _safe_result(
                MOOTDX_SHADOW_DATA_UNAVAILABLE,
                execution_ok=bool(transaction_result.get("execution_ok")),
                requested_codes=requested_codes,
                adapter_statuses={
                    "minute_bar": minute_result.get("status"),
                    "transaction": transaction_result.get("status"),
                },
                reason="transaction_provider_not_ready",
                batch_audit=dict(provider.batch_audit),
            )
        accepted, rejected = records_available_at(
            minute_records,
            provider.time_contract.decision_time,
            time_contract=provider.time_contract,
        )
        if rejected or not accepted:
            return _safe_result(
                MOOTDX_SHADOW_DATA_UNAVAILABLE,
                execution_ok=True,
                requested_codes=requested_codes,
                adapter_statuses={
                    "minute_bar": minute_result.get("status"),
                    "transaction": transaction_result.get("status"),
                },
                reason="point_in_time_minute_records_rejected",
                rejected_record_count=len(rejected),
            )
        decision_observed = parse_cn_datetime(provider.clock())
        decision_time = parse_cn_datetime(provider.time_contract.decision_time)
        if decision_observed is None or decision_observed < decision_time:
            return _safe_result(
                MOOTDX_SHADOW_DATA_UNAVAILABLE,
                execution_ok=True,
                requested_codes=requested_codes,
                adapter_statuses={
                    "minute_bar": minute_result.get("status"),
                    "transaction": transaction_result.get("status"),
                },
                reason="decision_time_not_reached",
                batch_audit=dict(provider.batch_audit),
            )
        records = minute_records
        return _safe_result(
            MOOTDX_SHADOW_RECORDS_READY,
            execution_ok=True,
            requested_codes=requested_codes,
            adapter_statuses={
                "minute_bar": minute_result.get("status"),
                "transaction": transaction_result.get("status"),
            },
            covered_codes=sorted(
                {
                    str((row.get("payload") or {}).get("code") or "")
                    for row in records
                }
            ),
            source={
                "origin_source": MOOTDX_ORIGIN_SOURCE,
                "adapter": MOOTDX_ADAPTER,
                "endpoint": dict(MOOTDX_QUALIFIED_ENDPOINT),
                "qualification_record_sha256": (
                    MOOTDX_QUALIFICATION_RECORD_SHA256
                ),
            },
            minute_record_count=len(minute_records),
            transaction_record_count=len(transaction_records),
            records=records,
            records_hash=stable_hash(records),
            audit_records=transaction_records,
            audit_records_hash=stable_hash(transaction_records),
            batch_audit=dict(provider.batch_audit),
        )
    except (SourceContractError, TypeError, ValueError) as exc:
        return _safe_result(
            MOOTDX_SHADOW_DATA_UNAVAILABLE,
            execution_ok=False,
            requested_codes=requested_codes,
            reason=str(exc) or type(exc).__name__,
        )
    finally:
        if provider is not None:
            provider.close()


def _capture(target: list[dict[str, Any]], rows: list[dict[str, Any]]):
    target.extend(rows)
    return rows


def _qualified_time_contract(observed_at: datetime) -> CloseTimeContract:
    day = observed_at.date()
    at = lambda value: datetime.combine(day, value, tzinfo=CN_TZ).isoformat(
        timespec="seconds"
    )
    return CloseTimeContract(
        feature_event_cutoff=at(time(14, 50)),
        collection_deadline=at(time(14, 51, 5)),
        decision_time=at(time(14, 51, 10)),
        execution_not_before=at(time(14, 52)),
        minute_label_semantics="minute_end",
        minute_label_validation_status="VERIFIED",
        probe_evidence_hash=MOOTDX_QUALIFICATION_RECORD_SHA256,
        is_final=True,
    )


def _time_contract_fields(contract: CloseTimeContract) -> dict[str, Any]:
    return {
        "decision_cutoff": contract.decision_time,
        "feature_event_cutoff": contract.feature_event_cutoff,
        "collection_deadline": contract.collection_deadline,
        "decision_time": contract.decision_time,
        "execution_not_before": contract.execution_not_before,
        "time_contract_version": contract.contract_version,
        "minute_label_semantics": contract.minute_label_semantics,
        "minute_label_validation_status": (
            contract.minute_label_validation_status
        ),
        "probe_evidence_hash": contract.probe_evidence_hash,
    }


def _safe_result(
    status: str,
    *,
    execution_ok: bool,
    requested_codes: list[str],
    **payload: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "execution_ok": bool(execution_ok),
        "shadow_contract_version": MOOTDX_SHADOW_CONTRACT_VERSION,
        "requested_codes": requested_codes,
        "fixed_endpoint": dict(MOOTDX_QUALIFIED_ENDPOINT),
        "qualification_scope": ["minute_bar", "transaction"],
        "automatic_configuration_change": False,
        "read_only": True,
        "strategy_integration": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
        "records": [],
        "audit_records": [],
        **payload,
    }


__all__ = [
    "MOOTDX_ADAPTER",
    "MOOTDX_MINUTE_PROVIDER_KEY",
    "MOOTDX_ORIGIN_SOURCE",
    "MOOTDX_QUALIFICATION_RECORD_SHA256",
    "MOOTDX_QUALIFIED_CODES",
    "MOOTDX_QUALIFIED_ENDPOINT",
    "MOOTDX_SHADOW_DATA_UNAVAILABLE",
    "MOOTDX_SHADOW_BATCH_DEADLINE_MS",
    "MOOTDX_SHADOW_NETWORK_NOT_REQUESTED",
    "MOOTDX_SHADOW_RECORDS_READY",
    "MOOTDX_TRANSACTION_PROVIDER_KEY",
    "MootdxQualifiedShadowProviders",
    "build_mootdx_shadow_records",
]
