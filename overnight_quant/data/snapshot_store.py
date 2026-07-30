from __future__ import annotations

from datetime import date, datetime, time
import json
from pathlib import Path
from typing import Any, Callable

from overnight_quant.data.close_confirmation_readiness import (
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
                    {
                        "source": source,
                        "status": "SUCCESS",
                        "ok": True,
                        "record_count": len(provider_rows),
                        "data_types": sorted(
                            {
                                str(row.get("data_type") or "").strip().lower()
                                for row in provider_rows
                                if row.get("data_type")
                            }
                        ),
                    }
                )
            except Exception as exc:
                errors.append({"source": source, "error": f"{type(exc).__name__}: {exc}"})
                source_status.append(
                    {
                        "source": source,
                        "status": "FAILED",
                        "ok": False,
                        "record_count": 0,
                        "data_types": [],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
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
                "source_status": source_status,
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
            "source_status": source_status,
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
        readiness = validate_close_confirmation_readiness(
            {
                "records": accepted,
                "source_status": list(source_status or []),
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
                "source_status": list(source_status or []),
                **_readiness_fields(readiness),
            }
        frozen = {
            "status": "FROZEN_1450",
            "execution_ok": True,
            "data_ready": readiness["data_ready"],
            "trade_date": day.isoformat(),
            "decision_time": decision_time.isoformat(timespec="seconds"),
            "records": accepted,
            "rejected_records": rejected,
            "record_count": len(accepted),
            "rejected_count": len(rejected),
            "source_status": list(source_status or []),
            "snapshot_hash": stable_hash(accepted),
            **_readiness_fields(readiness),
        }
        frozen["path"] = str(self.store.write_once("frozen_1450", day.isoformat(), frozen))
        return frozen


def load_frozen_snapshot(path: str | Path, decision_time: str | datetime | None = None) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    decision = decision_time or payload.get("decision_time")
    if not decision:
        raise PointInTimeError("decision_time_missing")
    accepted, rejected = records_available_at(payload.get("records") or [], decision)
    return {
        **payload,
        "records": accepted,
        "rejected_records": list(payload.get("rejected_records") or []) + rejected,
    }


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
