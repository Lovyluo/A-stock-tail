from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import math
from typing import Any, Iterable

from overnight_quant.data.point_in_time import (
    parse_cn_datetime,
    records_available_at,
    stable_hash,
)


SNAPSHOT_CONTRACT_VERSION = "close_confirmation_pit_v2"
MIN_MINUTE_BARS = 12
MIN_DAILY_BARS = 60
REQUIRED_NESTED_TEMPORAL_FIELDS = (
    "event_time",
    "observed_at",
    "available_at",
    "decision_cutoff",
    "source",
    "source_version",
    "raw_hash",
)
CRITICAL_DATA_TYPES = (
    "market",
    "industry",
    "quote",
    "minute_bar",
    "daily_bar",
    "fund_flow",
    "news",
)
SUCCESS_SOURCE_STATES = {
    "SUCCESS",
    "READY",
    "OK",
    "AVAILABLE",
    "AVAILABLE_EMPTY",
}
FAILED_SOURCE_STATES = {"FAILED", "ERROR", "UNAVAILABLE", "TIMEOUT"}


def validate_close_confirmation_readiness(
    snapshot: dict[str, Any],
    *,
    decision_time: str | datetime | None = None,
    min_minute_bars: int = MIN_MINUTE_BARS,
    min_daily_bars: int = MIN_DAILY_BARS,
) -> dict[str, Any]:
    normalized = normalize_close_confirmation_snapshot(
        snapshot,
        decision_time=decision_time,
    )
    decision = parse_cn_datetime(normalized.get("decision_time"))
    if decision is None:
        return _invalid_decision_result(normalized)

    coverage = _coverage_by_type(normalized, decision)
    source_status = _critical_source_status(normalized, coverage)
    readiness_errors: list[str] = []

    market = _market_snapshot(normalized)
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
    for stock in normalized.get("stocks") or []:
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
            "minute_bar_count": len(stock.get("intraday_bars") or []),
            "daily_bar_count": len(stock.get("daily_bars") or []),
            "daily_bar_audit": deepcopy(stock.get("daily_bar_audit") or {}),
        }
        if not errors:
            eligible_codes.append(code)

    if not eligible_codes:
        readiness_errors.append("no_scoreable_stock")
        for code, item in stock_readiness.items():
            readiness_errors.extend(
                f"{code}:{error}" for error in item["errors"]
            )

    readiness_errors = list(dict.fromkeys(readiness_errors))
    return {
        "data_ready": not readiness_errors,
        "coverage_by_type": coverage,
        "readiness_errors": readiness_errors,
        "critical_source_status": source_status,
        "eligible_stock_codes": eligible_codes,
        "stock_readiness": stock_readiness,
        "accepted_record_count": len(normalized.get("records") or []),
        "rejected_record_count": len(
            normalized.get("rejected_records") or []
        ),
        "accepted_source_status_count": len(
            normalized.get("source_status") or []
        ),
        "rejected_source_status_count": len(
            normalized.get("rejected_source_status") or []
        ),
        "snapshot_contract_version": SNAPSHOT_CONTRACT_VERSION,
        "normalized_snapshot": normalized,
    }


def normalize_close_confirmation_snapshot(
    snapshot: dict[str, Any],
    *,
    decision_time: str | datetime | None = None,
) -> dict[str, Any]:
    decision = parse_cn_datetime(
        decision_time or snapshot.get("decision_time")
    )
    result = deepcopy(snapshot)
    result["snapshot_contract_version"] = SNAPSHOT_CONTRACT_VERSION
    if decision is None:
        result["decision_time"] = str(
            decision_time or snapshot.get("decision_time") or ""
        )
        return result
    result["decision_time"] = decision.isoformat(timespec="seconds")

    accepted_records, rejected_records = records_available_at(
        result.get("records") or [],
        decision,
        require_published_at_for_news=True,
    )
    result["records"] = accepted_records
    result["rejected_records"] = [
        *(result.get("rejected_records") or []),
        *rejected_records,
    ]
    if not result.get("stocks") and accepted_records:
        result.update(materialize_point_in_time_records(accepted_records))

    accepted_sources, rejected_sources = filter_source_status_at(
        result.get("source_status") or [],
        decision,
    )
    result["source_status"] = accepted_sources
    result["rejected_source_status"] = [
        *(result.get("rejected_source_status") or []),
        *rejected_sources,
    ]

    market_records, market_rejected = filter_nested_temporal_rows(
        result.get("market_records") or [],
        decision,
        data_type="market",
    )
    industry_records, industry_rejected = filter_nested_temporal_rows(
        result.get("industry_records") or [],
        decision,
        data_type="industry",
    )
    global_news, global_news_rejected = filter_nested_temporal_rows(
        result.get("news") or [],
        decision,
        data_type="news",
        require_published_at=True,
    )
    result["market_records"] = market_records
    result["industry_records"] = industry_records
    result["news"] = global_news
    result["nested_rejections"] = [
        *(result.get("nested_rejections") or []),
        *market_rejected,
        *industry_rejected,
        *global_news_rejected,
    ]

    normalized_stocks = []
    for stock in result.get("stocks") or []:
        normalized_stocks.append(_normalize_stock(stock, decision))
    result["stocks"] = normalized_stocks
    return result


def filter_source_status_at(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decision = parse_cn_datetime(decision_time)
    if decision is None:
        return [], [
            {**dict(row), "pit_reject_reason": "decision_time_invalid"}
            for row in rows
        ]
    return filter_nested_temporal_rows(
        rows,
        decision,
        data_type="source_status",
    )


def filter_nested_temporal_rows(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
    *,
    data_type: str,
    require_published_at: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decision = parse_cn_datetime(decision_time)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in rows:
        row = deepcopy(dict(item))
        reason = (
            "decision_time_invalid"
            if decision is None
            else _nested_temporal_reject_reason(
                row,
                decision,
                require_published_at=require_published_at,
            )
        )
        if reason:
            row["pit_reject_reason"] = reason
            row["pit_data_type"] = data_type
            rejected.append(row)
        else:
            accepted.append(row)
    return accepted, rejected


def normalize_daily_bars(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    decision = parse_cn_datetime(decision_time)
    temporal_valid, rejected = filter_nested_temporal_rows(
        rows,
        decision_time,
        data_type="daily_bar",
    )
    errors: list[str] = []
    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    adjustments: set[str] = set()

    for row in temporal_valid:
        adjustment = str(row.get("adjustment") or "").strip().lower()
        adjustments.add(adjustment or "unknown")
        bar_date = _parse_bar_date(row)
        reason = ""
        if bar_date is None:
            reason = "daily_trade_date_invalid"
        elif decision is not None and bar_date >= decision.date():
            reason = "current_or_future_daily_bar_prohibited"
        elif adjustment != "qfq":
            reason = "daily_adjustment_not_qfq"
        elif not _valid_ohlcv(row):
            reason = "daily_ohlcv_invalid"
        if reason:
            rejected.append({**row, "pit_reject_reason": reason})
        else:
            candidates.append({**row, "date": bar_date.isoformat()})

    if adjustments and adjustments != {"qfq"}:
        errors.append("daily_adjustment_mixed_or_unknown")

    original_dates = [str(row.get("date") or "") for row in candidates]
    ordered = sorted(
        candidates,
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("available_at") or ""),
            str(row.get("raw_hash") or ""),
        ),
    )
    if original_dates != [str(row.get("date") or "") for row in ordered]:
        warnings.append("daily_bars_reordered")

    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        by_date.setdefault(str(row["date"]), []).append(row)
    deduplicated = []
    duplicate_dates = []
    for bar_date, date_rows in sorted(by_date.items()):
        deduplicated.append(date_rows[-1])
        if len(date_rows) > 1:
            duplicate_dates.append(bar_date)
            rejected.extend(
                {
                    **row,
                    "pit_reject_reason": "duplicate_daily_trade_date",
                }
                for row in date_rows[:-1]
            )
    if duplicate_dates:
        warnings.append("duplicate_daily_dates_deduplicated")

    audit = {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "adjustment": (
            "qfq" if adjustments == {"qfq"} else "invalid_or_mixed"
        ),
        "input_count": len(list(rows)) if isinstance(rows, list) else None,
        "normalized_count": len(deduplicated),
        "unique_trade_date_count": len(by_date),
        "duplicate_dates": duplicate_dates,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "rejected": rejected,
    }
    return deduplicated, audit


def materialize_point_in_time_records(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
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
            dict.fromkeys(
                [*(stock.get("pit_record_types") or []), data_type]
            )
        )
        temporal = _temporal_fields(row)
        if data_type == "quote":
            stock.update({**payload, **temporal})
        elif data_type == "minute_bar":
            stock["intraday_bars"].append({**payload, **temporal})
        elif data_type == "daily_bar":
            stock["daily_bars"].append({**payload, **temporal})
        elif data_type == "fund_flow":
            stock["fund_flow"].append({**payload, **temporal})
        elif data_type == "news":
            stock["news"].append({**payload, **temporal})

    market_payload = (
        {
            **deepcopy(market_records[-1].get("payload") or {}),
            **_temporal_fields(market_records[-1]),
        }
        if market_records
        else {}
    )
    industries = {
        str(
            (item.get("payload") or {}).get("name")
            or (item.get("payload") or {}).get("industry")
            or ""
        ): {
            **deepcopy(item.get("payload") or {}),
            **_temporal_fields(item),
        }
        for item in industry_records
    }
    for stock in stocks.values():
        stock.setdefault("market", market_payload)
        industry_name = str(
            stock.get("industry_name") or stock.get("industry") or ""
        )
        if isinstance(stock.get("industry"), dict):
            continue
        stock["industry"] = industries.get(industry_name, {})
    return {
        "stocks": list(stocks.values()),
        "market_records": market_records,
        "industry_records": industry_records,
        "news": global_news,
    }


def _normalize_stock(
    stock: dict[str, Any],
    decision: datetime,
) -> dict[str, Any]:
    result = deepcopy(stock)
    quote_valid, quote_rejected = filter_nested_temporal_rows(
        [stock],
        decision,
        data_type="quote",
    )
    result["quote_temporal_valid"] = bool(quote_valid)
    result["quote_temporal_rejections"] = quote_rejected

    market_valid, market_rejected = filter_nested_temporal_rows(
        [stock.get("market") or {}],
        decision,
        data_type="market",
    )
    industry_valid, industry_rejected = filter_nested_temporal_rows(
        [stock.get("industry") or {}],
        decision,
        data_type="industry",
    )
    minute_valid, minute_rejected = filter_nested_temporal_rows(
        stock.get("intraday_bars") or stock.get("minute_bars") or [],
        decision,
        data_type="minute_bar",
    )
    daily_valid, daily_audit = normalize_daily_bars(
        stock.get("daily_bars") or [],
        decision,
    )
    daily_audit = _merge_daily_audit(
        stock.get("daily_bar_audit") or {},
        daily_audit,
    )
    fund_valid, fund_rejected = filter_nested_temporal_rows(
        stock.get("fund_flow") or [],
        decision,
        data_type="fund_flow",
    )
    news_valid, news_rejected = filter_nested_temporal_rows(
        stock.get("news") or [],
        decision,
        data_type="news",
        require_published_at=True,
    )

    result["market"] = market_valid[-1] if market_valid else {}
    result["industry"] = industry_valid[-1] if industry_valid else {}
    result["intraday_bars"] = sorted(
        minute_valid,
        key=lambda row: _record_datetime(row, decision),
    )
    result.pop("minute_bars", None)
    result["daily_bars"] = daily_valid
    result["daily_bar_audit"] = daily_audit
    result["fund_flow"] = fund_valid
    result["news"] = news_valid
    result["nested_rejections"] = [
        *quote_rejected,
        *market_rejected,
        *industry_rejected,
        *minute_rejected,
        *(daily_audit.get("rejected") or []),
        *fund_rejected,
        *news_rejected,
    ]
    return result


def _merge_daily_audit(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    if not previous:
        return current
    previous_adjustment = str(previous.get("adjustment") or "")
    current_adjustment = str(current.get("adjustment") or "")
    adjustment = (
        "qfq"
        if previous_adjustment == current_adjustment == "qfq"
        else "invalid_or_mixed"
    )
    rejected_by_hash = {
        stable_hash(row): deepcopy(row)
        for row in [
            *(previous.get("rejected") or []),
            *(current.get("rejected") or []),
        ]
    }
    return {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "adjustment": adjustment,
        "input_count": previous.get(
            "input_count",
            current.get("input_count"),
        ),
        "normalized_count": current.get("normalized_count", 0),
        "unique_trade_date_count": current.get(
            "unique_trade_date_count",
            0,
        ),
        "duplicate_dates": sorted(
            set(previous.get("duplicate_dates") or [])
            | set(current.get("duplicate_dates") or [])
        ),
        "errors": list(
            dict.fromkeys(
                [
                    *(previous.get("errors") or []),
                    *(current.get("errors") or []),
                ]
            )
        ),
        "warnings": list(
            dict.fromkeys(
                [
                    *(previous.get("warnings") or []),
                    *(current.get("warnings") or []),
                ]
            )
        ),
        "rejected": list(rejected_by_hash.values()),
    }


def _stock_readiness_errors(
    stock: dict[str, Any],
    decision: datetime,
    *,
    min_minute_bars: int,
    min_daily_bars: int,
) -> list[str]:
    errors: list[str] = []
    if not stock.get("quote_temporal_valid"):
        errors.append("quote_temporal_contract_invalid")
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

    bars = stock.get("intraday_bars") or []
    if len(bars) < min_minute_bars:
        errors.append("minute_bar_count_below_minimum")
    if (
        not bars
        or _record_datetime(bars[-1], decision)
        < decision.replace(second=0, microsecond=0)
    ):
        errors.append("minute_bar_not_complete_1450")

    market = stock.get("market")
    if not isinstance(market, dict) or not market:
        errors.append("market_snapshot_missing")

    industry = (
        stock.get("industry")
        if isinstance(stock.get("industry"), dict)
        else {}
    )
    if not industry:
        errors.append("industry_snapshot_missing")
    else:
        expected_name = str(stock.get("industry_name") or "").strip()
        actual_name = str(
            industry.get("name") or industry.get("industry") or ""
        ).strip()
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

    daily_bars = stock.get("daily_bars") or []
    daily_audit = stock.get("daily_bar_audit") or {}
    if daily_audit.get("errors"):
        errors.extend(daily_audit["errors"])
    if len({str(row.get("date") or "") for row in daily_bars}) < min_daily_bars:
        errors.append("daily_bar_history_insufficient")
    if str(daily_audit.get("adjustment") or "") != "qfq":
        errors.append("daily_adjustment_not_qfq")
    if not _valid_fund_flow(stock.get("fund_flow") or []):
        errors.append("fund_flow_missing")
    return list(dict.fromkeys(errors))


def _coverage_by_type(
    snapshot: dict[str, Any],
    decision: datetime,
) -> dict[str, int]:
    stocks = snapshot.get("stocks") or []
    return {
        "market": 1 if _market_snapshot(snapshot) else 0,
        "industry": max(
            len(snapshot.get("industry_records") or []),
            sum(
                1
                for stock in stocks
                if isinstance(stock.get("industry"), dict)
                and stock.get("industry")
            ),
        ),
        "quote": sum(
            1
            for stock in stocks
            if stock.get("quote_temporal_valid")
            and _looks_like_quote(stock)
        ),
        "minute_bar": sum(
            len(stock.get("intraday_bars") or []) for stock in stocks
        ),
        "daily_bar": sum(
            len(stock.get("daily_bars") or []) for stock in stocks
        ),
        "fund_flow": sum(
            len(stock.get("fund_flow") or []) for stock in stocks
        ),
        "news": len(snapshot.get("news") or [])
        + sum(len(stock.get("news") or []) for stock in stocks),
    }


def _critical_source_status(
    snapshot: dict[str, Any],
    coverage: dict[str, int],
) -> dict[str, dict[str, Any]]:
    records = snapshot.get("records") or []
    explicit_rows = list(snapshot.get("source_status") or [])
    result: dict[str, dict[str, Any]] = {}
    for data_type in CRITICAL_DATA_TYPES:
        record_sources = sorted(
            {
                str(row.get("source") or "")
                for row in records
                if _normalized_data_type(row) == data_type
                and row.get("source")
            }
        )
        matching = [
            row
            for row in explicit_rows
            if _source_row_matches(row, data_type)
        ]
        explicit_sources = [
            str(row.get("source") or "")
            for row in matching
            if row.get("source")
        ]
        states = {_source_state(row) for row in matching}
        if coverage.get(data_type, 0) > 0:
            status = "AVAILABLE"
        elif states & SUCCESS_SOURCE_STATES:
            status = (
                "AVAILABLE_EMPTY" if data_type == "news" else "MISSING"
            )
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


def _nested_temporal_reject_reason(
    row: dict[str, Any],
    decision: datetime,
    *,
    require_published_at: bool,
) -> str:
    missing = [
        field
        for field in REQUIRED_NESTED_TEMPORAL_FIELDS
        if row.get(field) in (None, "")
    ]
    if missing:
        return "temporal_contract_missing:" + ",".join(missing)
    event = parse_cn_datetime(row.get("event_time"))
    observed = parse_cn_datetime(row.get("observed_at"))
    available = parse_cn_datetime(row.get("available_at"))
    cutoff = parse_cn_datetime(row.get("decision_cutoff"))
    if None in {event, observed, available, cutoff}:
        return "temporal_contract_invalid"
    if event > decision:
        return "event_after_decision"
    if observed > decision:
        return "observed_after_decision"
    if available > decision:
        return "available_after_decision"
    if available < observed:
        return "available_before_observed"
    if decision > cutoff:
        return "decision_after_cutoff"
    if require_published_at:
        published = parse_cn_datetime(row.get("published_at"))
        if published is None:
            return "news_published_at_missing"
        if published > decision:
            return "news_published_after_decision"
    return ""


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


def _record_datetime(
    row: dict[str, Any],
    decision: datetime,
) -> datetime | None:
    value = (
        row.get("available_at")
        or row.get("event_time")
        or row.get("datetime")
        or row.get("time")
    )
    text = str(value or "")
    if len(text) <= 8 and ":" in text:
        try:
            hour, minute, *seconds = (
                int(part) for part in text.split(":")
            )
            return decision.replace(
                hour=hour,
                minute=minute,
                second=seconds[0] if seconds else 0,
                microsecond=0,
            )
        except (TypeError, ValueError):
            return None
    return parse_cn_datetime(value)


def _parse_bar_date(row: dict[str, Any]) -> date | None:
    text = str(row.get("date") or row.get("trade_date") or "").strip()
    if text:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    event = parse_cn_datetime(row.get("event_time"))
    return event.date() if event else None


def _valid_ohlcv(row: dict[str, Any]) -> bool:
    high = _optional_number(row.get("high"))
    low = _optional_number(row.get("low"))
    close = _optional_number(row.get("close"))
    volume = _optional_number(row.get("volume", row.get("vol")))
    return bool(
        high is not None
        and low is not None
        and close is not None
        and volume is not None
        and high > 0
        and low > 0
        and close > 0
        and volume > 0
        and high >= max(low, close)
        and low <= close
    )


def _valid_fund_flow(rows: list[dict[str, Any]]) -> bool:
    return any(
        any(
            _valid_number(row.get(field))
            for field in ("main_net", "large_net", "super_net")
        )
        for row in rows
    )


def _looks_like_quote(stock: dict[str, Any]) -> bool:
    return bool(
        str(stock.get("code") or "").strip()
        and _valid_positive(stock.get("price"))
        and _valid_positive(stock.get("prev_close"))
    )


def _normalized_data_type(row: dict[str, Any]) -> str:
    payload = (
        row.get("payload")
        if isinstance(row.get("payload"), dict)
        else {}
    )
    return _canonical_data_type(
        str(row.get("data_type") or payload.get("data_type") or "")
    )


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
    return _optional_number(value) is not None


def _optional_number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return (
            parsed
            if value not in (None, "", "-") and math.isfinite(parsed)
            else None
        )
    except (TypeError, ValueError):
        return None


def _valid_positive(value: Any) -> bool:
    parsed = _optional_number(value)
    return parsed is not None and parsed > 0


def _valid_nonnegative(value: Any) -> bool:
    parsed = _optional_number(value)
    return parsed is not None and parsed >= 0


def _valid_ratio(value: Any) -> bool:
    parsed = _optional_number(value)
    return parsed is not None and 0.0 <= parsed <= 1.0


def _invalid_decision_result(
    normalized: dict[str, Any],
) -> dict[str, Any]:
    return {
        "data_ready": False,
        "coverage_by_type": {name: 0 for name in CRITICAL_DATA_TYPES},
        "readiness_errors": ["decision_time_invalid"],
        "critical_source_status": {
            name: {
                "status": "MISSING",
                "record_count": 0,
                "sources": [],
            }
            for name in CRITICAL_DATA_TYPES
        },
        "eligible_stock_codes": [],
        "stock_readiness": {},
        "accepted_record_count": 0,
        "rejected_record_count": 0,
        "accepted_source_status_count": 0,
        "rejected_source_status_count": 0,
        "snapshot_contract_version": SNAPSHOT_CONTRACT_VERSION,
        "normalized_snapshot": normalized,
    }
