from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
import json
from pathlib import Path
from typing import Any, Callable

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
    ):
        self.store = store
        self.providers = dict(providers)
        self.clock = clock or (lambda: datetime.now(CN_TZ))

    def collect(self, observed_at: datetime) -> dict[str, Any]:
        current = _coerce_cn(observed_at)
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

    def freeze(
        self,
        trade_date: str | date,
        records: list[dict[str, Any]],
        *,
        source_status: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        day = date.fromisoformat(str(trade_date)) if not isinstance(trade_date, date) else trade_date
        decision_time = datetime.combine(day, FREEZE_TIME, tzinfo=CN_TZ)
        readiness = validate_close_confirmation_readiness(
            {
                "records": records,
                "source_status": source_status or [],
                "decision_time": decision_time.isoformat(timespec="seconds"),
            },
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
        )
        frozen = {
            "status": "FROZEN_1450",
            "execution_ok": True,
            "data_ready": readiness["data_ready"],
            "trade_date": day.isoformat(),
            "decision_time": decision_text,
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
    decision = decision_time or payload.get("decision_time")
    if not decision:
        raise PointInTimeError("decision_time_missing")
    accepted, rejected, _ = normalize_point_in_time_records(
        payload.get("records") or [],
        decision,
    )
    accepted_source_status, rejected_source_status = filter_source_status_at(
        payload.get("source_status") or [],
        decision,
    )
    if payload.get("snapshot_contract_version") == SNAPSHOT_CONTRACT_VERSION:
        expected_hash = close_snapshot_hash(
            decision_time=str(decision),
            records=accepted,
            source_status=accepted_source_status,
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
    )
    normalized_source_status, _ = filter_source_status_at(
        source_status,
        decision_text,
    )
    return stable_hash(
        {
            "decision_time": decision_text,
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
) -> dict[str, Any]:
    started = _coerce_cn(started_at)
    completed = _coerce_cn(completed_at)
    cutoff = datetime.combine(
        started.date(),
        FREEZE_TIME,
        tzinfo=CN_TZ,
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
        "decision_cutoff": cutoff.isoformat(timespec="seconds"),
        "source_version": source_version,
        "raw_hash": raw_hash or stable_hash(payload),
    }


def _stamp_provider_completion(
    row: dict[str, Any],
    *,
    started_at: datetime,
    completed_at: datetime,
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
    return stamped
