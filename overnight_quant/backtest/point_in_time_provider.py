from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable

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
            result.update(_materialize_records(result.get("records") or []))
        result["stocks"] = [
            self._stock_at_decision(stock, decision, require_minute_data=require_minute_data)
            for stock in result.get("stocks", [])
        ]
        result["stocks"] = [stock for stock in result["stocks"] if stock is not None]
        if require_minute_data and not result["stocks"]:
            raise PointInTimeDataError("NO_STOCKS_WITH_MINUTE_DATA")
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
        if require_minute_data and not bars:
            raise PointInTimeDataError(f"MINUTE_DATA_REQUIRED:{stock.get('code', '')}")
        result["intraday_bars"] = bars
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


def _materialize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    stocks: dict[str, dict[str, Any]] = {}
    market_records: list[dict[str, Any]] = []
    industry_records: list[dict[str, Any]] = []
    global_news: list[dict[str, Any]] = []
    for record in records:
        row = deepcopy(record)
        payload = deepcopy(record.get("payload") or {})
        data_type = str(record.get("data_type") or payload.get("data_type") or "").lower()
        code = str(payload.get("code") or record.get("code") or "").zfill(6)
        if data_type in {"market", "market_snapshot"}:
            market_records.append(row)
            continue
        if data_type in {"industry", "industry_snapshot"}:
            industry_records.append(row)
            continue
        if data_type == "news" and not code.strip("0"):
            global_news.append(row)
            continue
        if not code.strip("0"):
            continue
        stock = stocks.setdefault(
            code,
            {
                "code": code,
                "name": payload.get("name", ""),
                "intraday_bars": [],
                "daily_bars": [],
                "fund_flow": [],
                "news": [],
            },
        )
        if data_type in {"stock", "stock_snapshot", "quote"}:
            stock.update(payload)
        elif data_type in {"minute_bar", "intraday_bar"}:
            stock["intraday_bars"].append({**payload, **_temporal_fields(row)})
        elif data_type == "daily_bar":
            stock["daily_bars"].append({**payload, **_temporal_fields(row)})
        elif data_type == "fund_flow":
            stock["fund_flow"].append({**payload, **_temporal_fields(row)})
        elif data_type == "news":
            stock["news"].append({**payload, **_temporal_fields(row)})
    market_payload = deepcopy(market_records[-1].get("payload") or {}) if market_records else {}
    industries = {
        str((item.get("payload") or {}).get("name") or (item.get("payload") or {}).get("industry") or ""): deepcopy(
            item.get("payload") or {}
        )
        for item in industry_records
    }
    for stock in stocks.values():
        stock.setdefault("market", market_payload)
        industry_name = str(stock.get("industry_name") or stock.get("industry") or "")
        if isinstance(stock.get("industry"), dict):
            continue
        stock["industry"] = industries.get(industry_name, {})
    return {
        "stocks": list(stocks.values()),
        "market_records": market_records,
        "industry_records": industry_records,
        "news": global_news,
    }


def _temporal_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "event_time",
            "published_at",
            "observed_at",
            "available_at",
            "decision_cutoff",
            "source",
            "source_version",
            "request_hash",
            "raw_hash",
        )
    }


def _timestamp_value(value: Any) -> float:
    parsed = parse_cn_datetime(value)
    return parsed.timestamp() if parsed else float("-inf")
