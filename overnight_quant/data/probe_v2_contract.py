from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Iterable

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_label_probe import (
    MINUTE_REQUEST_DEADLINE_MS,
    PROBE_EVIDENCE_SCHEMA_V2,
)
from overnight_quant.data.probe_worker_process import (
    WORKER_TIMEOUT_ERROR,
)


TIMING_SERIALIZATION_TOLERANCE_MS = 5.0
REQUIRED_PROBE_CLOCKS = (
    "14:49:55",
    "14:50:05",
    "14:50:30",
    "14:51:05",
)


def validate_probe_v2_semantics(
    payload: dict[str, Any],
    *,
    source: str,
    tracked_codes: Iterable[str],
) -> list[str]:
    """Validate v2 timing and endpoint claims beyond hash integrity."""
    if str(payload.get("probe_evidence_schema_version") or "") != (
        PROBE_EVIDENCE_SCHEMA_V2
    ):
        return []

    normalized_source = str(source or "").strip().lower()
    codes = _normalized_codes(tracked_codes)
    errors = []
    if not codes:
        errors.append("probe_v2_tracked_codes_missing")
    if any(payload.get(key) for key in ("candidates", "tickets", "orders")):
        errors.append("probe_v2_strategy_outputs_not_empty")

    preflight_value = payload.get("source_preflight")
    preflight = _mapping(preflight_value)
    if preflight_value is not None and not isinstance(preflight_value, dict):
        errors.append("probe_source_preflight_invalid")
    preflight_status = str(preflight.get("status") or "")
    preflight_failed = (
        payload.get("status") == "SOURCE_PREFLIGHT_FAILED"
    )
    endpoint_id = ""
    if preflight_failed:
        errors.extend(
            _validate_failure_closed_preflight(
                preflight,
                source=normalized_source,
            )
        )
    elif normalized_source == "mootdx":
        preflight_errors, endpoint_id = _validate_mootdx_ready_preflight(
            preflight,
            tracked_codes=codes,
        )
        errors.extend(preflight_errors)
    elif preflight_status != "NOT_REQUIRED":
        errors.append("probe_v2_preflight_status_invalid")

    samples_value = payload.get("samples")
    samples = _mapping_list(samples_value)
    if samples_value is not None and (
        not isinstance(samples_value, list)
        or len(samples) != len(samples_value)
    ):
        errors.append("probe_v2_samples_invalid")
    trade_date = str(payload.get("trade_date") or "")[:10]
    if not _valid_iso_date(trade_date):
        errors.append("probe_v2_trade_date_invalid")
    if preflight_failed:
        if samples:
            errors.append("preflight_failed_samples_must_be_empty")
    else:
        errors.extend(
            _validate_v2_samples(
                samples,
                source=normalized_source,
                tracked_codes=codes,
                endpoint_id=endpoint_id,
                trade_date=trade_date,
            )
        )

    transaction_value = payload.get("transaction_evidence")
    transaction = _mapping(transaction_value)
    if transaction_value is not None and not isinstance(
        transaction_value,
        dict,
    ):
        errors.append("transaction_evidence_invalid")
    if normalized_source == "mootdx" and transaction:
        transaction_endpoint = str(
            transaction.get("endpoint_id") or ""
        ).strip()
        if not transaction_endpoint:
            errors.append("transaction_endpoint_id_missing")
        elif endpoint_id and transaction_endpoint != endpoint_id:
            errors.append("transaction_endpoint_id_mismatch")
        transaction_codes = _normalized_codes(
            transaction.get("requested_codes") or []
        )
        if transaction_codes != codes:
            errors.append("transaction_requested_codes_mismatch")

    errors.extend(_validate_audit_summary(payload, samples))
    return sorted(set(errors))


def _validate_failure_closed_preflight(
    preflight: dict[str, Any],
    *,
    source: str,
) -> list[str]:
    errors = []
    if source != "mootdx":
        errors.append("source_preflight_failed_source_invalid")
    if preflight.get("status") != "SOURCE_PREFLIGHT_FAILED":
        errors.append("probe_source_preflight_status_mismatch")
    if str(preflight.get("source") or "").strip().lower() != source:
        errors.append("probe_source_preflight_source_mismatch")
    attempts_value = preflight.get("attempts")
    attempts = _mapping_list(attempts_value)
    if attempts_value is not None and (
        not isinstance(attempts_value, list)
        or len(attempts) != len(attempts_value)
    ):
        errors.append("probe_source_preflight_attempts_invalid")
    for index, attempt in enumerate(attempts):
        errors.extend(_validate_preflight_attempt_deadline(attempt, index))
    return errors


def _validate_mootdx_ready_preflight(
    preflight: dict[str, Any],
    *,
    tracked_codes: list[str],
) -> tuple[list[str], str]:
    errors = []
    if preflight.get("status") != "SOURCE_PREFLIGHT_READY":
        errors.append("probe_source_preflight_not_ready")
    if str(preflight.get("source") or "").strip().lower() != "mootdx":
        errors.append("probe_source_preflight_source_mismatch")

    covered = _normalized_codes(preflight.get("covered_codes") or [])
    if covered != tracked_codes:
        errors.append("probe_source_preflight_coverage_mismatch")
    if _number(preflight.get("coverage_ratio")) != 1.0:
        errors.append("probe_source_preflight_ratio_invalid")

    endpoint_id = str(preflight.get("endpoint_id") or "").strip()
    if not endpoint_id:
        errors.append("probe_source_preflight_endpoint_missing")
    selected_endpoint = _mapping(preflight.get("selected_endpoint"))
    selected_id = str(selected_endpoint.get("id") or "").strip()
    if not selected_id or selected_id != endpoint_id:
        errors.append("probe_source_preflight_selected_endpoint_mismatch")

    attempts_value = preflight.get("attempts")
    attempts = _mapping_list(attempts_value)
    if attempts_value is not None and (
        not isinstance(attempts_value, list)
        or len(attempts) != len(attempts_value)
    ):
        errors.append("probe_source_preflight_attempts_invalid")
    for index, attempt in enumerate(attempts):
        errors.extend(_validate_preflight_attempt_deadline(attempt, index))
    if not attempts:
        errors.append("probe_source_preflight_attempt_missing")
        return errors, endpoint_id

    successful_attempts = [
        attempt
        for attempt in attempts
        if _preflight_attempt_succeeded(attempt, tracked_codes)
    ]
    if not successful_attempts:
        errors.append("probe_source_preflight_success_attempt_missing")
    else:
        last_success = successful_attempts[-1]
        if str(last_success.get("endpoint_id") or "").strip() != endpoint_id:
            errors.append("probe_source_preflight_success_endpoint_mismatch")
        if last_success is not attempts[-1]:
            errors.append("probe_source_preflight_success_not_final_attempt")
    return errors, endpoint_id


def _validate_preflight_attempt_deadline(
    attempt: dict[str, Any],
    index: int,
) -> list[str]:
    deadline = _number(attempt.get("request_deadline_ms"))
    if deadline is None or deadline <= 0 or deadline > (
        MINUTE_REQUEST_DEADLINE_MS
    ):
        return [f"probe_preflight_deadline_invalid:{index}"]
    return []


def _preflight_attempt_succeeded(
    attempt: dict[str, Any],
    tracked_codes: list[str],
) -> bool:
    return (
        bool(str(attempt.get("endpoint_id") or "").strip())
        and _normalized_codes(attempt.get("covered_codes") or [])
        == tracked_codes
        and _number(attempt.get("coverage_ratio")) == 1.0
        and attempt.get("request_timed_out") is False
        and attempt.get("worker_terminated") is False
        and not str(attempt.get("error_code") or "").strip()
        and not str(attempt.get("error") or "").strip()
    )


def _validate_v2_samples(
    samples: list[dict[str, Any]],
    *,
    source: str,
    tracked_codes: list[str],
    endpoint_id: str,
    trade_date: str,
) -> list[str]:
    errors = []
    clocks = [str(sample.get("target_at") or "")[11:19] for sample in samples]
    if sorted(clocks) != sorted(REQUIRED_PROBE_CLOCKS):
        errors.append("probe_v2_required_points_invalid")
    for sample in samples:
        clock = str(sample.get("target_at") or "")[11:19]
        if str(sample.get("probe_source") or "").strip().lower() != source:
            errors.append(f"probe_v2_sample_source_mismatch:{clock}")
        if _normalized_codes(sample.get("requested_codes") or []) != (
            tracked_codes
        ):
            errors.append(f"probe_sample_requested_codes_mismatch:{clock}")
        covered_codes = _normalized_codes(
            sample.get("covered_codes") or []
        )
        sample_failed = _sample_has_failure(sample)
        if (
            (not sample_failed and covered_codes != tracked_codes)
            or any(code not in tracked_codes for code in covered_codes)
        ):
            errors.append(f"probe_sample_covered_codes_mismatch:{clock}")
        if source == "mootdx":
            sample_endpoint_id = str(
                sample.get("endpoint_id") or ""
            ).strip()
            unstarted_missed_sample = _is_unstarted_missed_sample(sample)
            if unstarted_missed_sample:
                if sample_endpoint_id:
                    errors.append(
                        f"probe_missed_sample_endpoint_must_be_empty:{clock}"
                    )
            elif sample_endpoint_id != endpoint_id:
                errors.append(f"probe_sample_endpoint_id_mismatch:{clock}")
        errors.extend(
            _validate_sample_timing(
                sample,
                clock=clock,
                trade_date=trade_date,
            )
        )
    return errors


def _sample_has_failure(sample: dict[str, Any]) -> bool:
    return (
        sample.get("request_timed_out") is True
        or sample.get("sample_window_missed") is True
        or bool(str(sample.get("error_code") or "").strip())
        or bool(str(sample.get("error") or "").strip())
    )


def _is_unstarted_missed_sample(sample: dict[str, Any]) -> bool:
    return (
        sample.get("sample_window_missed") is True
        and sample.get("request_timed_out") is False
        and sample.get("worker_terminated") is False
        and str(sample.get("error_code") or "") == "SAMPLE_WINDOW_MISSED"
        and _integer(sample.get("returned_record_count")) == 0
        and not (sample.get("covered_codes") or [])
        and not (sample.get("signatures") or {})
        and not (sample.get("raw_response_hashes") or [])
        and not str(sample.get("provider_raw_hash") or "").strip()
        and not (sample.get("source_versions") or [])
        and _number(sample.get("request_elapsed_ms")) == 0.0
    )


def _validate_sample_timing(
    sample: dict[str, Any],
    *,
    clock: str,
    trade_date: str,
) -> list[str]:
    errors = []
    deadline = _number(sample.get("request_deadline_ms"))
    if deadline != float(MINUTE_REQUEST_DEADLINE_MS):
        errors.append(f"probe_sample_deadline_invalid:{clock}")

    target = _strict_cn_datetime(sample.get("target_at"))
    started = _strict_cn_datetime(sample.get("request_started_at"))
    completed = _strict_cn_datetime(sample.get("request_completed_at"))
    if target is None or started is None or completed is None:
        errors.append(f"probe_sample_timestamp_invalid:{clock}")
        return errors
    dates = {target.date(), started.date(), completed.date()}
    if len(dates) != 1:
        errors.append(f"probe_sample_date_mismatch:{clock}")
    target_date = target.date().isoformat()
    if trade_date and trade_date != target_date:
        errors.append(f"probe_sample_trade_date_mismatch:{clock}")
    if str(sample.get("sample_trade_date") or "") != target_date:
        errors.append(f"probe_sample_trade_date_mismatch:{clock}")
    if completed < started:
        errors.append(f"probe_sample_completion_before_start:{clock}")
        return errors
    if started < target:
        errors.append(f"probe_sample_started_before_target:{clock}")

    schedule_lag = (started - target).total_seconds() * 1000
    completion_lag = (completed - target).total_seconds() * 1000
    request_elapsed = (completed - started).total_seconds() * 1000
    for field, expected in (
        ("schedule_lag_ms", schedule_lag),
        ("completion_lag_ms", completion_lag),
        ("request_elapsed_ms", request_elapsed),
    ):
        actual = _number(sample.get(field))
        if actual is None or abs(actual - expected) > (
            TIMING_SERIALIZATION_TOLERANCE_MS
        ):
            errors.append(f"probe_sample_timing_mismatch:{clock}:{field}")

    timed_out = sample.get("request_timed_out") is True
    error_code = str(sample.get("error_code") or "")
    returned = _integer(sample.get("returned_record_count"))
    if timed_out and (
        error_code != WORKER_TIMEOUT_ERROR
        or sample.get("worker_terminated") is not True
        or returned != 0
    ):
        errors.append(f"probe_sample_timeout_contract_invalid:{clock}")
    if error_code == WORKER_TIMEOUT_ERROR and not timed_out:
        errors.append(f"probe_sample_timeout_contract_invalid:{clock}")

    successful = (
        not timed_out
        and sample.get("sample_window_missed") is not True
        and not error_code
        and not str(sample.get("error") or "")
    )
    if successful and completion_lag > MINUTE_REQUEST_DEADLINE_MS:
        errors.append(f"probe_sample_absolute_deadline_exceeded:{clock}")
    if successful and sample.get("worker_terminated") is True:
        errors.append(f"probe_sample_success_worker_terminated:{clock}")
    return errors


def _validate_audit_summary(
    payload: dict[str, Any],
    samples: list[dict[str, Any]],
) -> list[str]:
    expected = {
        "late_start_count": 0,
        "deadline_exceeded_count": 0,
        "missed_sample_count": 0,
        "late_record_count": 0,
    }
    for sample in samples:
        target = _strict_cn_datetime(sample.get("target_at"))
        started = _strict_cn_datetime(sample.get("request_started_at"))
        completed = _strict_cn_datetime(sample.get("request_completed_at"))
        if target is None or started is None or completed is None:
            continue
        schedule_lag = (started - target).total_seconds() * 1000
        completion_lag = (completed - target).total_seconds() * 1000
        missed = sample.get("sample_window_missed") is True
        timed_out = sample.get("request_timed_out") is True
        if schedule_lag > MINUTE_REQUEST_DEADLINE_MS:
            expected["late_start_count"] += 1
        if not missed and (
            timed_out or completion_lag > MINUTE_REQUEST_DEADLINE_MS
        ):
            expected["deadline_exceeded_count"] += 1
        if missed:
            expected["missed_sample_count"] += 1
        if (
            not sample.get("error")
            and completion_lag > MINUTE_REQUEST_DEADLINE_MS
        ):
            expected["late_record_count"] += max(
                0,
                _integer(sample.get("returned_record_count")) or 0,
            )
    errors = []
    for key, expected_value in expected.items():
        if _integer(payload.get(key)) != expected_value:
            errors.append(f"probe_audit_summary_mismatch:{key}")
    return errors


def _strict_cn_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    if parsed.utcoffset() != timedelta(hours=8):
        return None
    return parsed.astimezone(CN_TZ)


def _normalized_codes(values: Iterable[Any]) -> list[str]:
    return sorted(
        str(value).strip().zfill(6)
        for value in values
        if str(value).strip()
    )


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    return number


def _integer(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number) or not number.is_integer():
        return None
    return int(number)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _valid_iso_date(value: str) -> bool:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat() == value
    except ValueError:
        return False
