from __future__ import annotations

from datetime import date, datetime, time as datetime_time
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterable
from uuid import uuid4

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_probe_sources import mootdx_server_candidates
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash
from overnight_quant.data.probe_worker_process import run_probe_worker_process


NODE_BENCHMARK_VERSION_V1 = "mootdx_node_benchmark_v1"
NODE_BENCHMARK_VERSION = "mootdx_node_benchmark_v2"
BENCHMARK_EVIDENCE_SCHEMA_V1 = "v1"
BENCHMARK_EVIDENCE_SCHEMA_V2 = "v2"
FORMAL_VALIDATION_CODES = (
    "000001",
    "000333",
    "600000",
    "600519",
    "601318",
)
FORMAL_MINUTE_OFFSET = 800
COMPARISON_MINUTE_OFFSET = 320
REQUEST_DEADLINE_MS = 2000
RECOMMENDATION_MAX_ELAPSED_MS = 1500


def run_mootdx_node_benchmark(
    codes: Iterable[str],
    *,
    trade_date: str | date | None = None,
    endpoint_candidates: list[dict[str, Any]] | None = None,
    top_count: int = 3,
    qualification_rounds: int = 5,
    deadline_ms: int = REQUEST_DEADLINE_MS,
    recommendation_max_ms: int = RECOMMENDATION_MAX_ELAPSED_MS,
    compare_offsets: bool = True,
    diagnostic_only: bool = False,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]] = (
        run_probe_worker_process
    ),
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Benchmark mootdx endpoints without changing runtime configuration."""
    if int(deadline_ms) != REQUEST_DEADLINE_MS:
        raise ValueError("node_benchmark_deadline_must_be_2000ms")
    if int(recommendation_max_ms) > RECOMMENDATION_MAX_ELAPSED_MS:
        raise ValueError("node_benchmark_recommendation_limit_exceeds_1500ms")
    if int(recommendation_max_ms) <= 0:
        raise ValueError("node_benchmark_recommendation_limit_invalid")

    normalized_codes = sorted(
        {
            str(code).strip().zfill(6)
            for code in codes
            if str(code).strip()
        }
    )
    if not normalized_codes:
        raise ValueError("node_benchmark_codes_missing")
    formal_codes = sorted(FORMAL_VALIDATION_CODES)
    non_formal_scope = normalized_codes != formal_codes
    if non_formal_scope and not diagnostic_only:
        raise ValueError("node_benchmark_formal_codes_mismatch")

    rounds = max(5, int(qualification_rounds))
    requested_limit = max(1, min(3, int(top_count)))
    limit = requested_limit if diagnostic_only else 3
    runtime_clock = clock or (lambda: datetime.now(CN_TZ))
    benchmark_started_at = _as_cn(runtime_clock())
    data_trade_date = _trade_date_text(
        trade_date or benchmark_started_at.date()
    )
    candidates = _deduplicate_endpoints(
        endpoint_candidates
        if endpoint_candidates is not None
        else mootdx_server_candidates(limit=1000)
    )

    screening = [
        _run_endpoint(
            endpoint,
            normalized_codes,
            data_trade_date=data_trade_date,
            deadline_ms=deadline_ms,
            worker_runner=worker_runner,
            operation="benchmark_preflight",
            clock=runtime_clock,
        )
        for endpoint in candidates
    ]
    survivors = [
        row
        for row in screening
        if _is_screening_survivor(row, normalized_codes)
    ]
    ranked_survivors = sorted(survivors, key=_screening_rank)
    finalists = [row["endpoint"] for row in ranked_survivors[:limit]]

    qualification: list[dict[str, Any]] = []
    for endpoint in finalists:
        runs = [
            _run_endpoint(
                endpoint,
                normalized_codes,
                data_trade_date=data_trade_date,
                deadline_ms=deadline_ms,
                worker_runner=worker_runner,
                operation="benchmark_preflight",
                clock=runtime_clock,
            )
            for _ in range(rounds)
        ]
        qualification.append(
            _summarize_endpoint(
                endpoint,
                runs,
                expected_codes=normalized_codes,
                recommendation_max_ms=recommendation_max_ms,
            )
        )

    eligible = sorted(
        [row for row in qualification if row["recommendation_eligible"]],
        key=lambda row: (
            float(row["latency_ms"]["p95"]),
            float(row["latency_ms"]["max"]),
            str(row["endpoint"]["id"]),
        ),
    )
    recommended = None if diagnostic_only else (
        eligible[0]["endpoint"] if eligible else None
    )
    comparison_endpoint = recommended or (
        finalists[0] if finalists and not diagnostic_only else None
    )
    if compare_offsets and comparison_endpoint is not None:
        offset_comparison = _compare_offsets(
            comparison_endpoint,
            normalized_codes,
            data_trade_date=data_trade_date,
            deadline_ms=deadline_ms,
            worker_runner=worker_runner,
            clock=runtime_clock,
        )
    else:
        reason = (
            "diagnostic_scope"
            if diagnostic_only
            else "no_screening_survivor"
            if not finalists
            else "comparison_disabled"
        )
        offset_comparison = _empty_offset_comparison(reason)

    benchmark_completed_at = _as_cn(runtime_clock())
    status = (
        "NO_SCREENING_SURVIVOR"
        if not finalists
        else "DIAGNOSTIC_BENCHMARK_COMPLETED"
        if diagnostic_only
        else "MOOTDX_NODE_BENCHMARK_COMPLETED"
    )
    result = {
        "status": status,
        "execution_ok": True,
        "data_ready": False,
        "audit_only": True,
        "diagnostic_only": bool(diagnostic_only),
        "benchmark_evidence_schema_version": BENCHMARK_EVIDENCE_SCHEMA_V2,
        "benchmark_version": NODE_BENCHMARK_VERSION,
        "data_trade_date": data_trade_date,
        "trade_date": data_trade_date,
        "benchmark_started_at": benchmark_started_at.isoformat(
            timespec="milliseconds"
        ),
        "benchmark_completed_at": benchmark_completed_at.isoformat(
            timespec="milliseconds"
        ),
        "observed_at": benchmark_started_at.isoformat(
            timespec="milliseconds"
        ),
        "tracked_codes": normalized_codes,
        "formal_validation_codes": formal_codes,
        "request_deadline_ms": REQUEST_DEADLINE_MS,
        "recommendation_max_elapsed_ms": int(recommendation_max_ms),
        "screened_endpoint_count": len(candidates),
        "screening_survivor_count": len(survivors),
        "screening": screening,
        "qualification_rounds": rounds,
        "qualification": qualification,
        "recommended_endpoint": recommended,
        "automatic_configuration_change": False,
        "formal_minute_offset": FORMAL_MINUTE_OFFSET,
        "offset_comparison": offset_comparison,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    result["benchmark_evidence_hash"] = compute_benchmark_evidence_hash(
        result
    )
    return result


def compute_benchmark_evidence_hash(payload: dict[str, Any]) -> str:
    material = dict(payload)
    material.pop("benchmark_evidence_hash", None)
    return stable_hash(material)


def verify_benchmark_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    schema = str(
        payload.get("benchmark_evidence_schema_version")
        or BENCHMARK_EVIDENCE_SCHEMA_V1
    )
    errors: list[str] = []
    if schema not in {
        BENCHMARK_EVIDENCE_SCHEMA_V1,
        BENCHMARK_EVIDENCE_SCHEMA_V2,
    }:
        errors.append(f"benchmark_schema_unsupported:{schema}")
    expected = str(payload.get("benchmark_evidence_hash") or "")
    actual = compute_benchmark_evidence_hash(payload)
    if not expected or expected != actual:
        errors.append("benchmark_evidence_hash_mismatch")
    if schema == BENCHMARK_EVIDENCE_SCHEMA_V2:
        errors.extend(_validate_v2_benchmark_semantics(payload))
    return {
        "status": (
            "BENCHMARK_EVIDENCE_VERIFIED"
            if not errors
            else "BENCHMARK_EVIDENCE_INVALID"
        ),
        "execution_ok": True,
        "data_ready": False,
        "benchmark_evidence_schema_version": schema,
        "benchmark_evidence_hash": expected,
        "recomputed_benchmark_evidence_hash": actual,
        "errors": errors,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def write_benchmark_json_atomic(
    path: str | Path,
    payload: dict[str, Any],
) -> Path:
    import json
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"benchmark_output_already_exists:{target}")
    temporary = target.with_name(
        f"{target.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # A same-directory hard link publishes the fully written file in
            # one filesystem operation and fails if another process won.
            os.link(temporary, target)
        except FileExistsError as exc:
            raise FileExistsError(
                f"benchmark_output_already_exists:{target}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _validate_v2_benchmark_semantics(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    formal_codes = sorted(FORMAL_VALIDATION_CODES)

    if payload.get("benchmark_version") != NODE_BENCHMARK_VERSION:
        errors.append("benchmark_version_invalid")
    if payload.get("execution_ok") is not True:
        errors.append("benchmark_execution_not_ok")
    if payload.get("audit_only") is not True:
        errors.append("benchmark_audit_only_required")
    if payload.get("data_ready") is not False:
        errors.append("benchmark_data_ready_must_be_false")
    for field in ("candidates", "tickets", "orders"):
        if payload.get(field) != []:
            errors.append(f"benchmark_trading_output_not_empty:{field}")
    if payload.get("automatic_configuration_change") is not False:
        errors.append("benchmark_automatic_configuration_change_forbidden")
    if payload.get("request_deadline_ms") != REQUEST_DEADLINE_MS:
        errors.append("benchmark_request_deadline_invalid")
    if payload.get("formal_minute_offset") != FORMAL_MINUTE_OFFSET:
        errors.append("benchmark_formal_minute_offset_invalid")

    recommendation_limit = _optional_float(
        payload.get("recommendation_max_elapsed_ms")
    )
    if (
        recommendation_limit is None
        or not math.isfinite(recommendation_limit)
        or recommendation_limit <= 0
        or recommendation_limit > RECOMMENDATION_MAX_ELAPSED_MS
    ):
        errors.append("benchmark_recommendation_limit_invalid")

    tracked_codes = payload.get("tracked_codes")
    diagnostic_only = payload.get("diagnostic_only") is True
    if payload.get("formal_validation_codes") != formal_codes:
        errors.append("benchmark_formal_validation_codes_invalid")
    if not diagnostic_only and tracked_codes != formal_codes:
        errors.append("benchmark_formal_codes_invalid")
    if (
        not isinstance(tracked_codes, list)
        or not tracked_codes
        or tracked_codes != sorted(set(tracked_codes))
    ):
        errors.append("benchmark_tracked_codes_invalid")

    data_trade_date = None
    try:
        data_trade_date = _trade_date_text(payload.get("data_trade_date"))
    except (TypeError, ValueError):
        errors.append("benchmark_data_trade_date_invalid")
    if (
        data_trade_date is not None
        and payload.get("trade_date") != data_trade_date
    ):
        errors.append("benchmark_trade_date_mismatch")

    started = _parse_audited_datetime(payload.get("benchmark_started_at"))
    completed = _parse_audited_datetime(payload.get("benchmark_completed_at"))
    observed = _parse_audited_datetime(payload.get("observed_at"))
    if started is None or completed is None or observed is None:
        errors.append("benchmark_real_time_missing")
    else:
        if completed < started or observed != started:
            errors.append("benchmark_real_time_invalid")
        if (
            data_trade_date is not None
            and started.date().isoformat() < data_trade_date
        ):
            errors.append("benchmark_collection_precedes_data_trade_date")

    screening = payload.get("screening")
    qualification = payload.get("qualification")
    offset_comparison = payload.get("offset_comparison")
    if not isinstance(screening, list):
        errors.append("benchmark_screening_invalid")
        screening = []
    if not isinstance(qualification, list):
        errors.append("benchmark_qualification_invalid")
        qualification = []
    if not isinstance(offset_comparison, dict):
        errors.append("benchmark_offset_comparison_invalid")
        offset_comparison = {}

    if payload.get("screened_endpoint_count") != len(screening):
        errors.append("benchmark_screened_endpoint_count_mismatch")
    expected_codes = tracked_codes if isinstance(tracked_codes, list) else []
    survivor_count = sum(
        _is_screening_survivor(row, expected_codes)
        for row in screening
        if isinstance(row, dict)
    )
    if payload.get("screening_survivor_count") != survivor_count:
        errors.append("benchmark_screening_survivor_count_mismatch")
    expected_status = (
        "NO_SCREENING_SURVIVOR"
        if survivor_count == 0
        else "DIAGNOSTIC_BENCHMARK_COMPLETED"
        if diagnostic_only
        else "MOOTDX_NODE_BENCHMARK_COMPLETED"
    )
    if payload.get("status") != expected_status:
        errors.append("benchmark_status_mismatch")

    for index, row in enumerate(screening):
        errors.extend(
            _validate_v2_benchmark_run(
                row,
                prefix=f"screening[{index}]",
                data_trade_date=data_trade_date,
                benchmark_started_at=started,
                benchmark_completed_at=completed,
            )
        )

    rankable_survivors = []
    for index, row in enumerate(screening):
        if not isinstance(row, dict) or not _is_screening_survivor(
            row,
            expected_codes,
        ):
            continue
        try:
            rank = _screening_rank(row)
        except (KeyError, TypeError, ValueError, OverflowError):
            errors.append(f"benchmark_screening_rank_invalid:{index}")
            continue
        rankable_survivors.append((rank, row))
    ranked_survivors = [
        row
        for _rank, row in sorted(
            rankable_survivors,
            key=lambda item: item[0],
        )
    ]
    survivor_endpoints = [
        row.get("endpoint")
        for row in ranked_survivors
    ]
    expected_finalist_endpoints = survivor_endpoints[
        : min(3, len(survivor_endpoints))
    ]
    actual_qualification_endpoints = [
        row.get("endpoint") if isinstance(row, dict) else None
        for row in qualification
    ]
    formal_finalist_sequence_valid = bool(
        diagnostic_only
        or actual_qualification_endpoints == expected_finalist_endpoints
    )
    recommendation_limit_value = (
        recommendation_limit if recommendation_limit is not None else 0.0
    )
    qualification_errors, recomputed_qualification = (
        _validate_v2_qualification_semantics(
            payload,
            qualification,
            survivor_endpoints=survivor_endpoints,
            expected_finalist_endpoints=(
                None if diagnostic_only else expected_finalist_endpoints
            ),
            expected_codes=expected_codes,
            data_trade_date=data_trade_date,
            benchmark_started_at=started,
            benchmark_completed_at=completed,
            recommendation_max_ms=recommendation_limit_value,
        )
    )
    errors.extend(qualification_errors)

    eligible = []
    if formal_finalist_sequence_valid:
        eligible = sorted(
            [
                row
                for row in recomputed_qualification
                if row.get("recommendation_eligible") is True
            ],
            key=lambda row: (
                float(row["latency_ms"]["p95"]),
                float(row["latency_ms"]["max"]),
                str(row["endpoint"]["id"]),
            ),
        )
    expected_recommended = None if diagnostic_only else (
        eligible[0]["endpoint"] if eligible else None
    )
    if payload.get("recommended_endpoint") != expected_recommended:
        errors.append("benchmark_recommended_endpoint_mismatch")

    errors.extend(
        _validate_v2_offset_semantics(
            offset_comparison,
            diagnostic_only=diagnostic_only,
            survivor_count=survivor_count,
            expected_finalist_endpoints=expected_finalist_endpoints,
            expected_recommended=expected_recommended,
            expected_codes=expected_codes,
            data_trade_date=data_trade_date,
            benchmark_started_at=started,
            benchmark_completed_at=completed,
        )
    )

    if payload.get("status") == "NO_SCREENING_SURVIVOR":
        if survivor_count != 0:
            errors.append("benchmark_no_survivor_status_mismatch")
        if qualification != []:
            errors.append("benchmark_no_survivor_qualification_not_empty")
        if payload.get("recommended_endpoint") is not None:
            errors.append("benchmark_no_survivor_recommendation_present")

    return errors


def _validate_v2_qualification_semantics(
    payload: dict[str, Any],
    qualification: list[Any],
    *,
    survivor_endpoints: list[Any],
    expected_finalist_endpoints: list[Any] | None,
    expected_codes: list[str],
    data_trade_date: str | None,
    benchmark_started_at: datetime | None,
    benchmark_completed_at: datetime | None,
    recommendation_max_ms: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    recomputed: list[dict[str, Any]] = []
    qualification_rounds = payload.get("qualification_rounds")
    if (
        isinstance(qualification_rounds, bool)
        or not isinstance(qualification_rounds, int)
        or qualification_rounds < 5
    ):
        errors.append("benchmark_qualification_rounds_invalid")
        qualification_rounds = None
    if survivor_endpoints and not qualification:
        errors.append("benchmark_qualification_missing")
    if not survivor_endpoints and qualification:
        errors.append("benchmark_qualification_without_survivor")
    if len(qualification) > min(3, len(survivor_endpoints)):
        errors.append("benchmark_qualification_endpoint_count_invalid")
    actual_endpoints = [
        summary.get("endpoint") if isinstance(summary, dict) else None
        for summary in qualification
    ]
    if (
        expected_finalist_endpoints is not None
        and actual_endpoints != expected_finalist_endpoints
    ):
        errors.append("benchmark_qualification_finalists_mismatch")

    seen_endpoints: set[str] = set()
    for index, summary in enumerate(qualification):
        if not isinstance(summary, dict):
            errors.append(f"benchmark_qualification_summary_invalid:{index}")
            continue
        endpoint = summary.get("endpoint")
        endpoint_key = stable_hash(endpoint)
        if endpoint not in survivor_endpoints:
            errors.append(f"benchmark_qualification_endpoint_unknown:{index}")
        if endpoint_key in seen_endpoints:
            errors.append(f"benchmark_qualification_endpoint_duplicate:{index}")
        seen_endpoints.add(endpoint_key)

        runs = summary.get("runs")
        if not isinstance(runs, list):
            errors.append(f"benchmark_qualification_runs_invalid:{index}")
            continue
        if qualification_rounds is not None and len(runs) != qualification_rounds:
            errors.append(f"benchmark_qualification_rounds_mismatch:{index}")
        for run_index, row in enumerate(runs):
            prefix = f"qualification[{index}].runs[{run_index}]"
            errors.extend(
                _validate_v2_benchmark_run(
                    row,
                    prefix=prefix,
                    data_trade_date=data_trade_date,
                    benchmark_started_at=benchmark_started_at,
                    benchmark_completed_at=benchmark_completed_at,
                )
            )
            if isinstance(row, dict):
                if row.get("endpoint") != endpoint:
                    errors.append(f"benchmark_qualification_run_endpoint:{prefix}")
                if row.get("operation") != "benchmark_preflight":
                    errors.append(f"benchmark_qualification_run_operation:{prefix}")
                if row.get("minute_offset") is not None:
                    errors.append(f"benchmark_qualification_run_offset:{prefix}")
        try:
            expected_summary = _summarize_endpoint(
                endpoint,
                runs,
                expected_codes=expected_codes,
                recommendation_max_ms=int(recommendation_max_ms),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            errors.append(f"benchmark_qualification_recompute_failed:{index}")
            continue
        for field in (
            "round_count",
            "successful_round_count",
            "complete_coverage_round_count",
            "timeout_count",
            "error_count",
            "latency_ms",
            "process_overhead_ms",
            "recommendation_eligible",
        ):
            if summary.get(field) != expected_summary.get(field):
                errors.append(
                    f"benchmark_qualification_summary_mismatch:{index}:{field}"
                )
        recomputed.append(expected_summary)
    return errors, recomputed


def _validate_v2_offset_semantics(
    offset_comparison: dict[str, Any],
    *,
    diagnostic_only: bool,
    survivor_count: int,
    expected_finalist_endpoints: list[Any],
    expected_recommended: dict[str, Any] | None,
    expected_codes: list[str],
    data_trade_date: str | None,
    benchmark_started_at: datetime | None,
    benchmark_completed_at: datetime | None,
) -> list[str]:
    errors: list[str] = []
    if offset_comparison.get("audit_only") is not True:
        errors.append("benchmark_offset_audit_only_required")
    if offset_comparison.get("formal_default_unchanged") is not True:
        errors.append("benchmark_offset_default_changed")

    runs = offset_comparison.get("runs")
    if not isinstance(runs, dict):
        errors.append("benchmark_offset_runs_invalid")
        runs = {}
    status = offset_comparison.get("status")
    if status == "NOT_RUN" or diagnostic_only or survivor_count == 0:
        expected_reason = (
            "diagnostic_scope"
            if diagnostic_only
            else "no_screening_survivor"
            if survivor_count == 0
            else "comparison_disabled"
        )
        if status != "NOT_RUN":
            errors.append("benchmark_offset_not_run_status_mismatch")
        if runs != {}:
            errors.append("benchmark_offset_not_run_has_runs")
        if offset_comparison.get("incomplete_reasons") != [expected_reason]:
            errors.append("benchmark_offset_not_run_reason_mismatch")
        for field in (
            "same_record_counts",
            "same_1450_ohlcv_signatures",
            "same_canonical_hashes",
        ):
            if offset_comparison.get(field) is not False:
                errors.append(f"benchmark_offset_not_run_flag_invalid:{field}")
        return errors

    expected_offsets = {
        str(FORMAL_MINUTE_OFFSET),
        str(COMPARISON_MINUTE_OFFSET),
    }
    if set(runs) != expected_offsets:
        errors.append("benchmark_offset_run_keys_invalid")

    expected_endpoint = expected_recommended
    if expected_endpoint is None and expected_finalist_endpoints:
        expected_endpoint = expected_finalist_endpoints[0]

    incomplete_reasons: list[str] = []
    valid_runs: dict[str, dict[str, Any]] = {}
    for offset in sorted(expected_offsets):
        row = runs.get(offset)
        prefix = f"offset[{offset}]"
        errors.extend(
            _validate_v2_benchmark_run(
                row,
                prefix=prefix,
                data_trade_date=data_trade_date,
                benchmark_started_at=benchmark_started_at,
                benchmark_completed_at=benchmark_completed_at,
            )
        )
        if not isinstance(row, dict):
            continue
        valid_runs[offset] = row
        if row.get("operation") != "benchmark_minute":
            errors.append(f"benchmark_offset_operation_invalid:{offset}")
        if row.get("minute_offset") != int(offset):
            errors.append(f"benchmark_offset_value_invalid:{offset}")
        if expected_endpoint is None or row.get("endpoint") != expected_endpoint:
            errors.append(f"benchmark_offset_endpoint_invalid:{offset}")
        expected_endpoint_id = str(
            expected_endpoint.get("id") or ""
            if isinstance(expected_endpoint, dict)
            else ""
        )
        if row.get("endpoint_id") != expected_endpoint_id:
            errors.append(f"benchmark_offset_endpoint_id_invalid:{offset}")
        if row.get("requested_codes") != expected_codes:
            errors.append(f"benchmark_offset_requested_codes_invalid:{offset}")
        if row.get("covered_codes") != expected_codes:
            errors.append(f"benchmark_offset_covered_codes_invalid:{offset}")
        presence = row.get("presence_by_code")
        if (
            not isinstance(presence, dict)
            or sorted(presence) != expected_codes
            or any(presence.get(code) is not True for code in expected_codes)
        ):
            errors.append(f"benchmark_offset_presence_invalid:{offset}")
        signatures = row.get("signatures")
        if not isinstance(signatures, dict) or sorted(signatures) != expected_codes:
            errors.append(f"benchmark_offset_signatures_invalid:{offset}")
        else:
            for code in expected_codes:
                event = parse_cn_datetime(
                    (signatures.get(code) or {}).get("event_time")
                )
                if (
                    event is None
                    or data_trade_date is None
                    or event.date().isoformat() != data_trade_date
                    or event.time().replace(tzinfo=None)
                    != datetime_time(14, 50)
                ):
                    errors.append(
                        f"benchmark_offset_signature_time_invalid:{offset}:{code}"
                    )
        if sorted(row.get("minute_record_count_by_code") or {}) != expected_codes:
            errors.append(f"benchmark_offset_record_counts_invalid:{offset}")
        if sorted(row.get("canonical_minute_hash_by_code") or {}) != expected_codes:
            errors.append(f"benchmark_offset_canonical_hashes_invalid:{offset}")
        if data_trade_date is not None:
            incomplete_reasons.extend(
                _offset_run_incomplete_reasons(
                    row,
                    expected_codes=expected_codes,
                    data_trade_date=data_trade_date,
                    offset=offset,
                )
            )

    expected_reasons = sorted(set(incomplete_reasons))
    complete = not expected_reasons and set(valid_runs) == expected_offsets
    expected_status = (
        "OFFSET_COMPARISON_COMPLETED"
        if complete
        else "OFFSET_COMPARISON_INCOMPLETE"
    )
    if status != expected_status:
        errors.append("benchmark_offset_status_mismatch")
    if offset_comparison.get("incomplete_reasons") != expected_reasons:
        errors.append("benchmark_offset_reasons_mismatch")

    formal = valid_runs.get(str(FORMAL_MINUTE_OFFSET), {})
    comparison = valid_runs.get(str(COMPARISON_MINUTE_OFFSET), {})
    expected_flags = {
        "same_record_counts": bool(
            complete
            and formal.get("minute_record_count_by_code")
            == comparison.get("minute_record_count_by_code")
        ),
        "same_1450_ohlcv_signatures": bool(
            complete
            and formal.get("signatures") == comparison.get("signatures")
        ),
        "same_canonical_hashes": bool(
            complete
            and formal.get("canonical_minute_hash_by_code")
            == comparison.get("canonical_minute_hash_by_code")
        ),
    }
    for field, expected_value in expected_flags.items():
        if offset_comparison.get(field) is not expected_value:
            errors.append(f"benchmark_offset_flag_mismatch:{field}")
    return errors


def _validate_v2_benchmark_run(
    row: Any,
    *,
    prefix: str,
    data_trade_date: str | None,
    benchmark_started_at: datetime | None,
    benchmark_completed_at: datetime | None,
) -> list[str]:
    if not isinstance(row, dict):
        return [f"benchmark_request_invalid:{prefix}"]
    errors: list[str] = []
    if (
        data_trade_date is not None
        and row.get("data_trade_date") != data_trade_date
    ):
        errors.append(f"benchmark_request_trade_date_mismatch:{prefix}")

    parent_started = _parse_audited_datetime(
        row.get("benchmark_request_started_at")
    )
    parent_completed = _parse_audited_datetime(
        row.get("benchmark_request_completed_at")
    )
    if parent_started is None or parent_completed is None:
        errors.append(f"benchmark_request_time_missing:{prefix}")
    elif parent_completed < parent_started:
        errors.append(f"benchmark_request_time_invalid:{prefix}")
    elif (
        benchmark_started_at is not None
        and benchmark_completed_at is not None
        and (
            parent_started < benchmark_started_at
            or parent_completed > benchmark_completed_at
        )
    ):
        errors.append(f"benchmark_request_outside_run_window:{prefix}")

    worker_started_text = row.get("worker_request_started_at")
    worker_completed_text = row.get("worker_request_completed_at")
    worker_started = _parse_audited_datetime(worker_started_text)
    worker_completed = _parse_audited_datetime(worker_completed_text)
    if bool(worker_started_text) != bool(worker_completed_text):
        errors.append(f"benchmark_worker_time_incomplete:{prefix}")
    elif worker_started_text and (
        worker_started is None or worker_completed is None
    ):
        errors.append(f"benchmark_worker_time_invalid:{prefix}")
    elif (
        worker_started is not None
        and worker_completed is not None
        and worker_completed < worker_started
    ):
        errors.append(f"benchmark_worker_time_invalid:{prefix}")
    elif (
        worker_started is not None
        and worker_completed is not None
        and parent_started is not None
        and parent_completed is not None
        and (
            worker_started < parent_started
            or worker_completed > parent_completed
        )
    ):
        errors.append(f"benchmark_worker_time_outside_request:{prefix}")
    return errors


def _parse_audited_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(CN_TZ)


def _run_endpoint(
    endpoint: dict[str, Any],
    codes: list[str],
    *,
    data_trade_date: str,
    deadline_ms: int,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]],
    operation: str,
    clock: Callable[[], datetime],
    minute_offset: int | None = None,
) -> dict[str, Any]:
    parent_started = _as_cn(clock())
    task = {
        "operation": operation,
        "source": "mootdx",
        "codes": list(codes),
        "observed_at": parent_started.isoformat(timespec="milliseconds"),
        "data_trade_date": data_trade_date,
        "endpoint": endpoint,
        "provider_timeout_seconds": deadline_ms / 1000.0,
    }
    if minute_offset is not None:
        task["minute_offset"] = int(minute_offset)
    wall_started = time.perf_counter()
    worker_result = worker_runner(task, deadline_ms)
    wall_elapsed_ms = round((time.perf_counter() - wall_started) * 1000, 3)
    parent_completed = _as_cn(clock())
    payload = worker_result.get("payload") or {}
    e2e_ms = float(worker_result.get("elapsed_ms") or wall_elapsed_ms)
    provider_ms = _optional_float(payload.get("worker_request_elapsed_ms"))
    requested = sorted(
        str(code).zfill(6) for code in payload.get("requested_codes") or []
    )
    covered = sorted(
        str(code).zfill(6) for code in payload.get("covered_codes") or []
    )
    return {
        "endpoint": dict(endpoint),
        "endpoint_id": str(payload.get("endpoint_id") or ""),
        "operation": operation,
        "data_trade_date": data_trade_date,
        "minute_offset": minute_offset,
        "benchmark_request_started_at": parent_started.isoformat(
            timespec="milliseconds"
        ),
        "benchmark_request_completed_at": parent_completed.isoformat(
            timespec="milliseconds"
        ),
        "worker_request_started_at": str(
            payload.get("worker_request_started_at") or ""
        ),
        "worker_request_completed_at": str(
            payload.get("worker_request_completed_at") or ""
        ),
        "ok": worker_result.get("ok") is True,
        "requested_codes": requested,
        "covered_codes": covered,
        "presence_by_code": dict(payload.get("presence_by_code") or {}),
        "coverage_count": len(covered),
        "coverage_ratio": round(len(covered) / len(codes), 6),
        "returned_record_count": int(payload.get("returned_record_count") or 0),
        "request_timed_out": bool(worker_result.get("request_timed_out")),
        "worker_terminated": bool(worker_result.get("worker_terminated")),
        "error_code": str(worker_result.get("error_code") or ""),
        "error": str(worker_result.get("error") or "")[:500],
        "provider_elapsed_ms": provider_ms,
        "end_to_end_elapsed_ms": round(e2e_ms, 3),
        "process_overhead_ms": (
            round(max(0.0, e2e_ms - provider_ms), 3)
            if provider_ms is not None
            else None
        ),
        "source_versions": sorted(payload.get("source_versions") or []),
        "provider_raw_hash": str(payload.get("provider_raw_hash") or ""),
        "raw_response_hashes": sorted(
            payload.get("raw_response_hashes") or []
        ),
        "signatures": payload.get("signatures") or {},
        "minute_record_count_by_code": (
            payload.get("minute_record_count_by_code") or {}
        ),
        "canonical_minute_hash_by_code": (
            payload.get("canonical_minute_hash_by_code") or {}
        ),
    }


def _summarize_endpoint(
    endpoint: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    expected_codes: list[str],
    recommendation_max_ms: int,
) -> dict[str, Any]:
    latencies = [float(row["end_to_end_elapsed_ms"]) for row in runs]
    overheads = [
        float(row["process_overhead_ms"])
        for row in runs
        if row.get("process_overhead_ms") is not None
    ]
    complete = [
        row for row in runs if _is_screening_survivor(row, expected_codes)
    ]
    timeout_count = sum(row["request_timed_out"] for row in runs)
    error_count = sum(bool(row["error_code"]) for row in runs)
    maximum = max(latencies) if latencies else None
    eligible = bool(runs) and (
        len(complete) == len(runs)
        and timeout_count == 0
        and error_count == 0
        and maximum is not None
        and maximum <= float(recommendation_max_ms)
        and maximum <= RECOMMENDATION_MAX_ELAPSED_MS
    )
    return {
        "endpoint": dict(endpoint),
        "round_count": len(runs),
        "successful_round_count": len(complete),
        "complete_coverage_round_count": len(complete),
        "timeout_count": timeout_count,
        "error_count": error_count,
        "latency_ms": {
            "p50": _nearest_rank(latencies, 50),
            "p95": _nearest_rank(latencies, 95),
            "max": round(maximum, 3) if maximum is not None else None,
        },
        "process_overhead_ms": {
            "p50": _nearest_rank(overheads, 50),
            "p95": _nearest_rank(overheads, 95),
            "max": round(max(overheads), 3) if overheads else None,
        },
        "recommendation_eligible": eligible,
        "runs": runs,
    }


def _compare_offsets(
    endpoint: dict[str, Any],
    codes: list[str],
    *,
    data_trade_date: str,
    deadline_ms: int,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]],
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    runs = {
        str(offset): _run_endpoint(
            endpoint,
            codes,
            data_trade_date=data_trade_date,
            deadline_ms=deadline_ms,
            worker_runner=worker_runner,
            operation="benchmark_minute",
            minute_offset=offset,
            clock=clock,
        )
        for offset in (FORMAL_MINUTE_OFFSET, COMPARISON_MINUTE_OFFSET)
    }
    incomplete_reasons = []
    for offset, row in runs.items():
        incomplete_reasons.extend(
            _offset_run_incomplete_reasons(
                row,
                expected_codes=codes,
                data_trade_date=data_trade_date,
                offset=offset,
            )
        )
    complete = not incomplete_reasons
    formal = runs[str(FORMAL_MINUTE_OFFSET)]
    comparison = runs[str(COMPARISON_MINUTE_OFFSET)]
    return {
        "status": (
            "OFFSET_COMPARISON_COMPLETED"
            if complete
            else "OFFSET_COMPARISON_INCOMPLETE"
        ),
        "audit_only": True,
        "formal_default_unchanged": True,
        "incomplete_reasons": sorted(set(incomplete_reasons)),
        "runs": runs,
        "same_record_counts": bool(
            complete
            and formal["minute_record_count_by_code"]
            == comparison["minute_record_count_by_code"]
        ),
        "same_1450_ohlcv_signatures": bool(
            complete and formal["signatures"] == comparison["signatures"]
        ),
        "same_canonical_hashes": bool(
            complete
            and formal["canonical_minute_hash_by_code"]
            == comparison["canonical_minute_hash_by_code"]
        ),
    }


def _offset_run_incomplete_reasons(
    row: dict[str, Any],
    *,
    expected_codes: list[str],
    data_trade_date: str,
    offset: str,
) -> list[str]:
    prefix = f"offset_{offset}"
    reasons = []
    if not row.get("ok"):
        reasons.append(f"{prefix}:worker_failed")
    if row.get("requested_codes") != expected_codes:
        reasons.append(f"{prefix}:requested_codes_mismatch")
    if row.get("covered_codes") != expected_codes:
        reasons.append(f"{prefix}:coverage_incomplete")
    presence = row.get("presence_by_code") or {}
    if sorted(presence) != expected_codes:
        reasons.append(f"{prefix}:presence_codes_mismatch")
    for code in expected_codes:
        if presence.get(code) is not True:
            reasons.append(f"{prefix}:1450_presence_missing:{code}")
    signatures = row.get("signatures") or {}
    if sorted(signatures) != expected_codes:
        reasons.append(f"{prefix}:signature_codes_mismatch")
    for code in expected_codes:
        signature = signatures.get(code) or {}
        event = parse_cn_datetime(signature.get("event_time"))
        if (
            event is None
            or event.date().isoformat() != data_trade_date
            or event.time().replace(tzinfo=None) != datetime_time(14, 50)
        ):
            reasons.append(f"{prefix}:signature_time_invalid:{code}")
    return reasons


def _empty_offset_comparison(reason: str = "") -> dict[str, Any]:
    return {
        "status": "NOT_RUN",
        "audit_only": True,
        "formal_default_unchanged": True,
        "incomplete_reasons": [reason] if reason else [],
        "runs": {},
        "same_record_counts": False,
        "same_1450_ohlcv_signatures": False,
        "same_canonical_hashes": False,
    }


def _is_screening_survivor(
    row: dict[str, Any],
    expected_codes: list[str],
) -> bool:
    endpoint_id = str((row.get("endpoint") or {}).get("id") or "")
    return bool(
        row.get("ok") is True
        and row.get("requested_codes") == expected_codes
        and row.get("covered_codes") == expected_codes
        and row.get("endpoint_id") == endpoint_id
        and not row.get("request_timed_out")
        and not row.get("worker_terminated")
        and not row.get("error_code")
        and not row.get("error")
    )


def _screening_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        float(row["end_to_end_elapsed_ms"]),
        str((row.get("endpoint") or {}).get("id") or ""),
    )


def _deduplicate_endpoints(
    values: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for value in values:
        host = str(value.get("host") or "").strip()
        try:
            port = int(value.get("port") or 0)
        except (TypeError, ValueError):
            continue
        if not host or port <= 0 or (host, port) in seen:
            continue
        seen.add((host, port))
        name = str(value.get("name") or "").strip()
        result.append(
            {
                "id": str(
                    value.get("id") or f"{name or 'mootdx'}@{host}:{port}"
                ),
                "name": name,
                "host": host,
                "port": port,
            }
        )
    return result


def _nearest_rank(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(len(ordered) * percentile / 100.0))
    return round(ordered[rank - 1], 3)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trade_date_text(value: str | date | None) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def _as_cn(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)
