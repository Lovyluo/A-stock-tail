from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import math
from typing import Any, Iterable

from overnight_quant.data.close_time_contract import (
    CloseTimeContract,
    contract_datetimes,
    normalize_close_time_contract,
)
from overnight_quant.data.point_in_time import (
    parse_cn_datetime,
    records_available_at,
    stable_hash,
)


SNAPSHOT_CONTRACT_VERSION = "close_confirmation_pit_v4"
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
    "trading_calendar",
)
RECORD_TYPE_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "trading_calendar",
            "market",
            "industry",
            "quote",
            "minute_bar",
            "daily_bar",
            "fund_flow",
            "news",
        )
    )
}
SUCCESS_SOURCE_STATES = {
    "SUCCESS",
    "READY",
    "OK",
    "AVAILABLE",
    "AVAILABLE_EMPTY",
}
FAILED_SOURCE_STATES = {
    "FAILED",
    "ERROR",
    "UNAVAILABLE",
    "TIMEOUT",
    "DEADLINE_EXCEEDED",
}


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
    time_contract = normalize_close_time_contract(
        normalized.get("time_contract"),
        fallback_decision_time=decision,
    )
    if (
        time_contract is not None
        and time_contract.contract_version
        != "legacy_single_cutoff_v1"
        and not time_contract.minute_label_verified
    ):
        readiness_errors.append("minute_label_semantics_unverified")
    calendar_contract = dict(
        normalized.get("trading_calendar_contract") or {}
    )
    if not calendar_contract.get("available"):
        readiness_errors.append("trading_calendar_missing_or_invalid")
        readiness_errors.extend(calendar_contract.get("errors") or [])

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
            time_contract=time_contract,
        )
        stock_readiness[code] = {
            "ready": not errors,
            "errors": errors,
            "minute_bar_count": len(stock.get("intraday_bars") or []),
            "minute_bar_audit": deepcopy(
                stock.get("minute_bar_audit") or {}
            ),
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
        "proxy_coverage_by_type": _proxy_coverage_by_type(
            normalized
        ),
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
        "trading_calendar_contract": calendar_contract,
        "normalized_snapshot": normalized,
    }


def normalize_point_in_time_records(
    records: Iterable[dict[str, Any]],
    decision_time: str | datetime,
    *,
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    temporal_valid, rejected = records_available_at(
        records,
        decision_time,
        require_published_at_for_news=True,
        time_contract=time_contract,
    )
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in temporal_valid:
        normalized = deepcopy(row)
        data_type = _normalized_data_type(normalized)
        groups.setdefault(
            _record_logical_key(normalized, data_type),
            [],
        ).append(normalized)

    selected: list[dict[str, Any]] = []
    duplicate_counts: dict[str, int] = {}
    for logical_key in sorted(groups, key=stable_hash):
        candidates = sorted(
            groups[logical_key],
            key=_record_winner_key,
        )
        winner = candidates[-1]
        selected.append(winner)
        if len(candidates) > 1:
            data_type = _normalized_data_type(winner)
            duplicate_counts[data_type] = (
                duplicate_counts.get(data_type, 0) + len(candidates) - 1
            )
            rejected.extend(
                {
                    **row,
                    "pit_reject_reason": f"superseded_duplicate_{data_type}",
                    "pit_duplicate_key": stable_hash(logical_key),
                }
                for row in candidates[:-1]
            )

    selected.sort(key=_record_sort_key)
    rejected.sort(key=_rejected_row_sort_key)
    input_order_changed = [
        stable_hash(row) for row in temporal_valid
    ] != [stable_hash(row) for row in selected]
    return selected, rejected, {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "input_count": len(temporal_valid) + len(
            [
                row
                for row in rejected
                if not str(row.get("pit_reject_reason") or "").startswith(
                    "superseded_duplicate_"
                )
            ]
        ),
        "normalized_count": len(selected),
        "duplicate_counts": dict(sorted(duplicate_counts.items())),
        "input_order_changed": input_order_changed,
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
    time_contract = normalize_close_time_contract(
        result.get("time_contract"),
        fallback_decision_time=decision,
    )
    if time_contract is not None:
        result["time_contract"] = time_contract.as_dict()
        if (
            time_contract.contract_version
            != "legacy_single_cutoff_v1"
        ):
            decision = parse_cn_datetime(time_contract.decision_time)
    if decision is None:
        result["decision_time"] = str(
            decision_time or snapshot.get("decision_time") or ""
        )
        return result
    result["decision_time"] = decision.isoformat(timespec="seconds")

    accepted_records, rejected_records, record_audit = (
        normalize_point_in_time_records(
            result.get("records") or [],
            decision,
            time_contract=time_contract,
        )
    )
    record_audit = _merge_record_normalization_audit(
        result.get("record_normalization_audit") or {},
        record_audit,
    )
    result["records"] = accepted_records
    result["record_normalization_audit"] = record_audit
    result["rejected_records"] = [
        *(result.get("rejected_records") or []),
        *rejected_records,
    ]
    if accepted_records:
        result.update(materialize_point_in_time_records(accepted_records))

    accepted_sources, rejected_sources = filter_source_status_at(
        result.get("source_status") or [],
        decision,
        time_contract=time_contract,
    )
    result["source_status"] = accepted_sources
    result["rejected_source_status"] = [
        *(result.get("rejected_source_status") or []),
        *rejected_sources,
    ]

    calendar_input = result.get("calendar_records") or []
    if not calendar_input and isinstance(
        result.get("trading_calendar"),
        dict,
    ):
        calendar_input = [result["trading_calendar"]]
    calendar_records, calendar_rejected = normalize_nested_temporal_rows(
        calendar_input,
        decision,
        data_type="trading_calendar",
        time_contract=time_contract,
    )
    calendar_contract = build_trading_calendar_contract(
        calendar_records,
        decision,
    )
    result["calendar_records"] = calendar_records
    result["trading_calendar_contract"] = calendar_contract

    market_records, market_rejected = normalize_nested_temporal_rows(
        result.get("market_records") or [],
        decision,
        data_type="market",
        time_contract=time_contract,
    )
    industry_records, industry_rejected = normalize_nested_temporal_rows(
        result.get("industry_records") or [],
        decision,
        data_type="industry",
        time_contract=time_contract,
    )
    global_news, global_news_rejected = normalize_nested_temporal_rows(
        result.get("news") or [],
        decision,
        data_type="news",
        require_published_at=True,
        time_contract=time_contract,
    )
    result["market_records"] = market_records
    result["industry_records"] = industry_records
    result["news"] = global_news
    result["nested_rejections"] = [
        *(result.get("nested_rejections") or []),
        *calendar_rejected,
        *market_rejected,
        *industry_rejected,
        *global_news_rejected,
    ]

    normalized_stocks = []
    for stock in result.get("stocks") or []:
        normalized_stocks.append(
            _normalize_stock(
                stock,
                decision,
                calendar_contract=calendar_contract,
                time_contract=time_contract,
            )
        )
    result["stocks"] = sorted(
        normalized_stocks,
        key=lambda stock: str(stock.get("code") or "").zfill(6),
    )
    return result


def _merge_record_normalization_audit(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    if not previous:
        return current
    duplicate_counts = dict(previous.get("duplicate_counts") or {})
    for data_type, count in (
        current.get("duplicate_counts") or {}
    ).items():
        duplicate_counts[data_type] = (
            int(duplicate_counts.get(data_type, 0)) + int(count)
        )
    return {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "input_count": previous.get(
            "input_count",
            current.get("input_count"),
        ),
        "normalized_count": current.get("normalized_count", 0),
        "duplicate_counts": dict(sorted(duplicate_counts.items())),
        "input_order_changed": bool(
            previous.get("input_order_changed")
            or current.get("input_order_changed")
        ),
    }


def filter_source_status_at(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
    *,
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decision = parse_cn_datetime(decision_time)
    if decision is None:
        return [], [
            {**dict(row), "pit_reject_reason": "decision_time_invalid"}
            for row in rows
        ]
    accepted, rejected = filter_nested_temporal_rows(
        rows,
        decision,
        data_type="source_status",
        time_contract=time_contract,
    )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in accepted:
        data_types = ",".join(
            sorted(
                {
                    _canonical_data_type(str(item))
                    for item in (
                        row.get("data_types")
                        or [row.get("data_type")]
                    )
                    if item
                }
            )
        )
        completion = _normalized_time_text(
            row.get("completed_at") or row.get("available_at")
        )
        groups.setdefault(
            (
                data_types,
                str(row.get("source") or ""),
                completion,
            ),
            [],
        ).append(row)
    normalized = []
    for logical_key in sorted(groups):
        candidates = sorted(
            groups[logical_key],
            key=lambda row: (
                _normalized_time_text(row.get("event_time")),
                _normalized_time_text(row.get("available_at")),
                str(row.get("status") or row.get("state") or ""),
                str(row.get("raw_hash") or ""),
                stable_hash(row),
            ),
        )
        normalized.append(candidates[-1])
        rejected.extend(
            {
                **row,
                "pit_reject_reason": "superseded_duplicate_source_status",
            }
            for row in candidates[:-1]
        )
    normalized.sort(key=_source_status_sort_key)
    rejected.sort(key=_rejected_row_sort_key)
    return normalized, rejected


def normalize_nested_temporal_rows(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
    *,
    data_type: str,
    require_published_at: bool = False,
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted, rejected = filter_nested_temporal_rows(
        rows,
        decision_time,
        data_type=data_type,
        require_published_at=require_published_at,
        time_contract=time_contract,
    )
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in accepted:
        groups.setdefault(
            _nested_logical_key(row, data_type),
            [],
        ).append(row)
    normalized = []
    for logical_key in sorted(groups, key=stable_hash):
        candidates = sorted(groups[logical_key], key=_nested_winner_key)
        normalized.append(candidates[-1])
        rejected.extend(
            {
                **row,
                "pit_reject_reason": f"superseded_duplicate_{data_type}",
                "pit_duplicate_key": stable_hash(logical_key),
            }
            for row in candidates[:-1]
        )
    normalized.sort(
        key=lambda row: _nested_sort_key(row, data_type)
    )
    rejected.sort(key=_rejected_row_sort_key)
    return normalized, rejected


def normalize_minute_bars(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
    *,
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_rows = list(rows)
    accepted, rejected = filter_nested_temporal_rows(
        source_rows,
        decision_time,
        data_type="minute_bar",
        time_contract=time_contract,
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in accepted:
        event_minute = _event_minute_text(row.get("event_time"))
        if not event_minute:
            rejected.append(
                {
                    **row,
                    "pit_reject_reason": "minute_event_time_invalid",
                }
            )
            continue
        groups.setdefault(event_minute, []).append(row)

    normalized = []
    duplicate_minutes = []
    for event_minute in sorted(groups):
        candidates = sorted(groups[event_minute], key=_nested_winner_key)
        winner = {
            **candidates[-1],
            "_event_minute": event_minute,
        }
        normalized.append(winner)
        if len(candidates) > 1:
            duplicate_minutes.append(event_minute)
            rejected.extend(
                {
                    **row,
                    "pit_reject_reason": "duplicate_minute_event",
                    "pit_duplicate_key": event_minute,
                }
                for row in candidates[:-1]
            )
    normalized.sort(key=lambda row: row["_event_minute"])
    rejected.sort(key=_rejected_row_sort_key)
    return normalized, {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "input_count": len(source_rows),
        "normalized_count": len(normalized),
        "unique_event_minute_count": len(groups),
        "duplicate_minutes": duplicate_minutes,
        "warnings": (
            ["duplicate_minute_events_deduplicated"]
            if duplicate_minutes
            else []
        ),
        "rejected": rejected,
    }


def filter_nested_temporal_rows(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
    *,
    data_type: str,
    require_published_at: bool = False,
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
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
                data_type=data_type,
                require_published_at=require_published_at,
                time_contract=time_contract,
            )
        )
        if reason:
            row["pit_reject_reason"] = reason
            row["pit_data_type"] = data_type
            rejected.append(row)
        else:
            accepted.append(row)
    return accepted, rejected


def build_trading_calendar_contract(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
) -> dict[str, Any]:
    decision = parse_cn_datetime(decision_time)
    source_rows = list(rows)
    errors: list[str] = []
    if decision is None:
        errors.append("trading_calendar_decision_time_invalid")
    if not source_rows:
        errors.append("trading_calendar_missing")
        return {
            "available": False,
            "errors": errors,
            "confirmed_trade_dates": [],
            "latest_completed_trade_date": "",
            "source": "",
            "source_version": "",
            "raw_hash": "",
        }

    selected = source_rows[-1]
    payload = _row_payload(selected)
    calendar_kind = str(payload.get("calendar_kind") or "").strip()
    calendar_name = str(payload.get("calendar_name") or "").strip()
    source = str(selected.get("source") or "").strip()
    source_version = str(selected.get("source_version") or "").strip()
    raw_hash = str(selected.get("raw_hash") or "").strip()
    if calendar_kind not in {
        "exchange_calendar",
        "benchmark_index_trade_dates",
    }:
        errors.append("trading_calendar_kind_invalid")
    if not calendar_name:
        errors.append("trading_calendar_name_missing")
    if not source:
        errors.append("trading_calendar_source_missing")
    if not source_version:
        errors.append("trading_calendar_source_version_missing")
    if not raw_hash:
        errors.append("trading_calendar_raw_hash_missing")

    parsed_dates = []
    invalid_dates = []
    for value in payload.get("trade_dates") or []:
        try:
            parsed_dates.append(date.fromisoformat(str(value)[:10]))
        except (TypeError, ValueError):
            invalid_dates.append(str(value))
    confirmed_dates = sorted(set(parsed_dates))
    if invalid_dates:
        errors.append("trading_calendar_dates_invalid")
    weekend_dates = [
        day.isoformat() for day in confirmed_dates if day.weekday() >= 5
    ]
    if weekend_dates:
        errors.append("trading_calendar_weekend_dates_invalid")
    if not confirmed_dates:
        errors.append("trading_calendar_dates_missing")
    completed_dates = (
        [
            day
            for day in confirmed_dates
            if decision is not None and day < decision.date()
        ]
        if decision is not None
        else []
    )
    latest_completed = max(completed_dates) if completed_dates else None
    if latest_completed is None:
        errors.append("trading_calendar_completed_date_missing")
    declared_latest = str(
        payload.get("latest_completed_trade_date") or ""
    ).strip()
    if declared_latest:
        try:
            parsed_declared_latest = date.fromisoformat(
                declared_latest[:10]
            )
        except ValueError:
            errors.append("trading_calendar_latest_completed_invalid")
        else:
            if latest_completed != parsed_declared_latest:
                errors.append("trading_calendar_latest_completed_mismatch")

    return {
        "available": not errors,
        "calendar_kind": calendar_kind,
        "calendar_name": calendar_name,
        "source": source,
        "source_version": source_version,
        "raw_hash": raw_hash,
        "confirmed_trade_dates": [
            day.isoformat() for day in confirmed_dates
        ],
        "latest_completed_trade_date": (
            latest_completed.isoformat() if latest_completed else ""
        ),
        "invalid_dates": invalid_dates,
        "weekend_dates": weekend_dates,
        "errors": list(dict.fromkeys(errors)),
    }


def normalize_daily_bars(
    rows: Iterable[dict[str, Any]],
    decision_time: str | datetime,
    *,
    calendar_contract: dict[str, Any] | None = None,
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    decision = parse_cn_datetime(decision_time)
    temporal_valid, rejected = filter_nested_temporal_rows(
        rows,
        decision_time,
        data_type="daily_bar",
        time_contract=time_contract,
    )
    errors: list[str] = []
    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    adjustments: set[str] = set()
    calendar = dict(calendar_contract or {})
    calendar_available = bool(calendar.get("available"))
    confirmed_dates = set(calendar.get("confirmed_trade_dates") or [])
    latest_completed_text = str(
        calendar.get("latest_completed_trade_date") or ""
    )
    try:
        latest_completed = (
            date.fromisoformat(latest_completed_text)
            if latest_completed_text
            else None
        )
    except ValueError:
        latest_completed = None
    if not calendar_available:
        errors.append("trading_calendar_missing_or_invalid")

    for row in temporal_valid:
        adjustment = str(row.get("adjustment") or "").strip().lower()
        adjustments.add(adjustment or "unknown")
        bar_date = _parse_bar_date(row)
        reason = ""
        if bar_date is None:
            reason = "daily_trade_date_invalid"
        elif decision is not None and bar_date >= decision.date():
            reason = "current_or_future_daily_bar_prohibited"
        elif not calendar_available:
            reason = "daily_trading_calendar_unavailable"
        elif bar_date.isoformat() not in confirmed_dates:
            reason = "daily_date_not_confirmed_open"
        elif latest_completed is None or bar_date > latest_completed:
            reason = "daily_after_latest_completed_trade_date"
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
        "calendar_available": calendar_available,
        "calendar_source": str(calendar.get("source") or ""),
        "calendar_source_version": str(
            calendar.get("source_version") or ""
        ),
        "latest_completed_trade_date": latest_completed_text,
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
    calendar_records: list[dict[str, Any]] = []
    for record in records:
        row = deepcopy(record)
        payload = deepcopy(record.get("payload") or {})
        data_type = _normalized_data_type(record)
        code = str(payload.get("code") or record.get("code") or "").zfill(6)
        if data_type == "trading_calendar":
            calendar_records.append(row)
            continue
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
        "stocks": sorted(
            stocks.values(),
            key=lambda stock: str(stock.get("code") or "").zfill(6),
        ),
        "market_records": market_records,
        "industry_records": industry_records,
        "news": global_news,
        "calendar_records": calendar_records,
    }


def _normalize_stock(
    stock: dict[str, Any],
    decision: datetime,
    *,
    calendar_contract: dict[str, Any],
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
) -> dict[str, Any]:
    result = deepcopy(stock)
    quote_valid, quote_rejected = filter_nested_temporal_rows(
        [stock],
        decision,
        data_type="quote",
        time_contract=time_contract,
    )
    result["quote_temporal_valid"] = bool(quote_valid)
    result["quote_temporal_rejections"] = quote_rejected

    market_valid, market_rejected = normalize_nested_temporal_rows(
        [stock.get("market") or {}],
        decision,
        data_type="market",
        time_contract=time_contract,
    )
    industry_valid, industry_rejected = normalize_nested_temporal_rows(
        [stock.get("industry") or {}],
        decision,
        data_type="industry",
        time_contract=time_contract,
    )
    minute_valid, minute_audit = normalize_minute_bars(
        stock.get("intraday_bars") or stock.get("minute_bars") or [],
        decision,
        time_contract=time_contract,
    )
    minute_audit = _merge_minute_audit(
        stock.get("minute_bar_audit") or {},
        minute_audit,
    )
    daily_valid, daily_audit = normalize_daily_bars(
        stock.get("daily_bars") or [],
        decision,
        calendar_contract=calendar_contract,
        time_contract=time_contract,
    )
    daily_audit = _merge_daily_audit(
        stock.get("daily_bar_audit") or {},
        daily_audit,
    )
    fund_valid, fund_rejected = normalize_nested_temporal_rows(
        stock.get("fund_flow") or [],
        decision,
        data_type="fund_flow",
        time_contract=time_contract,
    )
    news_valid, news_rejected = normalize_nested_temporal_rows(
        stock.get("news") or [],
        decision,
        data_type="news",
        require_published_at=True,
        time_contract=time_contract,
    )

    result["market"] = market_valid[-1] if market_valid else {}
    result["industry"] = industry_valid[-1] if industry_valid else {}
    result["intraday_bars"] = minute_valid
    result["minute_bar_audit"] = minute_audit
    result.pop("minute_bars", None)
    result["daily_bars"] = daily_valid
    result["daily_bar_audit"] = daily_audit
    result["trading_calendar_contract"] = deepcopy(calendar_contract)
    result["fund_flow"] = fund_valid
    result["news"] = news_valid
    result["nested_rejections"] = [
        *quote_rejected,
        *market_rejected,
        *industry_rejected,
        *(minute_audit.get("rejected") or []),
        *(daily_audit.get("rejected") or []),
        *fund_rejected,
        *news_rejected,
    ]
    return result


def _merge_minute_audit(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    if not previous:
        return current
    rejected_by_hash = {
        stable_hash(row): deepcopy(row)
        for row in [
            *(previous.get("rejected") or []),
            *(current.get("rejected") or []),
        ]
    }
    return {
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "input_count": previous.get(
            "input_count",
            current.get("input_count"),
        ),
        "normalized_count": current.get("normalized_count", 0),
        "unique_event_minute_count": current.get(
            "unique_event_minute_count",
            0,
        ),
        "duplicate_minutes": sorted(
            set(previous.get("duplicate_minutes") or [])
            | set(current.get("duplicate_minutes") or [])
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
        "calendar_available": bool(
            previous.get("calendar_available")
            and current.get("calendar_available")
        ),
        "calendar_source": str(
            previous.get("calendar_source")
            or current.get("calendar_source")
            or ""
        ),
        "calendar_source_version": str(
            previous.get("calendar_source_version")
            or current.get("calendar_source_version")
            or ""
        ),
        "latest_completed_trade_date": str(
            previous.get("latest_completed_trade_date")
            or current.get("latest_completed_trade_date")
            or ""
        ),
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
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
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
    minute_audit = stock.get("minute_bar_audit") or {}
    unique_event_minutes = int(
        minute_audit.get("unique_event_minute_count") or len(bars)
    )
    if unique_event_minutes < min_minute_bars:
        errors.append("minute_bar_count_below_minimum")
    contract = normalize_close_time_contract(
        time_contract,
        fallback_decision_time=decision,
    )
    contract_times = contract_datetimes(contract) if contract else {}
    feature_cutoff = contract_times.get(
        "feature_event_cutoff",
        decision,
    )
    decision_minute = feature_cutoff.replace(second=0, microsecond=0)
    event_minutes = {
        parse_cn_datetime(row.get("event_time")).replace(
            second=0,
            microsecond=0,
        )
        for row in bars
        if parse_cn_datetime(row.get("event_time")) is not None
    }
    if decision_minute not in event_minutes:
        errors.append("minute_bar_1450_event_missing")

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
    if not daily_audit.get("calendar_available"):
        errors.append("daily_trading_calendar_unavailable")
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
            sum(
                1
                for row in stock.get("fund_flow") or []
                if _eligible_fund_flow_row(row)
            )
            for stock in stocks
        ),
        "news": len(snapshot.get("news") or [])
        + sum(len(stock.get("news") or []) for stock in stocks),
        "trading_calendar": (
            1
            if (snapshot.get("trading_calendar_contract") or {}).get(
                "available"
            )
            else 0
        ),
    }


def _proxy_coverage_by_type(
    snapshot: dict[str, Any],
) -> dict[str, int]:
    stocks = snapshot.get("stocks") or []
    return {
        "fund_flow": sum(
            sum(
                1
                for row in stock.get("fund_flow") or []
                if not _eligible_fund_flow_row(row)
            )
            for stock in stocks
        )
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
    data_type: str,
    require_published_at: bool,
    time_contract: dict[str, Any] | CloseTimeContract | None = None,
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
    contract = normalize_close_time_contract(
        time_contract,
        fallback_decision_time=decision,
    )
    contract_times = contract_datetimes(contract) if contract else {}
    split_contract = bool(
        contract
        and contract.contract_version != "legacy_single_cutoff_v1"
    )
    feature_cutoff = contract_times.get(
        "feature_event_cutoff",
        decision,
    )
    collection_deadline = contract_times.get(
        "collection_deadline",
        decision,
    )
    event_deadline = (
        collection_deadline
        if data_type == "source_status"
        else feature_cutoff
    )
    if event > event_deadline:
        return (
            "event_after_collection_deadline"
            if split_contract and data_type == "source_status"
            else (
                "event_after_feature_cutoff"
                if split_contract
                else "event_after_decision"
            )
        )
    if observed > collection_deadline:
        return (
            "observed_after_collection_deadline"
            if split_contract
            else "observed_after_decision"
        )
    if available > collection_deadline:
        return (
            "available_after_collection_deadline"
            if split_contract
            else "available_after_decision"
        )
    if available < observed:
        return "available_before_observed"
    if decision > cutoff:
        return "decision_after_cutoff"
    if require_published_at:
        published = parse_cn_datetime(row.get("published_at"))
        if published is None:
            return "news_published_at_missing"
        if published > feature_cutoff:
            return (
                "news_published_after_feature_cutoff"
                if split_contract
                else "news_published_after_decision"
            )
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


def _record_logical_key(
    row: dict[str, Any],
    data_type: str,
) -> tuple[Any, ...]:
    payload = _row_payload(row)
    code = str(payload.get("code") or row.get("code") or "").zfill(6)
    if data_type == "quote":
        return data_type, code
    if data_type == "market":
        return data_type, "a_share_market"
    if data_type == "industry":
        return data_type, _industry_name(row)
    if data_type == "minute_bar":
        return data_type, code, _event_minute_text(row.get("event_time"))
    if data_type == "daily_bar":
        return data_type, code, _daily_date_text(row)
    if data_type == "fund_flow":
        return (
            data_type,
            code,
            _normalized_time_text(row.get("event_time")),
            str(row.get("source") or ""),
        )
    if data_type == "news":
        return (
            data_type,
            _normalized_time_text(row.get("published_at")),
            _normalized_time_text(row.get("available_at")),
            str(row.get("source") or ""),
            str(row.get("raw_hash") or ""),
        )
    if data_type == "trading_calendar":
        return data_type, "confirmed_a_share_calendar"
    return data_type, stable_hash(row)


def _record_winner_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        _normalized_time_text(row.get("event_time")),
        _normalized_time_text(row.get("available_at")),
        str(row.get("source") or ""),
        str(row.get("raw_hash") or ""),
        stable_hash(_row_payload(row)),
    )


def _record_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    data_type = _normalized_data_type(row)
    payload = _row_payload(row)
    code = str(payload.get("code") or row.get("code") or "").zfill(6)
    primary = {
        "quote": (code,),
        "market": ("a_share_market",),
        "industry": (_industry_name(row),),
        "minute_bar": (
            code,
            _event_minute_text(row.get("event_time")),
        ),
        "daily_bar": (code, _daily_date_text(row)),
        "fund_flow": (
            code,
            _normalized_time_text(row.get("event_time")),
        ),
        "news": (
            _normalized_time_text(row.get("published_at")),
            _normalized_time_text(row.get("available_at")),
        ),
        "trading_calendar": ("confirmed_a_share_calendar",),
    }.get(data_type, (stable_hash(row),))
    return (
        RECORD_TYPE_ORDER.get(data_type, len(RECORD_TYPE_ORDER)),
        *primary,
        str(row.get("source") or ""),
        str(row.get("raw_hash") or ""),
        stable_hash(row),
    )


def _nested_logical_key(
    row: dict[str, Any],
    data_type: str,
) -> tuple[Any, ...]:
    code = str(_row_payload(row).get("code") or row.get("code") or "").zfill(6)
    if data_type == "market":
        return data_type, "a_share_market"
    if data_type == "industry":
        return data_type, _industry_name(row)
    if data_type == "fund_flow":
        return (
            data_type,
            code,
            _normalized_time_text(row.get("event_time")),
            str(row.get("source") or ""),
        )
    if data_type == "news":
        return (
            data_type,
            _normalized_time_text(row.get("published_at")),
            _normalized_time_text(row.get("available_at")),
            str(row.get("source") or ""),
            str(row.get("raw_hash") or ""),
        )
    if data_type == "trading_calendar":
        return data_type, "confirmed_a_share_calendar"
    return data_type, code, stable_hash(row)


def _nested_winner_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        _normalized_time_text(row.get("event_time")),
        _normalized_time_text(row.get("available_at")),
        str(row.get("source") or ""),
        str(row.get("raw_hash") or ""),
        stable_hash(_row_payload(row)),
    )


def _nested_sort_key(
    row: dict[str, Any],
    data_type: str,
) -> tuple[str, ...]:
    code = str(_row_payload(row).get("code") or row.get("code") or "").zfill(6)
    if data_type == "industry":
        primary = _industry_name(row)
    elif data_type == "news":
        primary = _normalized_time_text(row.get("published_at"))
    else:
        primary = _normalized_time_text(row.get("event_time"))
    return (
        primary,
        code,
        _normalized_time_text(row.get("available_at")),
        str(row.get("source") or ""),
        str(row.get("raw_hash") or ""),
        stable_hash(row),
    )


def _source_status_sort_key(row: dict[str, Any]) -> tuple[str, ...]:
    data_types = ",".join(
        sorted(
            {
                _canonical_data_type(str(item))
                for item in (
                    row.get("data_types")
                    or [row.get("data_type")]
                )
                if item
            }
        )
    )
    return (
        data_types,
        str(row.get("source") or ""),
        _normalized_time_text(
            row.get("completed_at") or row.get("available_at")
        ),
        _normalized_time_text(row.get("event_time")),
        str(row.get("raw_hash") or ""),
        stable_hash(row),
    )


def _rejected_row_sort_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("pit_reject_reason") or ""),
        str(row.get("data_type") or row.get("pit_data_type") or ""),
        _normalized_time_text(row.get("event_time")),
        str(row.get("source") or ""),
        str(row.get("raw_hash") or ""),
        stable_hash(row),
    )


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else row


def _industry_name(row: dict[str, Any]) -> str:
    payload = _row_payload(row)
    return str(
        payload.get("name")
        or payload.get("industry")
        or payload.get("industry_name")
        or ""
    ).strip()


def _daily_date_text(row: dict[str, Any]) -> str:
    payload = _row_payload(row)
    return str(
        payload.get("date")
        or payload.get("trade_date")
        or ""
    )[:10]


def _normalized_time_text(value: Any) -> str:
    parsed = parse_cn_datetime(value)
    return parsed.isoformat(timespec="seconds") if parsed else ""


def _event_minute_text(value: Any) -> str:
    parsed = parse_cn_datetime(value)
    return (
        parsed.replace(second=0, microsecond=0).isoformat(timespec="minutes")
        if parsed
        else ""
    )


def _record_datetime(
    row: dict[str, Any],
    decision: datetime,
) -> datetime | None:
    value = (
        row.get("event_time")
        or row.get("available_at")
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
        _eligible_fund_flow_row(row)
        and any(
            _valid_number(row.get(field))
            for field in ("main_net", "large_net", "super_net")
        )
        for row in rows
    )


def _eligible_fund_flow_row(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").lower()
    if bool(row.get("is_proxy")) or "sina_money_flow" in source:
        return False
    return row.get("eligible_for_hard_gate") is not False


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
        "calendar": "trading_calendar",
        "trade_calendar": "trading_calendar",
        "benchmark_trade_dates": "trading_calendar",
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
            "feature_event_cutoff",
            "collection_deadline",
            "decision_time",
            "execution_not_before",
            "time_contract_version",
            "minute_label_semantics",
            "minute_label_validation_status",
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
