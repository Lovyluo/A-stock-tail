from __future__ import annotations

from datetime import date, datetime, time
import json
from pathlib import Path
from typing import Any, Callable

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
                "observed_at": current.isoformat(timespec="seconds"),
                "records": [],
            }
        records: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for source, provider in self.providers.items():
            try:
                records.extend(dict(item) for item in provider(current))
            except Exception as exc:
                errors.append({"source": source, "error": f"{type(exc).__name__}: {exc}"})
        snapshot = {
            "status": "COLLECTED",
            "observed_at": current.isoformat(timespec="seconds"),
            "records": records,
            "errors": errors,
            "raw_hash": stable_hash(records),
        }
        snapshot_id = current.strftime("%Y%m%d_%H%M%S")
        snapshot["path"] = str(self.store.write_once("collection", snapshot_id, snapshot))
        return snapshot

    def freeze(self, trade_date: str | date, records: list[dict[str, Any]]) -> dict[str, Any]:
        day = date.fromisoformat(str(trade_date)) if not isinstance(trade_date, date) else trade_date
        decision_time = datetime.combine(day, FREEZE_TIME, tzinfo=CN_TZ)
        accepted, rejected = records_available_at(records, decision_time)
        frozen = {
            "status": "FROZEN_1450",
            "trade_date": day.isoformat(),
            "decision_time": decision_time.isoformat(timespec="seconds"),
            "records": accepted,
            "rejected_records": rejected,
            "record_count": len(accepted),
            "rejected_count": len(rejected),
            "snapshot_hash": stable_hash(accepted),
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
