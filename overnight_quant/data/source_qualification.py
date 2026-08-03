from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Iterable

from overnight_quant.data.minute_label_probe import (
    compute_probe_evidence_hash,
)
from overnight_quant.data.minute_probe_sources import (
    PROBE_SOURCE_EASTMONEY,
    PROBE_SOURCE_MOOTDX,
    normalize_probe_source,
)
from overnight_quant.data.close_time_contract import (
    PROVISIONAL_MINUTE_LABELS,
)
from overnight_quant.data.transaction_attribution import (
    attribute_mootdx_minute_intervals,
    compute_transaction_evidence_hash,
)
from overnight_quant.data.point_in_time import stable_hash


REQUIRED_PROBE_CLOCKS = (
    "14:49:55",
    "14:50:05",
    "14:50:30",
    "14:51:05",
)


@dataclass(frozen=True)
class MinuteQualificationPolicy:
    minimum_consecutive_trading_days: int = 3
    minimum_stock_count: int = 5
    maximum_request_p95_ms: float = 2000.0


def evaluate_minute_source_qualification(
    probe_results: Iterable[dict[str, Any]],
    *,
    source: str,
    trading_calendar: dict[str, Any] | Iterable[str],
    expected_codes: Iterable[str] | None = None,
    policy: MinuteQualificationPolicy | None = None,
) -> dict[str, Any]:
    normalized_source = normalize_probe_source(source)
    active_policy = policy or MinuteQualificationPolicy()
    calendar_contract = (
        dict(trading_calendar)
        if isinstance(trading_calendar, dict)
        else {"trade_dates": list(trading_calendar)}
    )
    calendar = _normalize_dates(
        calendar_contract.get("trade_dates") or []
    )
    expected = _normalize_codes(expected_codes or [])
    results = sorted(
        (dict(item) for item in probe_results),
        key=lambda item: str(item.get("trade_date") or ""),
    )
    errors = []
    day_results = []
    elapsed_values = []
    source_versions = set()
    seen_dates = set()
    qualified_dates = []

    errors.extend(_calendar_contract_errors(calendar_contract))
    if normalized_source == PROBE_SOURCE_EASTMONEY:
        errors.append("audit_only_source_not_eligible")
    elif normalized_source != PROBE_SOURCE_MOOTDX:
        errors.append("source_not_qualification_candidate")
    if not results:
        errors.append("probe_results_missing")

    for result in results:
        day = str(result.get("trade_date") or "")[:10]
        day_errors = _validate_probe_day(
            result,
            source=normalized_source,
            expected_codes=expected,
            minimum_stock_count=(
                active_policy.minimum_stock_count
            ),
        )
        if not day:
            day_errors.append("probe_trade_date_missing")
        elif day in seen_dates:
            day_errors.append(f"duplicate_probe_trade_date:{day}")
        else:
            seen_dates.add(day)
        if day and day not in calendar:
            day_errors.append(f"probe_date_not_in_calendar:{day}")
        for sample in result.get("samples") or []:
            elapsed = _number(sample.get("request_elapsed_ms"))
            if elapsed is not None:
                elapsed_values.append(elapsed)
            source_versions.update(
                str(value)
                for value in (sample.get("source_versions") or [])
                if str(value).strip()
            )
        if day and not day_errors:
            qualified_dates.append(day)
        day_results.append(
            {
                "trade_date": day,
                "qualified": not day_errors,
                "errors": day_errors,
                "probe_evidence_hash": str(
                    result.get("probe_evidence_hash") or ""
                ),
            }
        )
        errors.extend(day_errors)

    request_p95_ms = _nearest_rank_percentile(
        elapsed_values,
        0.95,
    )
    if request_p95_ms is None:
        errors.append("probe_latency_missing")
    elif request_p95_ms > active_policy.maximum_request_p95_ms:
        errors.append(
            "probe_request_p95_exceeded:"
            f"{request_p95_ms:.3f}"
        )
    if len(source_versions) != 1:
        errors.append(
            "probe_source_version_not_stable:"
            f"{len(source_versions)}"
        )

    consecutive_days = _maximum_consecutive_run(
        qualified_dates,
        calendar,
    )
    if consecutive_days < (
        active_policy.minimum_consecutive_trading_days
    ):
        errors.append(
            "consecutive_qualified_days_below_minimum:"
            f"{consecutive_days}"
        )

    unique_errors = sorted(set(errors))
    qualified = not unique_errors
    return {
        "status": (
            "SOURCE_QUALIFIED_FOR_PM_REVIEW"
            if qualified
            else "SOURCE_NOT_QUALIFIED"
        ),
        "execution_ok": True,
        "data_ready": False,
        "source": normalized_source,
        "source_versions": sorted(source_versions),
        "trading_calendar_source": str(
            calendar_contract.get("source") or ""
        ),
        "trading_calendar_source_version": str(
            calendar_contract.get("source_version") or ""
        ),
        "qualified_for_configuration_review": qualified,
        "automatic_configuration_change": False,
        "qualification_errors": unique_errors,
        "qualified_trading_dates": sorted(qualified_dates),
        "maximum_consecutive_qualified_days": consecutive_days,
        "request_p95_ms": request_p95_ms,
        "policy": {
            "minimum_consecutive_trading_days": (
                active_policy.minimum_consecutive_trading_days
            ),
            "minimum_stock_count": (
                active_policy.minimum_stock_count
            ),
            "maximum_request_p95_ms": (
                active_policy.maximum_request_p95_ms
            ),
        },
        "days": day_results,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _validate_probe_day(
    result: dict[str, Any],
    *,
    source: str,
    expected_codes: list[str],
    minimum_stock_count: int,
) -> list[str]:
    errors = []
    result_source = str(result.get("source") or "").strip().lower()
    if result_source != source:
        errors.append(
            f"probe_source_mismatch:{result_source or '<empty>'}"
        )
    if result.get("status") != "MINUTE_LABEL_PROVISIONAL":
        errors.append("probe_day_not_provisional")
    if result.get("minute_label_validation_status") != (
        "PROVISIONAL_TRANSACTION_ATTRIBUTION"
    ):
        errors.append("probe_transaction_attribution_missing")
    semantics = str(result.get("minute_label_semantics") or "")
    if semantics not in PROVISIONAL_MINUTE_LABELS:
        errors.append("probe_provisional_semantics_invalid")
    if result.get("source_role") != "qualification_candidate":
        errors.append("probe_source_role_not_eligible")
    if int(result.get("late_record_count") or 0) != 0:
        errors.append("probe_late_records_present")
    if any(result.get(key) for key in ("candidates", "tickets", "orders")):
        errors.append("probe_created_strategy_outputs")

    codes = _normalize_codes(result.get("tracked_codes") or [])
    if len(codes) < minimum_stock_count:
        errors.append(
            f"probe_stock_count_below_minimum:{len(codes)}"
        )
    if expected_codes and codes != expected_codes:
        errors.append("probe_expected_codes_mismatch")

    samples = list(result.get("samples") or [])
    clocks = sorted(
        str(sample.get("target_at") or "")[11:19]
        for sample in samples
    )
    if clocks != sorted(REQUIRED_PROBE_CLOCKS):
        errors.append("probe_required_points_invalid")
    for sample in samples:
        clock = str(sample.get("target_at") or "")[11:19]
        if str(sample.get("probe_source") or "").strip().lower() != source:
            errors.append(f"probe_sample_source_mismatch:{clock}")
        if sample.get("error"):
            errors.append(f"probe_sample_failed:{clock}")
        covered = _normalize_codes(sample.get("covered_codes") or [])
        if covered != codes:
            errors.append(f"probe_sample_coverage_incomplete:{clock}")
        if not sample.get("request_started_at"):
            errors.append(f"probe_request_started_missing:{clock}")
        if not sample.get("request_completed_at"):
            errors.append(f"probe_request_completed_missing:{clock}")
        if not _is_hash(sample.get("provider_raw_hash")):
            errors.append(f"probe_provider_raw_hash_invalid:{clock}")
        raw_hashes = sample.get("raw_response_hashes") or []
        if not raw_hashes or not all(_is_hash(value) for value in raw_hashes):
            errors.append(f"probe_raw_hashes_invalid:{clock}")
        source_versions = sample.get("source_versions") or []
        if len(source_versions) != 1:
            errors.append(f"probe_source_version_invalid:{clock}")

    expected_hash = compute_probe_evidence_hash(
        samples,
        codes,
        source=source,
    )
    actual_hash = str(result.get("probe_evidence_hash") or "")
    if not _is_hash(actual_hash) or actual_hash != expected_hash:
        errors.append("probe_evidence_hash_drift")

    transaction = dict(result.get("transaction_evidence") or {})
    attribution = dict(result.get("transaction_attribution") or {})
    try:
        expected_transaction_hash = compute_transaction_evidence_hash(
            transaction,
            source=source,
        )
    except ValueError:
        expected_transaction_hash = ""
    actual_transaction_hash = str(
        result.get("transaction_evidence_hash") or ""
    )
    if (
        not _is_hash(actual_transaction_hash)
        or actual_transaction_hash != expected_transaction_hash
    ):
        errors.append("transaction_evidence_hash_drift")
    try:
        expected_attribution = attribute_mootdx_minute_intervals(
            result,
            transaction,
        )
        expected_combined_hash = str(
            expected_attribution.get("combined_evidence_hash") or ""
        )
        if stable_hash(attribution) != stable_hash(
            expected_attribution
        ):
            errors.append("transaction_attribution_derivation_drift")
    except (KeyError, ValueError):
        expected_combined_hash = ""
    actual_combined_hash = str(
        result.get("combined_evidence_hash") or ""
    )
    if (
        not _is_hash(actual_combined_hash)
        or actual_combined_hash != expected_combined_hash
    ):
        errors.append("combined_evidence_hash_drift")
    if attribution.get("status") != semantics:
        errors.append("transaction_attribution_semantics_mismatch")
    per_stock = attribution.get("per_stock") or {}
    if sorted(per_stock) != codes:
        errors.append("transaction_attribution_coverage_incomplete")
    elif any(
        row.get("status") != semantics
        or row.get("is_final") is not True
        for row in per_stock.values()
    ):
        errors.append("transaction_attribution_not_final_or_consistent")
    return sorted(set(errors))


def _maximum_consecutive_run(
    qualified_dates: Iterable[str],
    calendar: list[str],
) -> int:
    qualified = set(qualified_dates)
    maximum = 0
    current = 0
    for day in calendar:
        if day in qualified:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def _nearest_rank_percentile(
    values: Iterable[float],
    percentile: float,
) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _normalize_dates(values: Iterable[str]) -> list[str]:
    return sorted(
        {
            str(value).strip()[:10]
            for value in values
            if len(str(value).strip()) >= 10
        }
    )


def _calendar_contract_errors(
    calendar: dict[str, Any],
) -> list[str]:
    errors = []
    if not _normalize_dates(calendar.get("trade_dates") or []):
        errors.append("trusted_trading_calendar_missing")
    if not str(calendar.get("source") or "").strip():
        errors.append("trading_calendar_source_missing")
    if not str(calendar.get("source_version") or "").strip():
        errors.append("trading_calendar_source_version_missing")
    if not _is_hash(calendar.get("raw_hash")):
        errors.append("trading_calendar_raw_hash_invalid")
    return errors


def _normalize_codes(values: Iterable[str]) -> list[str]:
    return sorted(
        {
            str(value).strip().zfill(6)
            for value in values
            if str(value).strip()
        }
    )


def _is_hash(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(
        character in "0123456789abcdef"
        for character in text
    )


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:
        return None
    return number
