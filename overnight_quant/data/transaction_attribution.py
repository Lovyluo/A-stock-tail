from __future__ import annotations

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


def attribute_mootdx_minute_intervals(
    probe_result: dict[str, Any],
    transaction_evidence: dict[str, Any],
) -> dict[str, Any]:
    source = _require_source(probe_result.get("source"))
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
    if not final_bar.get("is_final"):
        errors.append("minute_bar_not_final")
    if not transaction_rows.get("coverage_complete"):
        errors.append("transaction_records_incomplete")
    if transaction_rows.get("timestamp_precision") != "second":
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
        )
        aggregate_1450 = aggregate_transaction_interval(
            records,
            interval_start=f"{day}T14:50:00+08:00",
            interval_end=f"{day}T14:50:59+08:00",
        )
    else:
        aggregate_1449 = _empty_aggregate()
        aggregate_1450 = _empty_aggregate()
    if not aggregate_1449.get("complete"):
        errors.append("transaction_1449_interval_incomplete")
    if not aggregate_1450.get("complete"):
        errors.append("transaction_1450_interval_incomplete")

    stable_ohlcv = final_bar.get("ohlcv") or {}
    trade_count_comparison = "minute_bar_field_unavailable_audit_only"
    if _finite(stable_ohlcv.get("trade_count")):
        trade_count_comparison = "compared_with_minute_bar"
    match_1449 = not errors and _aggregate_matches_bar(
        aggregate_1449,
        stable_ohlcv,
    )
    match_1450 = not errors and _aggregate_matches_bar(
        aggregate_1450,
        stable_ohlcv,
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
        "stable_bar": stable_ohlcv,
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
) -> dict[str, Any]:
    start = parse_cn_datetime(interval_start)
    end = parse_cn_datetime(interval_end)
    if start is None or end is None:
        raise ValueError("transaction_interval_invalid")
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
        }
    prices = [float(row["price"]) for row in selected]
    return {
        "interval_start": start.isoformat(timespec="seconds"),
        "interval_end": end.isoformat(timespec="seconds"),
        "complete": all(
            _finite(row.get(field))
            for row in selected
            for field in ("price", "volume", "trade_count")
        ),
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
        "volume": sum(float(row["volume"]) for row in selected),
        "trade_count": sum(
            int(row["trade_count"]) for row in selected
        ),
        "row_count": len(selected),
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
    }


def _aggregate_matches_bar(
    aggregate: dict[str, Any],
    bar: dict[str, Any],
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
    return _numbers_match(
        aggregate.get("volume"),
        bar.get("volume"),
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
        "trade_count": None,
        "row_count": 0,
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
