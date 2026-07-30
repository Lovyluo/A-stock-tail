from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable

from overnight_quant.data.close_confirmation_readiness import materialize_point_in_time_records
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import parse_cn_datetime, records_available_at
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
        result = deepcopy(snapshot)
        result["decision_time"] = decision.isoformat()
        accepted_records, rejected_records = records_available_at(
            result.get("records") or [],
            decision,
            require_published_at_for_news=True,
        )
        result["records"] = accepted_records
        result["rejected_records"] = list(result.get("rejected_records") or []) + rejected_records
        if not result.get("stocks") and result.get("records"):
            result.update(materialize_point_in_time_records(result.get("records") or []))
        result["stocks"] = [
            self._stock_at_decision(stock, decision, require_minute_data=require_minute_data)
            for stock in result.get("stocks", [])
        ]
        result["stocks"] = [stock for stock in result["stocks"] if stock is not None]
        market_records, market_rejected = records_available_at(result.get("market_records", []), decision)
        industry_records, industry_rejected = records_available_at(result.get("industry_records", []), decision)
        news, news_rejected = records_available_at(
            result.get("news", []),
            decision,
            require_published_at_for_news=True,
        )
        result["market_records"] = market_records
        result["industry_records"] = industry_records
        result["news"] = news
        result["pit_rejected_records"] = (
            list(result.get("rejected_records") or [])
            + market_rejected
            + industry_rejected
            + news_rejected
        )
        return result

    @staticmethod
    def _stock_at_decision(
        stock: dict[str, Any],
        decision: datetime,
        *,
        require_minute_data: bool,
    ) -> dict[str, Any] | None:
        result = deepcopy(stock)
        bars = []
        source_bars = stock.get("intraday_bars") or stock.get("minute_bars") or []
        for bar in source_bars:
            available = parse_cn_datetime(bar.get("available_at") or bar.get("observed_at") or bar.get("event_time"))
            if available is not None and available <= decision:
                bars.append(deepcopy(bar))
        result["intraday_bars"] = bars
        result["pit_data_errors"] = list(result.get("pit_data_errors") or [])
        if require_minute_data and not bars:
            result["pit_data_errors"].append("minute_data_required")
        result.pop("minute_bars", None)
        news, rejected_news = records_available_at(
            stock.get("news", []),
            decision,
            require_published_at_for_news=True,
        )
        result["news"] = news
        result["pit_rejected_news"] = rejected_news
        result.pop("close", None)
        result.pop("closing_price", None)
        result.pop("daily_close", None)
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
