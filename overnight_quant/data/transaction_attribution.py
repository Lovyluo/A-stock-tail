from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable

from overnight_quant.data.close_time_contract import (
    MINUTE_LABEL_END_PROVISIONAL,
    MINUTE_LABEL_START_PROVISIONAL,
    build_close_time_contract,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import (
    parse_cn_datetime,
    stable_hash,
)


ATTRIBUTION_INCONCLUSIVE = "INCONCLUSIVE"
MINIMUM_STABLE_OBSERVATIONS = 3
MOOTDX_ATTRIBUTION_ALGORITHM_VERSION = (
    "mootdx_minute_transaction_attribution_v2"
)
MOOTDX_VOLUME_NORMALIZATION_VERSION = (
    "a_share_lot_to_share_v1"
)
MOOTDX_TRANSACTION_RAW_VOLUME_UNIT = "lot"
MOOTDX_MINUTE_BAR_VOLUME_UNIT = "share"
MOOTDX_LOT_TO_SHARE_FACTOR = 100.0
MOOTDX_LOT_TO_SHARE_BASIS = "A_share_round_lot_100_shares"


def attribute_mootdx_minute_intervals(
    probe_result: dict[str, Any],
    transaction_evidence: dict[str, Any],
) -> dict[str, Any]:
    source = _require_source(probe_result.get("source"))
    algorithm_version = str(
        transaction_evidence.get("attribution_algorithm_version")
        or ""
    )
    minute_hash = _require_hash(
        probe_result.get("probe_evidence_hash"),
        "minute_probe_evidence_hash_invalid",
    )
    transaction_hash = compute_transaction_evidence_hash(
        transaction_evidence,
        source=source,
    )
    stored_transaction_hash = str(
        transaction_evidence.get("transaction_evidence_hash") or ""
    )
    if transaction_hash != stored_transaction_hash:
        return _inconclusive_attribution(
            source,
            minute_hash,
            stored_transaction_hash,
            ["transaction_evidence_hash_drift"],
            algorithm_version=algorithm_version,
        )

    samples = sorted(
        probe_result.get("samples") or [],
        key=lambda item: str(item.get("target_at") or ""),
    )
    codes = sorted(
        str(code).zfill(6)
        for code in (probe_result.get("tracked_codes") or [])
    )
    evidence_by_code = transaction_evidence.get("by_code") or {}
    per_stock = {}
    for code in codes:
        final_bar = _finalized_bar(samples, code)
        transaction_rows = dict(evidence_by_code.get(code) or {})
        per_stock[code] = _attribute_stock(
            code,
            final_bar,
            transaction_rows,
            transaction_hash=stored_transaction_hash,
            algorithm_version=algorithm_version,
        )

    statuses = {
        str(row.get("status") or ATTRIBUTION_INCONCLUSIVE)
        for row in per_stock.values()
    }
    all_final = bool(per_stock) and all(
        row.get("is_final") is True
        for row in per_stock.values()
    )
    if (
        len(statuses) == 1
        and next(iter(statuses))
        in {
            MINUTE_LABEL_END_PROVISIONAL,
            MINUTE_LABEL_START_PROVISIONAL,
        }
        and all_final
    ):
        status = next(iter(statuses))
        reasons = ["all_stocks_same_interval_attribution"]
    else:
        status = ATTRIBUTION_INCONCLUSIVE
        reasons = ["stock_attribution_not_consistent"]

    attribution = {
        "status": status,
        "source": source,
        "minute_probe_evidence_hash": minute_hash,
        "transaction_evidence_hash": stored_transaction_hash,
        "per_stock": dict(sorted(per_stock.items())),
        "all_stocks_final": all_final,
        "reasons": reasons,
    }
    if algorithm_version:
        attribution["attribution_algorithm_version"] = (
            algorithm_version
        )
    combined_hash = compute_combined_evidence_hash(
        attribution,
        source=source,
    )
    attribution["combined_evidence_hash"] = combined_hash
    if status != ATTRIBUTION_INCONCLUSIVE:
        attribution["provisional_time_contract"] = (
            _build_provisional_contract(
                probe_result,
                attribution,
            )
        )
    else:
        attribution["provisional_time_contract"] = {}
    return attribution


def normalize_mootdx_transaction_evidence(
    transaction_evidence: dict[str, Any],
) -> dict[str, Any]:
    normalized = deepcopy(transaction_evidence)
    if _require_source(normalized.get("source")) != "mootdx":
        raise ValueError("transaction_evidence_source_not_mootdx")
    normalized.pop("transaction_evidence_hash", None)
    normalized["attribution_algorithm_version"] = (
        MOOTDX_ATTRIBUTION_ALGORITHM_VERSION
    )
    normalized["volume_normalization_version"] = (
        MOOTDX_VOLUME_NORMALIZATION_VERSION
    )
    normalized["raw_volume_unit"] = (
        MOOTDX_TRANSACTION_RAW_VOLUME_UNIT
    )
    normalized["normalized_volume_unit"] = (
        MOOTDX_MINUTE_BAR_VOLUME_UNIT
    )
    normalized["volume_conversion_factor"] = (
        MOOTDX_LOT_TO_SHARE_FACTOR
    )
    normalized["volume_conversion_basis"] = (
        MOOTDX_LOT_TO_SHARE_BASIS
    )
    by_code = {}
    for code, value in sorted(
        (normalized.get("by_code") or {}).items()
    ):
        stock = dict(value or {})
        records = []
        for source_position, value_row in enumerate(
            stock.get("records") or []
        ):
            row = dict(value_row)
            event = parse_cn_datetime(row.get("event_time"))
            precision = str(
                row.get("timestamp_precision") or "unknown"
            )
            source_time = str(row.get("source_time_text") or "")
            source_time_origin = str(
                row.get("source_time_origin") or "source"
            )
            if not source_time and event is not None:
                source_time = event.strftime(
                    "%H:%M" if precision == "minute" else "%H:%M:%S"
                )
                source_time_origin = (
                    "derived_from_event_time_for_legacy_replay"
                )
            raw_volume = row.get("raw_volume", row.get("volume"))
            if not _finite(raw_volume):
                raw_volume = None
            normalized_volume = (
                float(raw_volume) * MOOTDX_LOT_TO_SHARE_FACTOR
                if raw_volume is not None
                else None
            )
            row.update(
                {
                    "source_time_text": source_time,
                    "source_time_origin": source_time_origin,
                    "source_position": int(
                        row.get("source_position", source_position)
                    ),
                    "raw_volume": raw_volume,
                    "raw_volume_unit": (
                        MOOTDX_TRANSACTION_RAW_VOLUME_UNIT
                    ),
                    "normalized_volume": normalized_volume,
                    "normalized_volume_unit": (
                        MOOTDX_MINUTE_BAR_VOLUME_UNIT
                    ),
                    "volume_conversion_factor": (
                        MOOTDX_LOT_TO_SHARE_FACTOR
                    ),
                    "volume_conversion_basis": (
                        MOOTDX_LOT_TO_SHARE_BASIS
                    ),
                }
            )
            records.append(row)
        records.sort(
            key=lambda row: (
                str(row.get("event_time") or ""),
                int(row.get("source_position") or 0),
            )
        )
        precision = _records_precision(records)
        stock.update(
            {
                "source_timestamp_precision": str(
                    stock.get("source_timestamp_precision")
                    or stock.get("timestamp_precision")
                    or ""
                ),
                "timestamp_precision": precision,
                "source_volume_unit": str(
                    stock.get("source_volume_unit")
                    or stock.get("volume_unit")
                    or ""
                ),
                "volume_unit": MOOTDX_TRANSACTION_RAW_VOLUME_UNIT,
                "raw_volume_unit": (
                    MOOTDX_TRANSACTION_RAW_VOLUME_UNIT
                ),
                "normalized_volume_unit": (
                    MOOTDX_MINUTE_BAR_VOLUME_UNIT
                ),
                "volume_conversion_factor": (
                    MOOTDX_LOT_TO_SHARE_FACTOR
                ),
                "volume_conversion_basis": (
                    MOOTDX_LOT_TO_SHARE_BASIS
                ),
                "volume_normalization_version": (
                    MOOTDX_VOLUME_NORMALIZATION_VERSION
                ),
                "records": records,
                "coverage_complete": bool(
                    stock.get("coverage_complete")
                )
                and _records_cover_intervals(records, precision),
            }
        )
        by_code[str(code).zfill(6)] = stock
    normalized["by_code"] = by_code
    normalized["transaction_evidence_hash"] = (
        compute_transaction_evidence_hash(
            normalized,
            source="mootdx",
        )
    )
    return normalized


def build_mootdx_probe_reanalysis(
    probe_result: dict[str, Any],
) -> dict[str, Any]:
    source = _require_source(probe_result.get("source"))
    if source != "mootdx":
        raise ValueError("reanalysis_source_not_mootdx")
    normalized_transaction = normalize_mootdx_transaction_evidence(
        dict(probe_result.get("transaction_evidence") or {})
    )
    attribution = attribute_mootdx_minute_intervals(
        probe_result,
        normalized_transaction,
    )
    provisional = attribution.get("status") in {
        MINUTE_LABEL_END_PROVISIONAL,
        MINUTE_LABEL_START_PROVISIONAL,
    }
    result = {
        "status": (
            "PM_REVIEW_REQUIRED"
            if provisional
            else "REANALYSIS_INCONCLUSIVE"
        ),
        "execution_ok": True,
        "data_ready": False,
        "source": source,
        "source_role": "qualification_candidate",
        "trade_date": str(probe_result.get("trade_date") or "")[:10],
        "reanalysis_version": MOOTDX_ATTRIBUTION_ALGORITHM_VERSION,
        "original_probe_status": str(probe_result.get("status") or ""),
        "probe_evidence_hash": str(
            probe_result.get("probe_evidence_hash") or ""
        ),
        "tracked_codes": sorted(
            str(code).zfill(6)
            for code in (probe_result.get("tracked_codes") or [])
        ),
        "samples": deepcopy(probe_result.get("samples") or []),
        "transaction_evidence": normalized_transaction,
        "transaction_evidence_hash": normalized_transaction[
            "transaction_evidence_hash"
        ],
        "transaction_attribution": attribution,
        "combined_evidence_hash": str(
            attribution.get("combined_evidence_hash") or ""
        ),
        "minute_label_semantics": str(
            attribution.get("status") or ATTRIBUTION_INCONCLUSIVE
        ),
        "minute_label_validation_status": (
            "PM_REVIEW_REQUIRED"
            if provisional
            else "INCONCLUSIVE"
        ),
        "automatic_qualification_update": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    result["reanalysis_evidence_hash"] = stable_hash(result)
    return result


def compute_transaction_evidence_hash(
    transaction_evidence: dict[str, Any],
    *,
    source: str,
) -> str:
    normalized_source = _require_source(source)
    payload_source = str(
        transaction_evidence.get("source") or ""
    ).strip().lower()
    if payload_source != normalized_source:
        raise ValueError(
            "transaction_evidence_source_mismatch:"
            f"{payload_source or '<empty>'}:{normalized_source}"
        )
    canonical = {
        key: value
        for key, value in transaction_evidence.items()
        if key != "transaction_evidence_hash"
    }
    return stable_hash(
        {
            "source": normalized_source,
            "transaction_evidence": canonical,
        }
    )


def compute_combined_evidence_hash(
    attribution: dict[str, Any],
    *,
    source: str,
) -> str:
    normalized_source = _require_source(source)
    minute_hash = _require_hash(
        attribution.get("minute_probe_evidence_hash"),
        "minute_probe_evidence_hash_invalid",
    )
    transaction_hash = _require_hash(
        attribution.get("transaction_evidence_hash"),
        "transaction_evidence_hash_invalid",
    )
    canonical = {
        key: value
        for key, value in attribution.items()
        if key
        not in {
            "combined_evidence_hash",
            "provisional_time_contract",
        }
    }
    return stable_hash(
        {
            "source": normalized_source,
            "minute_probe_evidence_hash": minute_hash,
            "transaction_evidence_hash": transaction_hash,
            "attribution": canonical,
        }
    )


def _attribute_stock(
    code: str,
    final_bar: dict[str, Any],
    transaction_rows: dict[str, Any],
    *,
    transaction_hash: str,
    algorithm_version: str,
) -> dict[str, Any]:
    base = {
        "code": code,
        "bar_label_time": final_bar.get("bar_label_time", ""),
        "first_observed_at": final_bar.get(
            "first_observed_at",
            "",
        ),
        "finalized_at": final_bar.get("finalized_at", ""),
        "is_final": bool(final_bar.get("is_final")),
        "finalization_delay_ms": 0.0,
        "transaction_evidence_hash": transaction_hash,
    }
    errors = []
    normalized_contract = (
        algorithm_version == MOOTDX_ATTRIBUTION_ALGORITHM_VERSION
    )
    if algorithm_version and not normalized_contract:
        errors.append("transaction_attribution_version_unsupported")
    if not final_bar.get("is_final"):
        errors.append("minute_bar_not_final")
    if not transaction_rows.get("coverage_complete"):
        errors.append("transaction_records_incomplete")
    timestamp_precision = str(
        transaction_rows.get("timestamp_precision") or "unknown"
    )
    if normalized_contract:
        if timestamp_precision not in {"minute", "second"}:
            errors.append("transaction_timestamp_precision_unknown_or_mixed")
        if not _volume_contract_valid(transaction_rows):
            errors.append("transaction_volume_conversion_contract_invalid")
    else:
        if timestamp_precision != "second":
            errors.append("transaction_timestamp_precision_insufficient")
        final_volume_unit = str(final_bar.get("volume_unit") or "")
        transaction_volume_unit = str(
            transaction_rows.get("volume_unit") or ""
        )
        if (
            not final_volume_unit
            or final_volume_unit != transaction_volume_unit
        ):
            errors.append("transaction_volume_unit_mismatch")
    if transaction_rows.get("error"):
        errors.append("transaction_source_failed")

    records = transaction_rows.get("records") or []
    day = str(final_bar.get("bar_label_time") or "")[:10]
    if len(day) == 10:
        aggregate_1449 = aggregate_transaction_interval(
            records,
            interval_start=f"{day}T14:49:00+08:00",
            interval_end=f"{day}T14:49:59+08:00",
            normalized_contract=normalized_contract,
        )
        aggregate_1450 = aggregate_transaction_interval(
            records,
            interval_start=f"{day}T14:50:00+08:00",
            interval_end=f"{day}T14:50:59+08:00",
            normalized_contract=normalized_contract,
        )
    else:
        aggregate_1449 = _empty_aggregate()
        aggregate_1450 = _empty_aggregate()
    if not aggregate_1449.get("complete"):
        errors.append("transaction_1449_interval_incomplete")
    if not aggregate_1450.get("complete"):
        errors.append("transaction_1450_interval_incomplete")

    stable_ohlcv = final_bar.get("ohlcv") or {}
    stable_bar = dict(stable_ohlcv)
    if normalized_contract:
        stable_bar.update(_normalized_minute_bar(final_bar))
        if not stable_bar.get("volume_contract_valid"):
            errors.append("minute_bar_volume_unit_invalid")
    trade_count_comparison = "minute_bar_field_unavailable_audit_only"
    if _finite(stable_ohlcv.get("trade_count")):
        trade_count_comparison = "compared_with_minute_bar"
    match_1449 = not errors and _aggregate_matches_bar(
        aggregate_1449,
        stable_bar,
        normalized_contract=normalized_contract,
    )
    match_1450 = not errors and _aggregate_matches_bar(
        aggregate_1450,
        stable_bar,
        normalized_contract=normalized_contract,
    )
    if match_1449 and not match_1450:
        status = MINUTE_LABEL_END_PROVISIONAL
        selected = aggregate_1449
    elif match_1450 and not match_1449:
        status = MINUTE_LABEL_START_PROVISIONAL
        selected = aggregate_1450
    else:
        status = ATTRIBUTION_INCONCLUSIVE
        selected = {}
        if not errors:
            errors.append("transaction_intervals_both_or_neither_match")

    interval_end = parse_cn_datetime(selected.get("interval_end"))
    finalized_at = parse_cn_datetime(final_bar.get("finalized_at"))
    finalization_delay_ms = 0.0
    if interval_end is not None and finalized_at is not None:
        finalization_delay_ms = max(
            0.0,
            round(
                (finalized_at - interval_end).total_seconds()
                * 1000,
                3,
            ),
        )
    return {
        **base,
        "status": status,
        "interval_start": selected.get("interval_start", ""),
        "interval_end": selected.get("interval_end", ""),
        "finalization_delay_ms": finalization_delay_ms,
        "stable_bar": stable_bar,
        "aggregate_1449": aggregate_1449,
        "aggregate_1450": aggregate_1450,
        "match_1449": match_1449,
        "match_1450": match_1450,
        "trade_count_comparison": trade_count_comparison,
        "reasons": sorted(set(errors)),
    }


def aggregate_transaction_interval(
    records: Iterable[dict[str, Any]],
    *,
    interval_start: str,
    interval_end: str,
    normalized_contract: bool = False,
) -> dict[str, Any]:
    start = parse_cn_datetime(interval_start)
    end = parse_cn_datetime(interval_end)
    if start is None or end is None:
        raise ValueError("transaction_interval_invalid")
    boundary_aligned = (
        start.date() == end.date()
        and start.hour == end.hour
        and start.minute == end.minute
        and start.second == 0
        and end.second == 59
    )
    selected = []
    for row in records:
        event = parse_cn_datetime(row.get("event_time"))
        if event is not None and start <= event <= end:
            selected.append(dict(row))
    selected.sort(
        key=lambda row: (
            str(row.get("event_time") or ""),
            int(row.get("source_position") or 0),
        )
    )
    if not selected:
        return {
            "interval_start": start.isoformat(timespec="seconds"),
            "interval_end": end.isoformat(timespec="seconds"),
            "complete": False,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": None,
            "trade_count": None,
            "row_count": 0,
            "boundary_aligned": boundary_aligned,
        }
    prices = [float(row["price"]) for row in selected]
    raw_volume = sum(
        float(row.get("raw_volume", row.get("volume")))
        for row in selected
    )
    normalized_volume = sum(
        float(
            row.get(
                "normalized_volume",
                row.get("volume"),
            )
        )
        for row in selected
    )
    precisions = {
        str(row.get("timestamp_precision") or "unknown")
        for row in selected
    }
    precision_valid = precisions in ({"minute"}, {"second"})
    if precisions == {"minute"}:
        precision_valid = precision_valid and all(
            _minute_source_time_matches_event(row)
            for row in selected
        )
    return {
        "interval_start": start.isoformat(timespec="seconds"),
        "interval_end": end.isoformat(timespec="seconds"),
        "complete": boundary_aligned and precision_valid and all(
            _finite(row.get(field))
            for row in selected
            for field in ("price", "volume", "trade_count")
        ) and (
            not normalized_contract
            or all(
                _record_volume_contract_valid(row)
                for row in selected
            )
        ),
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
        "volume": raw_volume,
        "raw_volume": raw_volume,
        "raw_volume_unit": (
            MOOTDX_TRANSACTION_RAW_VOLUME_UNIT
            if normalized_contract
            else ""
        ),
        "normalized_volume": normalized_volume,
        "normalized_volume_unit": (
            MOOTDX_MINUTE_BAR_VOLUME_UNIT
            if normalized_contract
            else ""
        ),
        "volume_conversion_factor": (
            MOOTDX_LOT_TO_SHARE_FACTOR
            if normalized_contract
            else 1.0
        ),
        "volume_conversion_basis": (
            MOOTDX_LOT_TO_SHARE_BASIS
            if normalized_contract
            else ""
        ),
        "trade_count": sum(
            int(row["trade_count"]) for row in selected
        ),
        "row_count": len(selected),
        "timestamp_precision": (
            next(iter(precisions)) if len(precisions) == 1 else "mixed"
        ),
        "boundary_aligned": boundary_aligned,
    }


def _records_precision(records: Iterable[dict[str, Any]]) -> str:
    precisions = {
        str(row.get("timestamp_precision") or "unknown")
        for row in records
    }
    if precisions == {"minute"}:
        return "minute"
    if precisions == {"second"}:
        return "second"
    if not precisions or precisions == {"unknown"}:
        return "unknown"
    return "mixed"


def _minute_source_time_matches_event(row: dict[str, Any]) -> bool:
    source_time = str(row.get("source_time_text") or "")
    if (
        len(source_time) != 5
        or source_time[2] != ":"
        or not source_time[:2].isdigit()
        or not source_time[3:].isdigit()
    ):
        return False
    hour = int(source_time[:2])
    minute = int(source_time[3:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return False
    event = parse_cn_datetime(row.get("event_time"))
    return bool(
        event is not None
        and event.second == 0
        and event.hour == hour
        and event.minute == minute
    )


def _records_cover_intervals(
    records: Iterable[dict[str, Any]],
    precision: str,
) -> bool:
    events = [
        parse_cn_datetime(row.get("event_time")) for row in records
    ]
    events = [event for event in events if event is not None]
    if not events or precision not in {"minute", "second"}:
        return False
    minutes = {(event.hour, event.minute) for event in events}
    if not {(14, 49), (14, 50)}.issubset(minutes):
        return False
    if precision == "minute":
        return all(event.second == 0 for event in events)
    return True


def _volume_contract_valid(transaction_rows: dict[str, Any]) -> bool:
    return (
        str(transaction_rows.get("volume_unit") or "")
        == MOOTDX_TRANSACTION_RAW_VOLUME_UNIT
        and str(transaction_rows.get("raw_volume_unit") or "")
        == MOOTDX_TRANSACTION_RAW_VOLUME_UNIT
        and str(transaction_rows.get("normalized_volume_unit") or "")
        == MOOTDX_MINUTE_BAR_VOLUME_UNIT
        and _numbers_match(
            transaction_rows.get("volume_conversion_factor"),
            MOOTDX_LOT_TO_SHARE_FACTOR,
            absolute_tolerance=0.0,
        )
        and str(transaction_rows.get("volume_conversion_basis") or "")
        == MOOTDX_LOT_TO_SHARE_BASIS
        and str(transaction_rows.get("volume_normalization_version") or "")
        == MOOTDX_VOLUME_NORMALIZATION_VERSION
    )


def _record_volume_contract_valid(row: dict[str, Any]) -> bool:
    raw_volume = row.get("raw_volume")
    normalized_volume = row.get("normalized_volume")
    return (
        _finite(raw_volume)
        and _finite(normalized_volume)
        and str(row.get("raw_volume_unit") or "")
        == MOOTDX_TRANSACTION_RAW_VOLUME_UNIT
        and str(row.get("normalized_volume_unit") or "")
        == MOOTDX_MINUTE_BAR_VOLUME_UNIT
        and _numbers_match(
            row.get("volume_conversion_factor"),
            MOOTDX_LOT_TO_SHARE_FACTOR,
            absolute_tolerance=0.0,
        )
        and str(row.get("volume_conversion_basis") or "")
        == MOOTDX_LOT_TO_SHARE_BASIS
        and _numbers_match(
            normalized_volume,
            float(raw_volume) * MOOTDX_LOT_TO_SHARE_FACTOR,
            absolute_tolerance=0.001,
            relative_tolerance=0.000001,
        )
    )


def _normalized_minute_bar(
    final_bar: dict[str, Any],
) -> dict[str, Any]:
    values = final_bar.get("ohlcv") or {}
    raw_volume = values.get("volume")
    source_unit = str(final_bar.get("volume_unit") or "")
    source = str(final_bar.get("source") or "")
    source_version = str(final_bar.get("source_version") or "")
    legacy_unit = (
        source_unit == "mootdx_native_volume"
        and source == "mootdx_tdx_std_minute"
        and "tdx_std_bars_1m" in source_version
    )
    valid = source_unit == MOOTDX_MINUTE_BAR_VOLUME_UNIT or legacy_unit
    return {
        "raw_volume": raw_volume,
        "raw_volume_unit": source_unit,
        "normalized_volume": raw_volume if valid else None,
        "normalized_volume_unit": (
            MOOTDX_MINUTE_BAR_VOLUME_UNIT if valid else ""
        ),
        "volume_conversion_factor": 1.0 if valid else None,
        "volume_conversion_basis": (
            "mootdx_minute_bar_volume_is_share"
            if valid
            else ""
        ),
        "volume_contract_valid": valid and _finite(raw_volume),
    }


def _finalized_bar(
    samples: list[dict[str, Any]],
    code: str,
) -> dict[str, Any]:
    observations = []
    for sample in samples:
        signature = (sample.get("signatures") or {}).get(code)
        if signature is not None:
            observations.append((sample, signature))
    if len(observations) < MINIMUM_STABLE_OBSERVATIONS:
        return {"is_final": False}
    last_hash = str(observations[-1][1].get("ohlcv_hash") or "")
    stable_index = None
    for index in range(len(observations)):
        remaining = observations[index:]
        if (
            len(remaining) >= MINIMUM_STABLE_OBSERVATIONS
            and all(
                str(item[1].get("ohlcv_hash") or "") == last_hash
                for item in remaining
            )
        ):
            stable_index = index
            break
    if stable_index is None or not last_hash:
        return {"is_final": False}
    first_sample = observations[0][0]
    finalized_sample = observations[
        stable_index + MINIMUM_STABLE_OBSERVATIONS - 1
    ][0]
    final_signature = observations[-1][1]
    return {
        "bar_label_time": (
            f"{str(first_sample.get('target_at') or '')[:10]}"
            "T14:50:00+08:00"
        ),
        "first_observed_at": first_sample.get(
            "request_completed_at",
            "",
        ),
        "finalized_at": finalized_sample.get(
            "request_completed_at",
            "",
        ),
        "is_final": True,
        "ohlcv": dict(final_signature.get("ohlcv") or {}),
        "ohlcv_hash": last_hash,
        "volume_unit": str(
            final_signature.get("volume_unit") or ""
        ),
        "source": str(final_signature.get("source") or ""),
        "source_version": str(
            final_signature.get("source_version") or ""
        ),
        "stable_observation_count": MINIMUM_STABLE_OBSERVATIONS,
    }


def _aggregate_matches_bar(
    aggregate: dict[str, Any],
    bar: dict[str, Any],
    *,
    normalized_contract: bool = False,
) -> bool:
    if not aggregate.get("complete"):
        return False
    for field in ("open", "high", "low", "close"):
        if not _numbers_match(
            aggregate.get(field),
            bar.get(field),
            absolute_tolerance=0.0001,
        ):
            return False
    aggregate_volume = (
        aggregate.get("normalized_volume")
        if normalized_contract
        else aggregate.get("volume")
    )
    bar_volume = (
        bar.get("normalized_volume")
        if normalized_contract
        else bar.get("volume")
    )
    return _numbers_match(
        aggregate_volume,
        bar_volume,
        absolute_tolerance=0.001,
        relative_tolerance=0.000001,
    ) and (
        not _finite(bar.get("trade_count"))
        or _numbers_match(
            aggregate.get("trade_count"),
            bar.get("trade_count"),
            absolute_tolerance=0.0,
        )
    )


def _empty_aggregate() -> dict[str, Any]:
    return {
        "interval_start": "",
        "interval_end": "",
        "complete": False,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": None,
        "raw_volume": None,
        "raw_volume_unit": "",
        "normalized_volume": None,
        "normalized_volume_unit": "",
        "volume_conversion_factor": None,
        "volume_conversion_basis": "",
        "trade_count": None,
        "row_count": 0,
        "timestamp_precision": "unknown",
        "boundary_aligned": False,
    }


def _build_provisional_contract(
    probe_result: dict[str, Any],
    attribution: dict[str, Any],
) -> dict[str, Any]:
    rows = list((attribution.get("per_stock") or {}).values())
    first_observed = min(
        str(row["first_observed_at"]) for row in rows
    )
    finalized = max(str(row["finalized_at"]) for row in rows)
    interval_start = min(str(row["interval_start"]) for row in rows)
    interval_end = max(str(row["interval_end"]) for row in rows)
    delay = max(
        float(row.get("finalization_delay_ms") or 0.0)
        for row in rows
    )
    trade_date = (
        str(probe_result.get("trade_date") or "")[:10]
        or str(rows[0].get("bar_label_time") or "")[:10]
        or str(
            (probe_result.get("samples") or [{}])[0].get(
                "sampled_at"
            )
            or ""
        )[:10]
    )
    contract = build_close_time_contract(
        trade_date,
        minute_label_semantics=attribution["status"],
        verified=False,
        probe_evidence_hash=attribution[
            "minute_probe_evidence_hash"
        ],
        bar_label_time=min(
            str(row["bar_label_time"]) for row in rows
        ),
        interval_start=interval_start,
        interval_end=interval_end,
        first_observed_at=first_observed,
        finalized_at=finalized,
        is_final=True,
        finalization_delay_ms=delay,
        transaction_evidence_hash=attribution[
            "transaction_evidence_hash"
        ],
        combined_evidence_hash=attribution[
            "combined_evidence_hash"
        ],
    )
    return contract.as_dict()


def _inconclusive_attribution(
    source: str,
    minute_hash: str,
    transaction_hash: str,
    reasons: list[str],
    *,
    algorithm_version: str = "",
) -> dict[str, Any]:
    attribution = {
        "status": ATTRIBUTION_INCONCLUSIVE,
        "source": source,
        "minute_probe_evidence_hash": minute_hash,
        "transaction_evidence_hash": transaction_hash,
        "per_stock": {},
        "all_stocks_final": False,
        "reasons": reasons,
        "provisional_time_contract": {},
    }
    if algorithm_version:
        attribution["attribution_algorithm_version"] = (
            algorithm_version
        )
    if _is_hash(transaction_hash):
        attribution["combined_evidence_hash"] = (
            compute_combined_evidence_hash(
                attribution,
                source=source,
            )
        )
    else:
        attribution["combined_evidence_hash"] = ""
    return attribution


def _require_source(value: Any) -> str:
    source = str(value or "").strip().lower()
    if not source:
        raise ValueError("evidence_source_required")
    return source


def _require_hash(value: Any, error: str) -> str:
    text = str(value or "").strip().lower()
    if not _is_hash(text):
        raise ValueError(error)
    return text


def _is_hash(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(
        character in "0123456789abcdef"
        for character in text
    )


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in {
        float("inf"),
        float("-inf"),
    }


def _numbers_match(
    left: Any,
    right: Any,
    *,
    absolute_tolerance: float,
    relative_tolerance: float = 0.0,
) -> bool:
    if not _finite(left) or not _finite(right):
        return False
    left_number = float(left)
    right_number = float(right)
    allowed = max(
        absolute_tolerance,
        abs(right_number) * relative_tolerance,
    )
    return abs(left_number - right_number) <= allowed
