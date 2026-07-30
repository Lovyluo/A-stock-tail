from __future__ import annotations

from datetime import date, datetime, time
import json
from pathlib import Path
from typing import Any, Callable

from overnight_quant.data.close_confirmation_readiness import (
    SNAPSHOT_CONTRACT_VERSION,
    filter_source_status_at,
    validate_close_confirmation_readiness,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import (
    PointInTimeError,
    parse_cn_datetime,
    records_available_at,
    stable_hash,
)


COLLECTION_START = time(14, 40)
FREEZE_TIME = time(14, 50)


class ImmutableSnapshotError(RuntimeError):
    pass


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
        providers: dict[str, Callable[[datetime], list[dict[str, Any]]]],
    ):
        self.store = store
        self.providers = dict(providers)

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
        for source, provider in self.providers.items():
            try:
                provider_rows = [dict(item) for item in provider(current)]
                records.extend(provider_rows)
                source_status.append(
                    _source_status_record(
                        source=source,
                        status="SUCCESS",
                        ok=True,
                        record_count=len(provider_rows),
                        data_types=sorted(
                            {
                                str(row.get("data_type") or "").strip().lower()
                                for row in provider_rows
                                if row.get("data_type")
                            }
                        ),
                        observed_at=current,
                    )
                )
            except Exception as exc:
                errors.append({"source": source, "error": f"{type(exc).__name__}: {exc}"})
                source_status.append(
                    _source_status_record(
                        source=source,
                        status="FAILED",
                        ok=False,
                        record_count=0,
                        data_types=[],
                        observed_at=current,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        accepted, rejected = records_available_at(records, current)
        readiness = validate_close_confirmation_readiness(
            {
                "records": accepted,
                "source_status": source_status,
                "decision_time": current.isoformat(timespec="seconds"),
            },
        )
        if current.time() < FREEZE_TIME:
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
                "records": [],
                "rejected_records": rejected,
                "errors": errors,
                "source_status": list(
                    readiness["normalized_snapshot"].get("source_status") or []
                ),
                **_readiness_fields(readiness),
            }
        snapshot = {
            "status": "COLLECTED",
            "execution_ok": True,
            "data_ready": readiness["data_ready"],
            "observed_at": current.isoformat(timespec="seconds"),
            "decision_time": current.isoformat(timespec="seconds"),
            "records": accepted,
            "rejected_records": rejected,
            "errors": errors,
            "source_status": list(
                readiness["normalized_snapshot"].get("source_status") or []
            ),
            "raw_hash": stable_hash(accepted),
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
        accepted, rejected = records_available_at(records, decision_time)
        accepted_source_status, rejected_source_status = filter_source_status_at(
            source_status or [],
            decision_time,
        )
        readiness = validate_close_confirmation_readiness(
            {
                "records": accepted,
                "source_status": accepted_source_status,
                "decision_time": decision_time.isoformat(timespec="seconds"),
            },
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
    accepted, rejected = records_available_at(payload.get("records") or [], decision)
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
    return stable_hash(
        {
            "decision_time": decision_text,
            "snapshot_contract_version": SNAPSHOT_CONTRACT_VERSION,
            "records": records,
            "source_status": source_status,
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
    observed_at: datetime,
    error: str = "",
) -> dict[str, Any]:
    current = _coerce_cn(observed_at)
    cutoff = datetime.combine(
        current.date(),
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
        "event_time": current.isoformat(timespec="seconds"),
        "observed_at": current.isoformat(timespec="seconds"),
        "available_at": current.isoformat(timespec="seconds"),
        "decision_cutoff": cutoff.isoformat(timespec="seconds"),
        "source_version": "close_window_collector_v1",
        "raw_hash": stable_hash(payload),
    }
