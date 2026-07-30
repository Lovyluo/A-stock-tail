from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
from typing import Any

from overnight_quant.data.point_in_time import parse_cn_datetime, records_available_at


MIN_MINUTE_BARS = 12
MIN_DAILY_BARS = 60
CRITICAL_DATA_TYPES = (
    "market",
    "industry",
    "quote",
    "minute_bar",
    "daily_bar",
    "fund_flow",
    "news",
)
SUCCESS_SOURCE_STATES = {"SUCCESS", "READY", "OK", "AVAILABLE", "AVAILABLE_EMPTY"}
FAILED_SOURCE_STATES = {"FAILED", "ERROR", "UNAVAILABLE", "TIMEOUT"}


def validate_close_confirmation_readiness(
    snapshot: dict[str, Any],
    *,
    decision_time: str | datetime | None = None,
    min_minute_bars: int = MIN_MINUTE_BARS,
    min_daily_bars: int = MIN_DAILY_BARS,
) -> dict[str, Any]:
    decision = parse_cn_datetime(decision_time or snapshot.get("decision_time"))
    if decision is None:
        return _invalid_decision_result()

    accepted_records, rejected_records = records_available_at(
        snapshot.get("records") or [],
        decision,
        require_published_at_for_news=True,
    )
    view = deepcopy(snapshot)
    view["records"] = accepted_records
    if not view.get("stocks") and accepted_records:
        view.update(materialize_point_in_time_records(accepted_records))

    coverage = _coverage_by_type(view, accepted_records, decision)
    source_status = _critical_source_status(view, accepted_records, coverage)
    readiness_errors: list[str] = []

    market = _market_snapshot(view)
    if not market:
        readiness_errors.append("market_snapshot_missing")
    else:
        if not _valid_number(market.get("index_change_pct")):
            readiness_errors.append("market_index_strength_missing")
        if not _valid_ratio(market.get("breadth_ratio")):
            readiness_errors.append("market_breadth_missing")

    news_status = source_status["news"]["status"]
    if news_status == "FAILED":
        readiness_errors.append("news_source_failed")
    elif news_status == "MISSING":
        readiness_errors.append("news_source_status_missing")

    stock_readiness: dict[str, dict[str, Any]] = {}
    eligible_codes: list[str] = []
    for stock in view.get("stocks") or []:
        code = str(stock.get("code") or "").zfill(6)
        errors = _stock_readiness_errors(
            stock,
            decision,
            min_minute_bars=max(1, int(min_minute_bars)),
            min_daily_bars=max(1, int(min_daily_bars)),
        )
        stock_readiness[code] = {
            "ready": not errors,
            "errors": errors,
            "minute_bar_count": len(_bars_at_decision(stock, decision)),
            "daily_bar_count": len(_valid_daily_bars(stock.get("daily_bars") or [])),
        }
        if not errors:
            eligible_codes.append(code)

    if not eligible_codes:
        readiness_errors.append("no_scoreable_stock")
        for code, item in stock_readiness.items():
            readiness_errors.extend(f"{code}:{error}" for error in item["errors"])

    readiness_errors = list(dict.fromkeys(readiness_errors))
    return {
        "data_ready": not readiness_errors,
        "coverage_by_type": coverage,
        "readiness_errors": readiness_errors,
        "critical_source_status": source_status,
        "eligible_stock_codes": eligible_codes,
        "stock_readiness": stock_readiness,
        "accepted_record_count": len(accepted_records),
        "rejected_record_count": len(rejected_records),
    }


def materialize_point_in_time_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    stocks: dict[str, dict[str, Any]] = {}
    market_records: list[dict[str, Any]] = []
    industry_records: list[dict[str, Any]] = []
    global_news: list[dict[str, Any]] = []
    for record in records:
        row = deepcopy(record)
        payload = deepcopy(record.get("payload") or {})
        data_type = _normalized_data_type(record)
        code = str(payload.get("code") or record.get("code") or "").zfill(6)
        if data_type == "market":
            market_records.append(row)
            continue
        if data_type == "industry":
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
                "pit_record_types": [],
            },
        )
        stock["pit_record_types"] = list(
            dict.fromkeys([*(stock.get("pit_record_types") or []), data_type])
        )
        if data_type == "quote":
            stock.update(payload)
        elif data_type == "minute_bar":
            stock["intraday_bars"].append({**payload, **_temporal_fields(row)})
        elif data_type == "daily_bar":
            stock["daily_bars"].append({**payload, **_temporal_fields(row)})
        elif data_type == "fund_flow":
            stock["fund_flow"].append({**payload, **_temporal_fields(row)})
        elif data_type == "news":
            stock["news"].append({**payload, **_temporal_fields(row)})

    market_payload = deepcopy(market_records[-1].get("payload") or {}) if market_records else {}
    industries = {
        str(
            (item.get("payload") or {}).get("name")
            or (item.get("payload") or {}).get("industry")
            or ""
        ): deepcopy(item.get("payload") or {})
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


def _stock_readiness_errors(
    stock: dict[str, Any],
    decision: datetime,
    *,
    min_minute_bars: int,
    min_daily_bars: int,
) -> list[str]:
    errors: list[str] = []
    if not _valid_positive(stock.get("price")):
        errors.append("quote_price_missing")
    if not _valid_positive(stock.get("prev_close")):
        errors.append("quote_prev_close_missing")
    if not _valid_nonnegative(stock.get("amount_wan")):
        errors.append("quote_amount_missing")
    if not _valid_nonnegative(stock.get("turnover_pct")):
        errors.append("quote_turnover_missing")
    if "suspended" not in stock:
        errors.append("quote_suspended_status_missing")
    if "is_limit_up" not in stock or "is_limit_down" not in stock:
        errors.append("quote_limit_status_missing")

    bars = _bars_at_decision(stock, decision)
    if len(bars) < min_minute_bars:
        errors.append("minute_bar_count_below_minimum")
    if not bars or _record_datetime(bars[-1], decision) < decision.replace(second=0, microsecond=0):
        errors.append("minute_bar_not_complete_1450")

    industry = stock.get("industry") if isinstance(stock.get("industry"), dict) else {}
    if not industry:
        errors.append("industry_snapshot_missing")
    else:
        expected_name = str(stock.get("industry_name") or "").strip()
        actual_name = str(industry.get("name") or industry.get("industry") or "").strip()
        if not expected_name:
            errors.append("quote_industry_name_missing")
        if not actual_name:
            errors.append("industry_snapshot_name_missing")
        elif expected_name and expected_name != actual_name:
            errors.append("industry_snapshot_mismatch")
        if not (
            _valid_number(industry.get("relative_strength_pct"))
            or _valid_number(industry.get("change_pct"))
        ):
            errors.append("industry_strength_missing")
        if not _valid_ratio(industry.get("breadth_ratio")):
            errors.append("industry_breadth_missing")

    daily_bars = _valid_daily_bars(stock.get("daily_bars") or [])
    if len(daily_bars) < min_daily_bars:
        errors.append("daily_bar_history_insufficient")
    if not _valid_fund_flow(stock.get("fund_flow") or []):
        errors.append("fund_flow_missing")
    return list(dict.fromkeys(errors))


def _coverage_by_type(
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    decision: datetime,
) -> dict[str, int]:
    coverage = {name: 0 for name in CRITICAL_DATA_TYPES}
    for row in records:
        data_type = _normalized_data_type(row)
        if data_type in coverage:
            coverage[data_type] += 1
    stocks = snapshot.get("stocks") or []
    if coverage["quote"] == 0:
        coverage["quote"] = sum(1 for stock in stocks if _looks_like_quote(stock))
    if coverage["minute_bar"] == 0:
        coverage["minute_bar"] = sum(len(_bars_at_decision(stock, decision)) for stock in stocks)
    if coverage["daily_bar"] == 0:
        coverage["daily_bar"] = sum(len(_valid_daily_bars(stock.get("daily_bars") or [])) for stock in stocks)
    if coverage["fund_flow"] == 0:
        coverage["fund_flow"] = sum(len(stock.get("fund_flow") or []) for stock in stocks)
    if coverage["news"] == 0:
        coverage["news"] = len(_valid_news_rows(snapshot.get("news") or [], decision))
        coverage["news"] += sum(
            len(_valid_news_rows(stock.get("news") or [], decision))
            for stock in stocks
        )
    if coverage["market"] == 0 and _market_snapshot(snapshot):
        coverage["market"] = 1
    if coverage["industry"] == 0:
        coverage["industry"] = sum(1 for stock in stocks if isinstance(stock.get("industry"), dict) and stock.get("industry"))
    return coverage


def _critical_source_status(
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    coverage: dict[str, int],
) -> dict[str, dict[str, Any]]:
    explicit_rows = list(snapshot.get("source_status") or [])
    result: dict[str, dict[str, Any]] = {}
    for data_type in CRITICAL_DATA_TYPES:
        record_sources = sorted(
            {
                str(row.get("source") or "")
                for row in records
                if _normalized_data_type(row) == data_type and row.get("source")
            }
        )
        matching = [
            row
            for row in explicit_rows
            if _source_row_matches(row, data_type)
        ]
        explicit_sources = [str(row.get("source") or "") for row in matching if row.get("source")]
        states = {_source_state(row) for row in matching}
        if coverage.get(data_type, 0) > 0:
            status = "AVAILABLE"
        elif states & SUCCESS_SOURCE_STATES:
            status = "AVAILABLE_EMPTY" if data_type == "news" else "MISSING"
        elif states & FAILED_SOURCE_STATES:
            status = "FAILED"
        else:
            status = "MISSING"
        result[data_type] = {
            "status": status,
            "record_count": int(coverage.get(data_type, 0)),
            "sources": sorted(set(record_sources + explicit_sources)),
        }
    return result


def _source_row_matches(row: dict[str, Any], data_type: str) -> bool:
    row_type = _normalized_data_type(row)
    if row_type == data_type:
        return True
    return data_type in {
        _canonical_data_type(str(item))
        for item in (row.get("data_types") or [])
    }


def _source_state(row: dict[str, Any]) -> str:
    if "ok" in row:
        return "SUCCESS" if bool(row.get("ok")) else "FAILED"
    return str(row.get("status") or row.get("state") or "").strip().upper()


def _market_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    for row in reversed(snapshot.get("market_records") or []):
        payload = row.get("payload") if isinstance(row, dict) else None
        if isinstance(payload, dict) and payload:
            return payload
    for stock in snapshot.get("stocks") or []:
        market = stock.get("market")
        if isinstance(market, dict) and market:
            return market
    return {}


def _bars_at_decision(stock: dict[str, Any], decision: datetime) -> list[dict[str, Any]]:
    bars = []
    for row in stock.get("intraday_bars") or stock.get("minute_bars") or []:
        stamp = _record_datetime(row, decision)
        if stamp is not None and stamp <= decision:
            bars.append(row)
    return sorted(bars, key=lambda row: _record_datetime(row, decision))


def _record_datetime(row: dict[str, Any], decision: datetime) -> datetime | None:
    value = row.get("available_at") or row.get("event_time") or row.get("datetime") or row.get("time")
    text = str(value or "")
    if len(text) <= 8 and ":" in text:
        try:
            hour, minute, *seconds = (int(part) for part in text.split(":"))
            return decision.replace(
                hour=hour,
                minute=minute,
                second=seconds[0] if seconds else 0,
                microsecond=0,
            )
        except (TypeError, ValueError):
            return None
    return parse_cn_datetime(value)


def _valid_daily_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if _valid_positive(row.get("close"))
        and _valid_positive(row.get("volume", row.get("vol")))
    ]


def _valid_fund_flow(rows: list[dict[str, Any]]) -> bool:
    return any(
        any(
            _valid_number(row.get(field))
            for field in ("main_net", "large_net", "super_net")
        )
        for row in rows
    )


def _valid_news_rows(
    rows: list[dict[str, Any]],
    decision: datetime,
) -> list[dict[str, Any]]:
    valid = []
    for row in rows:
        published = parse_cn_datetime(row.get("published_at"))
        available = parse_cn_datetime(row.get("available_at"))
        if (
            published is not None
            and available is not None
            and published <= decision
            and available <= decision
            and str(row.get("source") or "").strip()
        ):
            valid.append(row)
    return valid


def _looks_like_quote(stock: dict[str, Any]) -> bool:
    return bool(
        str(stock.get("code") or "").strip()
        and _valid_positive(stock.get("price"))
        and _valid_positive(stock.get("prev_close"))
    )


def _normalized_data_type(row: dict[str, Any]) -> str:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return _canonical_data_type(str(row.get("data_type") or payload.get("data_type") or ""))


def _canonical_data_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "market_snapshot": "market",
        "industry_snapshot": "industry",
        "stock": "quote",
        "stock_snapshot": "quote",
        "intraday_bar": "minute_bar",
    }
    return aliases.get(normalized, normalized)


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


def _valid_number(value: Any) -> bool:
    try:
        return value not in (None, "", "-") and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _valid_positive(value: Any) -> bool:
    return _valid_number(value) and float(value) > 0


def _valid_nonnegative(value: Any) -> bool:
    return _valid_number(value) and float(value) >= 0


def _valid_ratio(value: Any) -> bool:
    return _valid_number(value) and 0.0 <= float(value) <= 1.0


def _invalid_decision_result() -> dict[str, Any]:
    return {
        "data_ready": False,
        "coverage_by_type": {name: 0 for name in CRITICAL_DATA_TYPES},
        "readiness_errors": ["decision_time_invalid"],
        "critical_source_status": {
            name: {"status": "MISSING", "record_count": 0, "sources": []}
            for name in CRITICAL_DATA_TYPES
        },
        "eligible_stock_codes": [],
        "stock_readiness": {},
        "accepted_record_count": 0,
        "rejected_record_count": 0,
    }
