from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from concurrent.futures import Future, ThreadPoolExecutor, wait
import json
from pathlib import Path
import time as time_module
from typing import Any, Callable

from overnight_quant.data.close_time_contract import (
    CloseTimeContract,
    build_close_time_contract,
    contract_datetimes,
    normalize_close_time_contract,
)
from overnight_quant.data.close_confirmation_readiness import (
    SNAPSHOT_CONTRACT_VERSION,
    filter_source_status_at,
    normalize_point_in_time_records,
    validate_close_confirmation_readiness,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import (
    PointInTimeError,
    parse_cn_datetime,
    stable_hash,
)


COLLECTION_START = time(14, 40)
FREEZE_TIME = time(14, 50)


class ImmutableSnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderBatch:
    records: list[dict[str, Any]] = field(default_factory=list)
    data_types: list[str] = field(default_factory=list)
    source_version: str = ""
    raw_hash: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    callback: Callable[
        [datetime],
        list[dict[str, Any]] | ProviderBatch,
    ]
    data_types: list[str] = field(default_factory=list)
    source_version: str = ""
    deadline_setter: Callable[[float | None], None] | None = None
    metrics_getter: Callable[[], dict[str, Any]] | None = None
    cancel_setter: Callable[[], None] | None = None
    priority: int = 3
    stage: str = "news"

    def __post_init__(self) -> None:
        owner = getattr(self.callback, "__self__", None)
        transport = getattr(owner, "transport", None)
        if self.deadline_setter is None and callable(
            getattr(transport, "set_global_deadline", None)
        ):
            object.__setattr__(
                self,
                "deadline_setter",
                transport.set_global_deadline,
            )
        if self.metrics_getter is None and callable(
            getattr(transport, "metrics_snapshot", None)
        ):
            object.__setattr__(
                self,
                "metrics_getter",
                transport.metrics_snapshot,
            )
        if self.cancel_setter is None and callable(
            getattr(transport, "cancel_current_collection", None)
        ):
            object.__setattr__(
                self,
                "cancel_setter",
                transport.cancel_current_collection,
            )

    def __call__(
        self,
        observed_at: datetime,
    ) -> list[dict[str, Any]] | ProviderBatch:
        return self.callback(observed_at)


class ImmutableSnapshotStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write_once(self, namespace: str, snapshot_id: str, payload: dict[str, Any]) -> Path:
        target = self.root / namespace / f"{snapshot_id}.json"
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n"
        content_hash = stable_hash(payload)
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if stable_hash(existing) != content_hash:
                raise ImmutableSnapshotError(f"immutable_snapshot_conflict:{target}")
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(target)
        return target

    def read(self, namespace: str, snapshot_id: str) -> dict[str, Any]:
        path = self.root / namespace / f"{snapshot_id}.json"
        if not path.is_file():
            raise ImmutableSnapshotError(f"snapshot_missing:{path}")
        return json.loads(path.read_text(encoding="utf-8"))


class CloseWindowCollector:
    def __init__(
        self,
        store: ImmutableSnapshotStore,
        providers: dict[
            str,
            Callable[
                [datetime],
                list[dict[str, Any]] | ProviderBatch,
            ],
        ],
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time_module.monotonic,
        time_contract: dict[str, Any] | CloseTimeContract | None = None,
        max_workers: int = 4,
    ):
        self.store = store
        self.providers = dict(providers)
        self.clock = clock or (lambda: datetime.now(CN_TZ))
        self.monotonic = monotonic
        self.time_contract = normalize_close_time_contract(time_contract)
        self.max_workers = max(1, int(max_workers))

    def collect(self, observed_at: datetime) -> dict[str, Any]:
        current = _coerce_cn(observed_at)
        if self.time_contract is not None:
            return self._collect_with_time_contract(current)
        if not (COLLECTION_START <= current.time() <= FREEZE_TIME):
            return {
                "status": "NOT_COLLECTION_WINDOW",
                "execution_ok": True,
                "data_ready": False,
                "observed_at": current.isoformat(timespec="seconds"),
                "records": [],
            }
        if not self.providers:
            readiness = validate_close_confirmation_readiness(
                {"records": [], "decision_time": current.isoformat(timespec="seconds")},
            )
            return {
                "status": "NO_DATA_SOURCE",
                "execution_ok": True,
                "data_ready": False,
                "observed_at": current.isoformat(timespec="seconds"),
                "records": [],
                "errors": [],
                **_readiness_fields(readiness),
            }
        records: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        source_status: list[dict[str, Any]] = []
        completion_times: list[datetime] = [current]
        for source, provider in self.providers.items():
            started_at = _coerce_cn(self.clock())
            expected_data_types = sorted(
                {
                    str(item).strip().lower()
                    for item in getattr(provider, "data_types", [])
                    if str(item).strip()
                }
            )
            expected_source_version = str(
                getattr(provider, "source_version", "")
                or "close_window_collector_v1"
            )
            try:
                provider_result = provider(started_at)
                if isinstance(provider_result, ProviderBatch):
                    provider_rows = [
                        dict(item) for item in provider_result.records
                    ]
                    declared_data_types = sorted(
                        {
                            str(item).strip().lower()
                            for item in provider_result.data_types
                            if str(item).strip()
                        }
                    )
                    source_version = (
                        provider_result.source_version
                        or "close_window_collector_v1"
                    )
                    source_raw_hash = provider_result.raw_hash
                else:
                    provider_rows = [
                        dict(item) for item in provider_result
                    ]
                    declared_data_types = []
                    source_version = expected_source_version
                    source_raw_hash = ""
                completed_at = _coerce_cn(self.clock())
                completion_times.append(completed_at)
                provider_rows = [
                    _stamp_provider_completion(
                        row,
                        started_at=started_at,
                        completed_at=completed_at,
                    )
                    for row in provider_rows
                ]
                records.extend(provider_rows)
                source_status.append(
                    _source_status_record(
                        source=source,
                        status="SUCCESS",
                        ok=True,
                        record_count=len(provider_rows),
                        data_types=sorted(
                            set(expected_data_types)
                            | set(declared_data_types)
                            | {
                                str(row.get("data_type") or "").strip().lower()
                                for row in provider_rows
                                if row.get("data_type")
                            }
                        ),
                        started_at=started_at,
                        completed_at=completed_at,
                        source_version=source_version,
                        raw_hash=source_raw_hash,
                    )
                )
            except Exception as exc:
                completed_at = _coerce_cn(self.clock())
                completion_times.append(completed_at)
                errors.append({"source": source, "error": f"{type(exc).__name__}: {exc}"})
                source_status.append(
                    _source_status_record(
                        source=source,
                        status="FAILED",
                        ok=False,
                        record_count=0,
                        data_types=expected_data_types,
                        started_at=started_at,
                        completed_at=completed_at,
                        source_version=expected_source_version,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        freeze_cutoff = datetime.combine(
            current.date(),
            FREEZE_TIME,
            tzinfo=CN_TZ,
        )
        collection_completed_at = max(completion_times)
        effective_decision_time = min(
            collection_completed_at,
            freeze_cutoff,
        )
        readiness = validate_close_confirmation_readiness(
            {
                "records": records,
                "source_status": source_status,
                "decision_time": effective_decision_time.isoformat(
                    timespec="seconds"
                ),
            },
        )
        normalized = readiness["normalized_snapshot"]
        accepted = list(normalized.get("records") or [])
        rejected = list(normalized.get("rejected_records") or [])
        if effective_decision_time.time() < FREEZE_TIME:
            readiness["data_ready"] = False
            readiness["readiness_errors"] = list(
                dict.fromkeys(
                    [*(readiness.get("readiness_errors") or []), "snapshot_not_frozen_1450"]
                )
            )
        if not accepted:
            return {
                "status": "NO_VALID_RECORDS",
                "execution_ok": True,
                "data_ready": False,
                "observed_at": current.isoformat(timespec="seconds"),
                "completed_at": collection_completed_at.isoformat(
                    timespec="seconds"
                ),
                "records": [],
                "rejected_records": rejected,
                "errors": errors,
                "source_status": list(
                    normalized.get("source_status") or []
                ),
                "rejected_source_status": list(
                    normalized.get("rejected_source_status") or []
                ),
                **_readiness_fields(readiness),
            }
        snapshot = {
            "status": "COLLECTED",
            "execution_ok": True,
            "data_ready": readiness["data_ready"],
            "observed_at": current.isoformat(timespec="seconds"),
            "completed_at": collection_completed_at.isoformat(
                timespec="seconds"
            ),
            "decision_time": effective_decision_time.isoformat(
                timespec="seconds"
            ),
            "records": accepted,
            "rejected_records": rejected,
            "errors": errors,
            "source_status": list(
                readiness["normalized_snapshot"].get("source_status") or []
            ),
            "rejected_source_status": list(
                normalized.get("rejected_source_status") or []
            ),
            "ingest_hash": stable_hash(records),
            **_readiness_fields(readiness),
        }
        snapshot_id = current.strftime("%Y%m%d_%H%M%S")
        snapshot["path"] = str(self.store.write_once("collection", snapshot_id, snapshot))
        return snapshot

    def _collect_with_time_contract(
        self,
        current: datetime,
    ) -> dict[str, Any]:
        contract = self.time_contract
        if contract is None:
            raise RuntimeError("time_contract_missing")
        timeline = contract_datetimes(contract)
        feature_cutoff = timeline["feature_event_cutoff"]
        collection_deadline = timeline["collection_deadline"]
        decision_time = timeline["decision_time"]
        if current.date() != feature_cutoff.date():
            return {
                "status": "TIME_CONTRACT_DATE_MISMATCH",
                "execution_ok": False,
                "data_ready": False,
                "observed_at": current.isoformat(timespec="seconds"),
                "time_contract": contract.as_dict(),
                "records": [],
            }
        collection_start = datetime.combine(
            current.date(),
            COLLECTION_START,
            tzinfo=CN_TZ,
        )
        if not (collection_start <= current <= collection_deadline):
            return {
                "status": "NOT_COLLECTION_WINDOW",
                "execution_ok": True,
                "data_ready": False,
                "observed_at": current.isoformat(timespec="seconds"),
                "time_contract": contract.as_dict(),
                "records": [],
            }
        if not self.providers:
            readiness = validate_close_confirmation_readiness(
                {
                    "records": [],
                    "decision_time": decision_time.isoformat(
                        timespec="seconds"
                    ),
                    "time_contract": contract.as_dict(),
                },
            )
            return {
                "status": "NO_DATA_SOURCE",
                "execution_ok": True,
                "data_ready": False,
                "observed_at": current.isoformat(timespec="seconds"),
                "time_contract": contract.as_dict(),
                "records": [],
                "errors": [],
                **_readiness_fields(readiness),
            }

        deadline_monotonic = self.monotonic() + max(
            0.0,
            (collection_deadline - current).total_seconds(),
        )
        self._set_provider_deadlines(deadline_monotonic)
        executor = ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(self.providers)),
            thread_name_prefix="close-pit",
        )
        futures: dict[Future, str] = {}
        skipped_sources: list[str] = []
        for source in _provider_submission_order(self.providers):
            if self.monotonic() >= deadline_monotonic:
                skipped_sources.append(source)
                continue
            futures[
                executor.submit(
                    self._invoke_provider,
                    source,
                    self.providers[source],
                )
            ] = source
        remaining_seconds = max(
            0.0,
            deadline_monotonic - self.monotonic(),
        )
        completed_futures, pending_futures = wait(
            futures,
            timeout=remaining_seconds,
        )
        provider_results = {
            futures[future]: future.result()
            for future in completed_futures
        }
        running_at_deadline = {
            future for future in pending_futures if future.running()
        }
        cancelled_before_start = set()
        for future in pending_futures:
            if future.cancel():
                cancelled_before_start.add(future)
            else:
                running_at_deadline.add(future)
        if pending_futures or skipped_sources:
            self._cancel_providers()
        executor.shutdown(wait=True, cancel_futures=True)
        late_sources = sorted(
            futures[future]
            for future in running_at_deadline
        )
        not_started_sources = sorted(
            {
                *skipped_sources,
                *(
                    futures[future]
                    for future in cancelled_before_start
                ),
            }
        )
        deadline_sources = sorted(
            {
                *not_started_sources,
                *late_sources,
            }
        )
        self._set_provider_deadlines(None)

        records: list[dict[str, Any]] = []
        source_status: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        late_audit: list[dict[str, Any]] = []
        provider_metrics: dict[str, dict[str, Any]] = {}
        completion_times = [current]
        for source in sorted(self.providers):
            provider = self.providers[source]
            expected_types = _provider_data_types(provider)
            expected_version = _provider_source_version(provider)
            if source in deadline_sources:
                errors.append(
                    {
                        "source": source,
                        "error": "DEADLINE_EXCEEDED",
                    }
                )
                source_status.append(
                    _source_status_record(
                        source=source,
                        status="DEADLINE_EXCEEDED",
                        ok=False,
                        record_count=0,
                        data_types=expected_types,
                        started_at=current,
                        completed_at=collection_deadline,
                        source_version=expected_version,
                        error="global_collection_deadline_exceeded",
                        time_contract=contract,
                    )
                )
                provider_metrics[source] = {
                    "status": "DEADLINE_EXCEEDED",
                    "elapsed_ms": None,
                    "record_count": 0,
                }
                continue

            outcome = provider_results[source]
            started_at = outcome["started_at"]
            completed_at = outcome["completed_at"]
            completion_times.append(completed_at)
            provider_metrics[source] = {
                "status": outcome["status"],
                "elapsed_ms": outcome["elapsed_ms"],
                "record_count": 0,
            }
            if outcome["error"]:
                error_text = outcome["error"]
                errors.append({"source": source, "error": error_text})
                status = (
                    "DEADLINE_EXCEEDED"
                    if "deadline" in error_text.lower()
                    else "FAILED"
                )
                source_status.append(
                    _source_status_record(
                        source=source,
                        status=status,
                        ok=False,
                        record_count=0,
                        data_types=expected_types,
                        started_at=started_at,
                        completed_at=completed_at,
                        source_version=expected_version,
                        error=error_text,
                        time_contract=contract,
                    )
                )
                provider_metrics[source]["status"] = status
                continue

            (
                provider_rows,
                declared_types,
                source_version,
                source_raw_hash,
            ) = _provider_batch_fields(
                outcome["result"],
                expected_version=expected_version,
            )
            stamped_rows = [
                _stamp_provider_completion(
                    row,
                    started_at=started_at,
                    completed_at=completed_at,
                    time_contract=contract,
                )
                for row in provider_rows
            ]
            records.extend(stamped_rows)
            provider_metrics[source]["record_count"] = len(
                stamped_rows
            )
            status = (
                "SUCCESS"
                if completed_at <= collection_deadline
                else "LATE_AUDIT_ONLY"
            )
            provider_metrics[source]["status"] = status
            if status == "LATE_AUDIT_ONLY":
                late_audit.append(
                    {
                        "source": source,
                        "completed_at": completed_at.isoformat(
                            timespec="seconds"
                        ),
                        "record_count": len(stamped_rows),
                        "raw_hash": source_raw_hash,
                    }
                )
            source_status.append(
                _source_status_record(
                    source=source,
                    status=status,
                    ok=status == "SUCCESS",
                    record_count=len(stamped_rows),
                    data_types=sorted(
                        set(expected_types)
                        | set(declared_types)
                        | {
                            str(row.get("data_type") or "")
                            .strip()
                            .lower()
                            for row in stamped_rows
                            if row.get("data_type")
                        }
                    ),
                    started_at=started_at,
                    completed_at=completed_at,
                    source_version=source_version,
                    raw_hash=source_raw_hash,
                    error=(
                        "completed_after_collection_deadline"
                        if status == "LATE_AUDIT_ONLY"
                        else ""
                    ),
                    time_contract=contract,
                )
            )

        readiness = validate_close_confirmation_readiness(
            {
                "records": records,
                "source_status": source_status,
                "decision_time": decision_time.isoformat(
                    timespec="seconds"
                ),
                "time_contract": contract.as_dict(),
            },
        )
        if current < feature_cutoff:
            readiness["data_ready"] = False
            readiness["readiness_errors"] = list(
                dict.fromkeys(
                    [
                        *(readiness.get("readiness_errors") or []),
                        "feature_event_cutoff_not_reached",
                    ]
                )
            )
        normalized = readiness["normalized_snapshot"]
        accepted = list(normalized.get("records") or [])
        rejected = list(normalized.get("rejected_records") or [])
        collection_completed_at = max(completion_times)
        transport_metrics = self._provider_transport_metrics()
        completed_count = sum(
            1
            for item in provider_metrics.values()
            if item.get("status") == "SUCCESS"
        )
        collection_metrics = {
            "provider_count": len(self.providers),
            "provider_success_count": completed_count,
            "provider_success_ratio": round(
                completed_count / max(1, len(self.providers)),
                6,
            ),
            "not_started_provider_count": len(
                not_started_sources
            ),
            "late_provider_count": len(late_sources),
            "deadline_exceeded_count": sum(
                1
                for item in provider_metrics.values()
                if item.get("status") == "DEADLINE_EXCEEDED"
            ),
            **transport_metrics,
        }
        base = {
            "execution_ok": True,
            "data_ready": bool(readiness["data_ready"]),
            "observed_at": current.isoformat(timespec="seconds"),
            "completed_at": collection_completed_at.isoformat(
                timespec="seconds"
            ),
            "decision_time": decision_time.isoformat(
                timespec="seconds"
            ),
            "time_contract": contract.as_dict(),
            "records": accepted,
            "rejected_records": rejected,
            "errors": errors,
            "source_status": list(
                normalized.get("source_status") or []
            ),
            "rejected_source_status": list(
                normalized.get("rejected_source_status") or []
            ),
            "late_audit": late_audit,
            "provider_metrics": dict(sorted(provider_metrics.items())),
            "collection_metrics": collection_metrics,
            "ingest_hash": stable_hash(records),
            **_readiness_fields(readiness),
        }
        if not accepted:
            return {"status": "NO_VALID_RECORDS", **base}
        snapshot = {"status": "COLLECTED", **base}
        snapshot_id = current.strftime("%Y%m%d_%H%M%S")
        snapshot["path"] = str(
            self.store.write_once(
                "collection",
                snapshot_id,
                snapshot,
            )
        )
        return snapshot

    def _invoke_provider(
        self,
        source: str,
        provider: Callable[
            [datetime],
            list[dict[str, Any]] | ProviderBatch,
        ],
    ) -> dict[str, Any]:
        started_at = _coerce_cn(self.clock())
        started_monotonic = self.monotonic()
        try:
            result = provider(started_at)
            error = ""
            status = "SUCCESS"
        except Exception as exc:
            result = None
            error = f"{type(exc).__name__}: {exc}"
            status = "FAILED"
        completed_at = _coerce_cn(self.clock())
        return {
            "source": source,
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_ms": round(
                (self.monotonic() - started_monotonic) * 1000,
                3,
            ),
            "result": result,
            "error": error,
        }

    def _set_provider_deadlines(
        self,
        deadline: float | None,
    ) -> None:
        seen: set[tuple[int, int]] = set()
        for provider in self.providers.values():
            setter = getattr(provider, "deadline_setter", None)
            if setter is None:
                continue
            owner = getattr(setter, "__self__", None)
            function = getattr(setter, "__func__", setter)
            identity = (id(owner), id(function))
            if identity in seen:
                continue
            seen.add(identity)
            setter(deadline)

    def _cancel_providers(self) -> None:
        seen: set[tuple[int, int]] = set()
        for provider in self.providers.values():
            cancel = getattr(provider, "cancel_setter", None)
            if cancel is None:
                continue
            owner = getattr(cancel, "__self__", None)
            function = getattr(cancel, "__func__", cancel)
            identity = (id(owner), id(function))
            if identity in seen:
                continue
            seen.add(identity)
            cancel()

    def _provider_transport_metrics(self) -> dict[str, Any]:
        for provider in self.providers.values():
            getter = getattr(provider, "metrics_getter", None)
            if getter is not None:
                return dict(getter())
        return {}

    def freeze(
        self,
        trade_date: str | date,
        records: list[dict[str, Any]],
        *,
        source_status: list[dict[str, Any]] | None = None,
        time_contract: dict[str, Any] | CloseTimeContract | None = None,
    ) -> dict[str, Any]:
        day = date.fromisoformat(str(trade_date)) if not isinstance(trade_date, date) else trade_date
        contract = normalize_close_time_contract(
            time_contract or self.time_contract
        )
        decision_time = (
            contract_datetimes(contract)["decision_time"]
            if contract is not None
            else datetime.combine(day, FREEZE_TIME, tzinfo=CN_TZ)
        )
        snapshot_input = {
            "records": records,
            "source_status": source_status or [],
            "decision_time": decision_time.isoformat(timespec="seconds"),
        }
        if contract is not None:
            snapshot_input["time_contract"] = contract.as_dict()
        readiness = validate_close_confirmation_readiness(
            snapshot_input,
        )
        normalized = readiness["normalized_snapshot"]
        accepted = list(normalized.get("records") or [])
        rejected = list(normalized.get("rejected_records") or [])
        accepted_source_status = list(
            normalized.get("source_status") or []
        )
        rejected_source_status = list(
            normalized.get("rejected_source_status") or []
        )
        if not accepted:
            return {
                "status": "NO_VALID_RECORDS",
                "execution_ok": True,
                "data_ready": False,
                "trade_date": day.isoformat(),
                "decision_time": decision_time.isoformat(timespec="seconds"),
                "time_contract": (
                    contract.as_dict() if contract is not None else {}
                ),
                "records": [],
                "rejected_records": rejected,
                "record_count": 0,
                "rejected_count": len(rejected),
                "source_status": accepted_source_status,
                "rejected_source_status": rejected_source_status,
                **_readiness_fields(readiness),
            }
        decision_text = decision_time.isoformat(timespec="seconds")
        snapshot_hash = close_snapshot_hash(
            decision_time=decision_text,
            records=accepted,
            source_status=accepted_source_status,
            time_contract=contract,
        )
        frozen = {
            "status": "FROZEN_1450",
            "execution_ok": True,
            "data_ready": readiness["data_ready"],
            "trade_date": day.isoformat(),
            "decision_time": decision_text,
            "time_contract": (
                contract.as_dict() if contract is not None else {}
            ),
            "snapshot_contract_version": SNAPSHOT_CONTRACT_VERSION,
            "records": accepted,
            "rejected_records": rejected,
            "record_count": len(accepted),
            "rejected_count": len(rejected),
            "source_status": accepted_source_status,
            "snapshot_hash": snapshot_hash,
            **_readiness_fields(readiness),
        }
        frozen["path"] = str(self.store.write_once("frozen_1450", day.isoformat(), frozen))
        frozen["rejected_source_status"] = rejected_source_status
        if rejected_source_status:
            audit_payload = {
                "trade_date": day.isoformat(),
                "decision_time": decision_text,
                "time_contract": (
                    contract.as_dict() if contract is not None else {}
                ),
                "snapshot_contract_version": SNAPSHOT_CONTRACT_VERSION,
                "rejected_source_status": rejected_source_status,
                "audit_hash": stable_hash(rejected_source_status),
            }
            audit_id = (
                f"{day.isoformat()}_"
                f"{audit_payload['audit_hash'][:16]}"
            )
            frozen["source_status_audit_path"] = str(
                self.store.write_once(
                    "source_status_audit",
                    audit_id,
                    audit_payload,
                )
            )
        return frozen


def load_frozen_snapshot(path: str | Path, decision_time: str | datetime | None = None) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    time_contract = normalize_close_time_contract(
        payload.get("time_contract")
    )
    decision = (
        time_contract.decision_time
        if time_contract is not None
        else decision_time or payload.get("decision_time")
    )
    if not decision:
        raise PointInTimeError("decision_time_missing")
    accepted, rejected, _ = normalize_point_in_time_records(
        payload.get("records") or [],
        decision,
        time_contract=time_contract,
    )
    accepted_source_status, rejected_source_status = filter_source_status_at(
        payload.get("source_status") or [],
        decision,
        time_contract=time_contract,
    )
    if payload.get("snapshot_contract_version") == SNAPSHOT_CONTRACT_VERSION:
        expected_hash = close_snapshot_hash(
            decision_time=str(decision),
            records=accepted,
            source_status=accepted_source_status,
            time_contract=time_contract,
        )
        if str(payload.get("snapshot_hash") or "") != expected_hash:
            raise ImmutableSnapshotError("snapshot_hash_mismatch")
    return {
        **payload,
        "records": accepted,
        "rejected_records": list(payload.get("rejected_records") or []) + rejected,
        "source_status": accepted_source_status,
        "rejected_source_status": (
            list(payload.get("rejected_source_status") or [])
            + rejected_source_status
        ),
    }


def close_snapshot_hash(
    *,
    decision_time: str,
    records: list[dict[str, Any]],
    source_status: list[dict[str, Any]],
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> str:
    decision = parse_cn_datetime(decision_time)
    decision_text = (
        decision.isoformat(timespec="seconds")
        if decision is not None
        else str(decision_time)
    )
    normalized_records, _, _ = normalize_point_in_time_records(
        records,
        decision_text,
        time_contract=time_contract,
    )
    normalized_source_status, _ = filter_source_status_at(
        source_status,
        decision_text,
        time_contract=time_contract,
    )
    contract = normalize_close_time_contract(
        time_contract,
        fallback_decision_time=decision_text,
    )
    return stable_hash(
        {
            "decision_time": decision_text,
            "time_contract": (
                contract.as_dict() if contract is not None else {}
            ),
            "snapshot_contract_version": SNAPSHOT_CONTRACT_VERSION,
            "records": normalized_records,
            "source_status": normalized_source_status,
        }
    )


def _coerce_cn(value: datetime) -> datetime:
    return (value.replace(tzinfo=CN_TZ) if value.tzinfo is None else value).astimezone(CN_TZ)


def _readiness_fields(readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "coverage_by_type": dict(readiness.get("coverage_by_type") or {}),
        "proxy_coverage_by_type": dict(
            readiness.get("proxy_coverage_by_type") or {}
        ),
        "readiness_errors": list(readiness.get("readiness_errors") or []),
        "critical_source_status": dict(readiness.get("critical_source_status") or {}),
        "eligible_stock_codes": list(readiness.get("eligible_stock_codes") or []),
        "stock_readiness": dict(readiness.get("stock_readiness") or {}),
    }


def _source_status_record(
    *,
    source: str,
    status: str,
    ok: bool,
    record_count: int,
    data_types: list[str],
    started_at: datetime,
    completed_at: datetime,
    source_version: str = "close_window_collector_v1",
    raw_hash: str = "",
    error: str = "",
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> dict[str, Any]:
    started = _coerce_cn(started_at)
    completed = _coerce_cn(completed_at)
    contract = normalize_close_time_contract(
        time_contract,
        fallback_decision_time=datetime.combine(
            started.date(),
            FREEZE_TIME,
            tzinfo=CN_TZ,
        ),
    )
    payload = {
        "source": source,
        "status": status,
        "ok": ok,
        "record_count": int(record_count),
        "data_types": list(data_types),
        "error": error,
    }
    return {
        **payload,
        "event_time": completed.isoformat(timespec="seconds"),
        "observed_at": started.isoformat(timespec="seconds"),
        "available_at": completed.isoformat(timespec="seconds"),
        "started_at": started.isoformat(timespec="seconds"),
        "completed_at": completed.isoformat(timespec="seconds"),
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
        "source_version": source_version,
        "raw_hash": raw_hash or stable_hash(payload),
    }


def _stamp_provider_completion(
    row: dict[str, Any],
    *,
    started_at: datetime,
    completed_at: datetime,
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> dict[str, Any]:
    stamped = dict(row)
    original_available = parse_cn_datetime(stamped.get("available_at"))
    effective_available = max(
        completed_at,
        original_available or completed_at,
    )
    stamped["available_at"] = effective_available.isoformat(
        timespec="seconds"
    )
    stamped["provider_started_at"] = started_at.isoformat(
        timespec="seconds"
    )
    stamped["provider_completed_at"] = completed_at.isoformat(
        timespec="seconds"
    )
    contract = normalize_close_time_contract(time_contract)
    if contract is not None:
        stamped.update(
            {
                "decision_cutoff": contract.decision_time,
                "feature_event_cutoff": (
                    contract.feature_event_cutoff
                ),
                "collection_deadline": contract.collection_deadline,
                "decision_time": contract.decision_time,
                "execution_not_before": (
                    contract.execution_not_before
                ),
                "time_contract_version": contract.contract_version,
                "minute_label_semantics": (
                    contract.minute_label_semantics
                ),
                "minute_label_validation_status": (
                    contract.minute_label_validation_status
                ),
            }
        )
    return stamped


def _provider_data_types(provider: Any) -> list[str]:
    return sorted(
        {
            str(item).strip().lower()
            for item in getattr(provider, "data_types", [])
            if str(item).strip()
        }
    )


def _provider_submission_order(
    providers: dict[str, Any],
) -> list[str]:
    return sorted(
        providers,
        key=lambda source: (
            int(getattr(providers[source], "priority", 3)),
            str(getattr(providers[source], "stage", "news")),
            source,
        ),
    )


def _provider_source_version(provider: Any) -> str:
    return str(
        getattr(provider, "source_version", "")
        or "close_window_collector_v1"
    )


def _provider_batch_fields(
    value: list[dict[str, Any]] | ProviderBatch | None,
    *,
    expected_version: str,
) -> tuple[list[dict[str, Any]], list[str], str, str]:
    if isinstance(value, ProviderBatch):
        return (
            [dict(item) for item in value.records],
            sorted(
                {
                    str(item).strip().lower()
                    for item in value.data_types
                    if str(item).strip()
                }
            ),
            value.source_version or expected_version,
            value.raw_hash,
        )
    return (
        [dict(item) for item in (value or [])],
        [],
        expected_version,
        "",
    )
