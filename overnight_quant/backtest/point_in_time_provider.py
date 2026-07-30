from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable

from overnight_quant.data.close_confirmation_readiness import (
    normalize_close_confirmation_snapshot,
)
from overnight_quant.data.close_time_contract import (
    normalize_close_time_contract,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import parse_cn_datetime
from overnight_quant.data.snapshot_store import load_frozen_snapshot


class PointInTimeDataError(RuntimeError):
    pass


class PointInTimeProvider:
    """Expose only records that were knowable at the requested decision time."""

    def __init__(self, snapshots: Iterable[dict[str, Any]] | None = None) -> None:
        self._snapshots = [deepcopy(item) for item in (snapshots or [])]

    @classmethod
    def from_frozen_file(cls, path: str | Path) -> "PointInTimeProvider":
        return cls([load_frozen_snapshot(path)])

    def snapshot_at(
        self,
        trade_date: str | date,
        decision_time: str | datetime = "14:50",
        *,
        require_minute_data: bool = True,
    ) -> dict[str, Any]:
        decision = _decision_datetime(trade_date, decision_time)
        matching = [
            item
            for item in self._snapshots
            if str(item.get("trade_date") or "") == decision.date().isoformat()
        ]
        if not matching:
            raise PointInTimeDataError("FROZEN_SNAPSHOT_NOT_FOUND")
        snapshot = max(
            matching,
            key=lambda item: _timestamp_value(item.get("frozen_at") or item.get("decision_time")),
        )
        time_contract = normalize_close_time_contract(
            snapshot.get("time_contract"),
            fallback_decision_time=decision,
        )
        if (
            time_contract is not None
            and time_contract.contract_version
            != "legacy_single_cutoff_v1"
        ):
            contract_decision = parse_cn_datetime(
                time_contract.decision_time
            )
            if contract_decision is not None:
                decision = contract_decision
        result = deepcopy(snapshot)
        result = normalize_close_confirmation_snapshot(
            result,
            decision_time=decision,
        )
        for stock in result.get("stocks") or []:
            stock["pit_data_errors"] = list(stock.get("pit_data_errors") or [])
            if require_minute_data and not stock.get("intraday_bars"):
                stock["pit_data_errors"].append("minute_data_required")
            stock.pop("close", None)
            stock.pop("closing_price", None)
            stock.pop("daily_close", None)
        result["pit_rejected_records"] = [
            *(result.get("rejected_records") or []),
            *(result.get("nested_rejections") or []),
        ]
        return result


def _decision_datetime(trade_date: str | date, value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    day = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date))
    parsed = parse_cn_datetime(value)
    if parsed is not None and ("T" in str(value) or " " in str(value)):
        return parsed
    hour, minute = (int(part) for part in str(value).split(":", 1))
    return datetime.combine(day, time(hour=hour, minute=minute), tzinfo=CN_TZ)


def _timestamp_value(value: Any) -> float:
    parsed = parse_cn_datetime(value)
    return parsed.timestamp() if parsed else float("-inf")
