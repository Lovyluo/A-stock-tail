from __future__ import annotations

from datetime import date, datetime, time
import json
import os
from pathlib import Path
import tempfile
import time as time_module
from typing import Any, Callable

from overnight_quant.data.close_time_contract import (
    MINUTE_LABEL_END,
    MINUTE_LABEL_START,
    MINUTE_LABEL_UNVERIFIED,
    build_close_time_contract,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_probe_sources import (
    MOOTDX_TRANSACTION_SOURCE_VERSION,
    PROBE_SOURCE_EASTMONEY,
    PROBE_SOURCE_MOOTDX,
    build_minute_probe_collector,
    mootdx_server_candidates,
    normalize_probe_source,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.probe_worker_process import (
    WORKER_TIMEOUT_ERROR,
    run_probe_worker_process,
)
from overnight_quant.data.transaction_attribution import (
    ATTRIBUTION_INCONCLUSIVE,
    attribute_mootdx_minute_intervals,
    compute_transaction_evidence_hash,
)


PROBE_CLOCKS = (
    time(14, 49, 55),
    time(14, 50, 5),
    time(14, 50, 30),
    time(14, 51, 5),
)
MAX_PROBE_START_LAG_SECONDS = 2.0
MINUTE_REQUEST_DEADLINE_MS = 2000
TRANSACTION_REQUEST_DEADLINE_MS = 5000
PROBE_EVIDENCE_SCHEMA_V1 = "v1"
PROBE_EVIDENCE_SCHEMA_V2 = "v2"


def minute_1450_signature(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    signatures: dict[str, dict[str, Any]] = {}
    for row in records:
        if str(row.get("data_type") or "") != "minute_bar":
            continue
        if str(row.get("event_time") or "")[11:16] != "14:50":
            continue
        payload = row.get("payload") or {}
        code = str(payload.get("code") or "").zfill(6)
        values = {
            field: payload.get(field)
            for field in (
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "trade_count",
            )
        }
        signatures[code] = {
            "ohlcv": values,
            "ohlcv_hash": stable_hash(values),
            "volume_unit": str(
                (payload.get("field_units") or {}).get("volume")
                or ""
            ),
            "source": row.get("source"),
            "source_version": row.get("source_version"),
            "raw_hash": row.get("raw_hash"),
        }
    return dict(sorted(signatures.items()))


def classify_minute_label_samples(
    samples: list[dict[str, Any]],
    *,
    required_codes: list[str] | None = None,
    source: str | None = None,
    schema_version: str | None = None,
    source_preflight: dict[str, Any] | None = None,
    audit_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered = sorted(
        samples,
        key=lambda item: (
            item.get("target_at")
            or item.get("sampled_at")
            or ""
        ),
    )
    expected = [
        "14:49:55",
        "14:50:05",
        "14:50:30",
        "14:51:05",
    ]
    by_clock = {
        str(
            item.get("target_at")
            or item.get("sampled_at")
            or ""
        )[11:19]: item
        for item in ordered
    }
    sample_sources = sorted(
        {
            str(item.get("probe_source") or "").strip().lower()
            for item in ordered
            if str(item.get("probe_source") or "").strip()
        }
    )
    resolved_source = str(source or "").strip().lower()
    if not resolved_source and len(sample_sources) == 1:
        resolved_source = sample_sources[0]
    if len(sample_sources) > 1 or (
        resolved_source
        and sample_sources
        and sample_sources != [resolved_source]
    ):
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=["probe_sources_mixed_or_mismatched"],
            source=resolved_source,
            schema_version=schema_version,
            source_preflight=source_preflight,
            audit_summary=audit_summary,
        )
    missing_points = [
        value for value in expected if value not in by_clock
    ]
    if missing_points:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=[
                "required_probe_points_missing:"
                + ",".join(missing_points)
            ],
            source=resolved_source,
            schema_version=schema_version,
            source_preflight=source_preflight,
            audit_summary=audit_summary,
        )

    tracked_codes = sorted(
        {
            str(code).zfill(6)
            for code in (
                required_codes
                or ordered[0].get("requested_codes")
                or []
            )
            if str(code).strip()
        }
    )
    if not tracked_codes:
        tracked_codes = sorted(
            set().union(
                *(
                    set(
                        (
                            by_clock[clock].get("signatures")
                            or {}
                        ).keys()
                    )
                    for clock in expected
                )
            )
        )
    evidence_hash = _probe_evidence_hash(
        ordered,
        tracked_codes,
        source=resolved_source,
        schema_version=schema_version,
        source_preflight=source_preflight,
        audit_summary=audit_summary,
    )
    timing_errors = _probe_timing_errors(by_clock, expected)
    if timing_errors:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=timing_errors,
            tracked_codes=tracked_codes,
            probe_evidence_hash=evidence_hash,
            source=resolved_source,
            schema_version=schema_version,
            source_preflight=source_preflight,
            audit_summary=audit_summary,
        )
    coverage_errors = []
    for clock in expected:
        covered = {
            str(code).zfill(6)
            for code in (
                by_clock[clock].get("covered_codes") or []
            )
        }
        missing = sorted(set(tracked_codes) - covered)
        if missing:
            coverage_errors.append(
                f"probe_stock_coverage_missing:{clock}:"
                + ",".join(missing)
            )
    if coverage_errors:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=coverage_errors,
            tracked_codes=tracked_codes,
            probe_evidence_hash=evidence_hash,
            source=resolved_source,
            schema_version=schema_version,
            source_preflight=source_preflight,
            audit_summary=audit_summary,
        )
    if not tracked_codes:
        return _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=ordered,
            reasons=["probe_required_codes_missing"],
            probe_evidence_hash=evidence_hash,
            source=resolved_source,
            schema_version=schema_version,
            source_preflight=source_preflight,
            audit_summary=audit_summary,
        )

    first_present = []
    later_present = []
    changed_during_1450 = []
    stable_after_1450 = []
    stable_from_first = []
    for code in tracked_codes:
        signatures = [
            (by_clock[clock].get("signatures") or {}).get(code)
            for clock in expected
        ]
        first_present.append(signatures[0] is not None)
        later_present.append(
            all(item is not None for item in signatures[1:])
        )
        if all(item is not None for item in signatures[1:]):
            hashes = [
                str(item["ohlcv_hash"])
                for item in signatures[1:]
                if item is not None
            ]
            changed_during_1450.append(hashes[0] != hashes[1])
            stable_after_1450.append(hashes[1] == hashes[2])
        else:
            changed_during_1450.append(False)
            stable_after_1450.append(False)
        if all(item is not None for item in signatures):
            all_hashes = [
                str(item["ohlcv_hash"])
                for item in signatures
                if item is not None
            ]
            stable_from_first.append(len(set(all_hashes)) == 1)
        else:
            stable_from_first.append(False)

    if (
        all(not value for value in first_present)
        and all(later_present)
        and all(changed_during_1450)
        and all(stable_after_1450)
    ):
        semantics = MINUTE_LABEL_START
        conclusion = "VERIFIED"
        reasons = [
            "minute_1450_absent_before_1450_changed_inside_minute_and_stabilized_after_1451"
        ]
    elif (
        all(first_present)
        and all(later_present)
        and all(stable_from_first)
    ):
        semantics = MINUTE_LABEL_END
        conclusion = "VERIFIED"
        reasons = [
            "minute_1450_present_before_1450_and_stable_through_1451"
        ]
    else:
        semantics = MINUTE_LABEL_UNVERIFIED
        conclusion = "INCONCLUSIVE"
        reasons = [
            "minute_1450_presence_or_change_pattern_inconclusive"
        ]
    return _probe_result(
        semantics,
        conclusion,
        samples=ordered,
        reasons=reasons,
        tracked_codes=tracked_codes,
        probe_evidence_hash=evidence_hash,
        source=resolved_source,
        schema_version=schema_version,
        source_preflight=source_preflight,
        audit_summary=audit_summary,
    )


def run_scheduled_minute_label_probe(
    codes: list[str],
    *,
    trade_date: str | date | None = None,
    source: str = PROBE_SOURCE_EASTMONEY,
    collectors: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]] | None = None,
    endpoint_candidates: list[dict[str, Any]] | None = None,
    request_deadline_ms: int = MINUTE_REQUEST_DEADLINE_MS,
    transaction_deadline_ms: int = TRANSACTION_REQUEST_DEADLINE_MS,
) -> dict[str, Any]:
    normalized_source = normalize_probe_source(source)
    runtime_clock = clock or (lambda: datetime.now(CN_TZ))
    runtime_sleep = sleep or time_module.sleep
    runtime_monotonic = monotonic or time_module.monotonic
    runtime_worker = worker_runner or run_probe_worker_process
    runtime_codes = sorted(
        {
            str(code).strip().zfill(6)
            for code in codes
            if str(code).strip()
        }
    )
    current = runtime_clock()
    day = (
        current.date()
        if trade_date is None
        else (
            trade_date
            if isinstance(trade_date, date)
            else date.fromisoformat(str(trade_date))
        )
    )
    targets = [
        datetime.combine(day, value, tzinfo=CN_TZ)
        for value in PROBE_CLOCKS
    ]
    runtime_collectors = collectors
    process_isolated = runtime_collectors is None
    if runtime_collectors is not None:
        collector_source = str(
            getattr(
                runtime_collectors,
                "probe_source",
                normalized_source,
            )
            or ""
        ).strip().lower()
        if collector_source != normalized_source:
            raise ValueError(
                "minute_probe_collector_source_mismatch:"
                f"{collector_source}:{normalized_source}"
            )
        runtime_codes = list(runtime_collectors.codes)

    source_preflight = _source_preflight(
        normalized_source,
        runtime_codes,
        observed_at=current,
        process_isolated=process_isolated,
        worker_runner=runtime_worker,
        endpoint_candidates=endpoint_candidates,
        deadline_ms=int(request_deadline_ms),
        clock=runtime_clock,
        monotonic=runtime_monotonic,
    )
    if source_preflight.get("status") == "SOURCE_PREFLIGHT_FAILED":
        audit_summary = _probe_audit_summary([])
        evidence_hash = compute_probe_evidence_hash(
            [],
            runtime_codes,
            source=normalized_source,
            schema_version=PROBE_EVIDENCE_SCHEMA_V2,
            source_preflight=source_preflight,
            audit_summary=audit_summary,
        )
        result = _probe_result(
            MINUTE_LABEL_UNVERIFIED,
            "INCONCLUSIVE",
            samples=[],
            reasons=["source_preflight_failed"],
            tracked_codes=runtime_codes,
            probe_evidence_hash=evidence_hash,
            source=normalized_source,
            schema_version=PROBE_EVIDENCE_SCHEMA_V2,
            source_preflight=source_preflight,
            audit_summary=audit_summary,
        )
        result.update(
            {
                "status": "SOURCE_PREFLIGHT_FAILED",
                "execution_ok": True,
                "data_ready": False,
                "trade_date": day.isoformat(),
                "requires_manual_review": True,
                **audit_summary,
                "candidates": [],
                "tickets": [],
                "orders": [],
            }
        )
        return result

    selected_endpoint = source_preflight.get("selected_endpoint") or None
    samples = []
    transaction_evidence: dict[str, Any] = {}
    try:
        for target in targets:
            wait_seconds = max(
                0.0,
                (target - runtime_clock()).total_seconds(),
            )
            if wait_seconds:
                runtime_sleep(wait_seconds)
            request_started = runtime_clock()
            schedule_lag_ms = round(
                max(0.0, (request_started - target).total_seconds() * 1000),
                3,
            )
            if schedule_lag_ms > MAX_PROBE_START_LAG_SECONDS * 1000:
                samples.append(
                    _empty_probe_sample(
                        source=normalized_source,
                        target=target,
                        request_started=request_started,
                        request_completed=request_started,
                        codes=runtime_codes,
                        schedule_lag_ms=schedule_lag_ms,
                        request_deadline_ms=int(request_deadline_ms),
                        error_code="SAMPLE_WINDOW_MISSED",
                        error="SAMPLE_WINDOW_MISSED",
                        sample_window_missed=True,
                    )
                )
                continue
            if process_isolated:
                worker_result = runtime_worker(
                    {
                        "operation": "minute",
                        "source": normalized_source,
                        "codes": runtime_codes,
                        "observed_at": request_started.isoformat(),
                        "endpoint": selected_endpoint,
                        "provider_timeout_seconds": (
                            int(request_deadline_ms) / 1000.0
                        ),
                    },
                    int(request_deadline_ms),
                )
            else:
                worker_result = _run_inline_minute_request(
                    runtime_collectors,
                    request_started,
                )
            request_completed = runtime_clock()
            samples.append(
                _sample_from_worker_result(
                    worker_result,
                    source=normalized_source,
                    target=target,
                    request_started=request_started,
                    request_completed=request_completed,
                    codes=runtime_codes,
                    schedule_lag_ms=schedule_lag_ms,
                    request_deadline_ms=int(request_deadline_ms),
                    endpoint_id=str(
                        source_preflight.get("endpoint_id") or ""
                    ),
                )
            )
        if normalized_source == PROBE_SOURCE_MOOTDX:
            transaction_evidence = _collect_transaction_evidence(
                collectors=runtime_collectors,
                process_isolated=process_isolated,
                worker_runner=runtime_worker,
                source=normalized_source,
                codes=runtime_codes,
                endpoint=selected_endpoint,
                observed_at=runtime_clock(),
                deadline_ms=int(transaction_deadline_ms),
                clock=runtime_clock,
                monotonic=runtime_monotonic,
                trade_date=day,
            )
    finally:
        if runtime_collectors is not None:
            close = getattr(runtime_collectors, "close", None)
            if callable(close):
                close()
    audit_summary = _probe_audit_summary(samples)
    result = classify_minute_label_samples(
        samples,
        required_codes=runtime_codes,
        source=normalized_source,
        schema_version=PROBE_EVIDENCE_SCHEMA_V2,
        source_preflight=source_preflight,
        audit_summary=audit_summary,
    )
    result["source_role"] = (
        "qualification_candidate"
        if normalized_source == PROBE_SOURCE_MOOTDX
        else "audit_only"
    )
    if normalized_source == PROBE_SOURCE_MOOTDX:
        if transaction_evidence:
            attribution = attribute_mootdx_minute_intervals(
                result,
                transaction_evidence,
            )
        else:
            attribution = {
                "status": ATTRIBUTION_INCONCLUSIVE,
                "source": normalized_source,
                "reasons": ["transaction_evidence_missing"],
                "per_stock": {},
                "all_stocks_final": False,
                "provisional_time_contract": {},
                "combined_evidence_hash": "",
            }
        result["transaction_evidence"] = transaction_evidence
        result["transaction_attribution"] = attribution
        result["transaction_evidence_hash"] = str(
            transaction_evidence.get("transaction_evidence_hash")
            or ""
        )
        result["combined_evidence_hash"] = str(
            attribution.get("combined_evidence_hash") or ""
        )
        if attribution.get("status") != ATTRIBUTION_INCONCLUSIVE:
            result["status"] = "MINUTE_LABEL_PROVISIONAL"
            result["minute_label_semantics"] = attribution["status"]
            result["minute_label_validation_status"] = (
                "PROVISIONAL_TRANSACTION_ATTRIBUTION"
            )
            result["recommended_time_contract"] = attribution[
                "provisional_time_contract"
            ]
    all_source_versions = sorted(
        {
            version
            for sample in samples
            for version in (sample.get("source_versions") or [])
            if version
        }
    )
    result.update(
        {
            "execution_ok": True,
            "data_ready": False,
            "trade_date": day.isoformat(),
            "source": normalized_source,
            "source_versions": all_source_versions,
            "requires_manual_review": True,
            **audit_summary,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }
    )
    return result


def _source_preflight(
    source: str,
    codes: list[str],
    *,
    observed_at: datetime,
    process_isolated: bool,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]],
    endpoint_candidates: list[dict[str, Any]] | None,
    deadline_ms: int,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
) -> dict[str, Any]:
    if not process_isolated:
        now = clock().isoformat(timespec="milliseconds")
        return {
            "status": "INJECTED_COLLECTOR",
            "source": source,
            "started_at": now,
            "completed_at": now,
            "endpoint_id": "injected",
            "covered_codes": list(codes),
            "coverage_ratio": 1.0 if codes else 0.0,
            "request_elapsed_ms": 0.0,
            "attempts": [],
        }
    if source != PROBE_SOURCE_MOOTDX:
        now = clock().isoformat(timespec="milliseconds")
        return {
            "status": "NOT_REQUIRED",
            "source": source,
            "started_at": now,
            "completed_at": now,
            "endpoint_id": "",
            "covered_codes": [],
            "coverage_ratio": 0.0,
            "request_elapsed_ms": 0.0,
            "attempts": [],
        }

    candidates = list(
        endpoint_candidates
        if endpoint_candidates is not None
        else mootdx_server_candidates()
    )
    preflight_started = clock()
    total_started = monotonic()
    attempts = []
    for endpoint in candidates:
        request_started = clock()
        started_monotonic = monotonic()
        worker_result = worker_runner(
            {
                "operation": "preflight",
                "source": source,
                "codes": codes,
                "observed_at": observed_at.isoformat(),
                "endpoint": endpoint,
                "provider_timeout_seconds": deadline_ms / 1000.0,
            },
            deadline_ms,
        )
        request_completed = clock()
        payload = worker_result.get("payload") or {}
        effective_started = _sample_datetime(
            payload.get("worker_request_started_at")
        ) or request_started
        effective_completed = _sample_datetime(
            payload.get("worker_request_completed_at")
        ) or request_completed
        covered = sorted(payload.get("covered_codes") or [])
        successful = worker_result.get("ok") is True and covered == codes
        attempt = {
            "endpoint_id": str(endpoint.get("id") or ""),
            "request_started_at": effective_started.isoformat(
                timespec="milliseconds"
            ),
            "request_completed_at": effective_completed.isoformat(
                timespec="milliseconds"
            ),
            "request_elapsed_ms": float(
                payload.get("worker_request_elapsed_ms")
                or round(
                    (monotonic() - started_monotonic) * 1000,
                    3,
                )
            ),
            "request_deadline_ms": deadline_ms,
            "request_timed_out": bool(
                worker_result.get("request_timed_out")
            ),
            "worker_terminated": bool(
                worker_result.get("worker_terminated")
            ),
            "covered_codes": covered,
            "coverage_ratio": (
                len(covered) / len(codes) if codes else 0.0
            ),
            "provider_raw_hash": str(
                payload.get("provider_raw_hash") or ""
            ),
            "raw_response_hashes": sorted(
                payload.get("raw_response_hashes") or []
            ),
            "source_versions": sorted(
                payload.get("source_versions") or []
            ),
            "error_code": str(
                worker_result.get("error_code") or ""
            ),
        }
        attempts.append(attempt)
        if successful:
            return {
                "status": "SOURCE_PREFLIGHT_READY",
                "source": source,
                "started_at": preflight_started.isoformat(
                    timespec="milliseconds"
                ),
                "completed_at": effective_completed.isoformat(
                    timespec="milliseconds"
                ),
                "endpoint_id": str(endpoint.get("id") or ""),
                "selected_endpoint": endpoint,
                "covered_codes": covered,
                "coverage_ratio": 1.0,
                "request_elapsed_ms": round(
                    (monotonic() - total_started) * 1000,
                    3,
                ),
                "attempts": attempts,
            }
    completed = clock()
    return {
        "status": "SOURCE_PREFLIGHT_FAILED",
        "source": source,
        "started_at": preflight_started.isoformat(timespec="milliseconds"),
        "completed_at": completed.isoformat(timespec="milliseconds"),
        "endpoint_id": "",
        "selected_endpoint": None,
        "covered_codes": [],
        "coverage_ratio": 0.0,
        "request_elapsed_ms": round(
            (monotonic() - total_started) * 1000,
            3,
        ),
        "attempts": attempts,
    }


def _run_inline_minute_request(
    collectors: Any,
    request_started: datetime,
) -> dict[str, Any]:
    try:
        batch = collectors.collect_minute_bars(request_started)
    except Exception as exc:
        return {
            "ok": False,
            "error_code": _stable_sample_error_code(exc),
            "error": f"{type(exc).__name__}: {exc}",
            "request_timed_out": False,
            "worker_terminated": False,
        }
    records = list(batch.records or [])
    signatures = minute_1450_signature(records)
    covered_codes = sorted(
        {
            str((row.get("payload") or {}).get("code") or "").zfill(6)
            for row in records
            if str(row.get("data_type") or "") == "minute_bar"
            and (row.get("payload") or {}).get("code")
        }
    )
    return {
        "ok": True,
        "payload": {
            "requested_codes": list(collectors.codes),
            "covered_codes": covered_codes,
            "presence_by_code": {
                code: code in signatures for code in collectors.codes
            },
            "signatures": signatures,
            "raw_response_hashes": sorted(
                {
                    str(row.get("raw_hash") or "")
                    for row in records
                    if row.get("raw_hash")
                }
            ),
            "provider_raw_hash": str(batch.raw_hash or ""),
            "source_versions": sorted(
                {
                    str(row.get("source_version") or "")
                    for row in records
                    if row.get("source_version")
                }
            ),
            "returned_record_count": len(records),
            "endpoint_id": "injected",
        },
        "request_timed_out": False,
        "worker_terminated": False,
        "error_code": "",
        "error": "",
    }


def _sample_from_worker_result(
    worker_result: dict[str, Any],
    *,
    source: str,
    target: datetime,
    request_started: datetime,
    request_completed: datetime,
    codes: list[str],
    schedule_lag_ms: float,
    request_deadline_ms: int,
    endpoint_id: str,
) -> dict[str, Any]:
    payload = worker_result.get("payload") or {}
    signatures = payload.get("signatures") or {}
    effective_started = _sample_datetime(
        payload.get("worker_request_started_at")
    ) or request_started
    effective_completed = _sample_datetime(
        payload.get("worker_request_completed_at")
    ) or request_completed
    effective_schedule_lag_ms = round(
        max(0.0, (effective_started - target).total_seconds() * 1000),
        3,
    )
    completion_lag_ms = round(
        max(0.0, (effective_completed - target).total_seconds() * 1000),
        3,
    )
    effective_elapsed_ms = round(
        max(
            0.0,
            (effective_completed - effective_started).total_seconds()
            * 1000,
        ),
        3,
    )
    error_code = str(worker_result.get("error_code") or "")
    error = str(worker_result.get("error") or "")
    if error_code and not error:
        error = error_code
    return {
        "probe_source": source,
        "target_at": target.isoformat(timespec="seconds"),
        "sampled_at": effective_started.isoformat(timespec="seconds"),
        "request_started_at": effective_started.isoformat(
            timespec="milliseconds"
        ),
        "request_completed_at": effective_completed.isoformat(
            timespec="milliseconds"
        ),
        "schedule_lag_ms": max(
            schedule_lag_ms,
            effective_schedule_lag_ms,
        ),
        "completion_lag_ms": completion_lag_ms,
        "request_deadline_ms": request_deadline_ms,
        "request_elapsed_ms": effective_elapsed_ms,
        "request_timed_out": bool(
            worker_result.get("request_timed_out")
        ),
        "sample_window_missed": False,
        "worker_terminated": bool(
            worker_result.get("worker_terminated")
        ),
        "error_code": error_code,
        "requested_codes": list(codes),
        "covered_codes": sorted(payload.get("covered_codes") or []),
        "presence_by_code": payload.get("presence_by_code")
        or {code: code in signatures for code in codes},
        "signatures": signatures,
        "raw_response_hashes": sorted(
            payload.get("raw_response_hashes") or []
        ),
        "provider_raw_hash": str(
            payload.get("provider_raw_hash") or ""
        ),
        "source_versions": sorted(
            payload.get("source_versions") or []
        ),
        "returned_record_count": int(
            payload.get("returned_record_count") or 0
        ),
        "endpoint_id": str(payload.get("endpoint_id") or endpoint_id),
        "sample_trade_date": target.date().isoformat(),
        "error": error,
    }


def _empty_probe_sample(
    *,
    source: str,
    target: datetime,
    request_started: datetime,
    request_completed: datetime,
    codes: list[str],
    schedule_lag_ms: float,
    request_deadline_ms: int,
    error_code: str,
    error: str,
    sample_window_missed: bool,
) -> dict[str, Any]:
    return {
        "probe_source": source,
        "target_at": target.isoformat(timespec="seconds"),
        "sampled_at": request_started.isoformat(timespec="seconds"),
        "request_started_at": request_started.isoformat(
            timespec="milliseconds"
        ),
        "request_completed_at": request_completed.isoformat(
            timespec="milliseconds"
        ),
        "schedule_lag_ms": schedule_lag_ms,
        "completion_lag_ms": round(
            max(0.0, (request_completed - target).total_seconds() * 1000),
            3,
        ),
        "request_deadline_ms": request_deadline_ms,
        "request_elapsed_ms": 0.0,
        "request_timed_out": error_code == WORKER_TIMEOUT_ERROR,
        "sample_window_missed": sample_window_missed,
        "worker_terminated": error_code == WORKER_TIMEOUT_ERROR,
        "error_code": error_code,
        "requested_codes": list(codes),
        "covered_codes": [],
        "presence_by_code": {code: False for code in codes},
        "signatures": {},
        "raw_response_hashes": [],
        "provider_raw_hash": "",
        "source_versions": [],
        "returned_record_count": 0,
        "endpoint_id": "",
        "sample_trade_date": target.date().isoformat(),
        "error": error,
    }


def _probe_audit_summary(
    samples: list[dict[str, Any]],
) -> dict[str, int]:
    late_record_count = 0
    for sample in samples:
        if (
            not sample.get("error")
            and float(sample.get("completion_lag_ms") or 0)
            > float(sample.get("request_deadline_ms") or 0)
        ):
            late_record_count += int(
                sample.get("returned_record_count") or 0
            )
    return {
        "late_start_count": sum(
            float(sample.get("schedule_lag_ms") or 0)
            > MAX_PROBE_START_LAG_SECONDS * 1000
            for sample in samples
        ),
        "deadline_exceeded_count": sum(
            sample.get("sample_window_missed") is not True
            and (
                sample.get("request_timed_out") is True
                or float(sample.get("completion_lag_ms") or 0)
                > float(sample.get("request_deadline_ms") or 0)
            )
            for sample in samples
        ),
        "missed_sample_count": sum(
            sample.get("sample_window_missed") is True
            for sample in samples
        ),
        "late_record_count": late_record_count,
    }


def _collect_transaction_evidence(
    *,
    collectors: Any | None,
    process_isolated: bool,
    worker_runner: Callable[[dict[str, Any], int], dict[str, Any]],
    source: str,
    codes: list[str],
    endpoint: dict[str, Any] | None,
    observed_at: datetime,
    deadline_ms: int,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    trade_date: date,
) -> dict[str, Any]:
    request_started = clock()
    started_monotonic = monotonic()
    if process_isolated:
        worker_result = worker_runner(
            {
                "operation": "transaction",
                "source": source,
                "codes": codes,
                "observed_at": observed_at.isoformat(),
                "endpoint": endpoint,
                "provider_timeout_seconds": deadline_ms / 1000.0,
            },
            deadline_ms,
        )
    else:
        collect = getattr(
            collectors,
            "collect_transaction_evidence",
            None,
        )
        if not callable(collect):
            return {}
        try:
            worker_result = {
                "ok": True,
                "payload": {
                    "transaction_evidence": collect(observed_at)
                },
                "request_timed_out": False,
                "worker_terminated": False,
                "error_code": "",
                "error": "",
            }
        except Exception as exc:
            worker_result = {
                "ok": False,
                "request_timed_out": False,
                "worker_terminated": False,
                "error_code": _stable_sample_error_code(exc),
                "error": f"{type(exc).__name__}: {exc}",
            }
    request_completed = clock()
    elapsed_ms = round(
        (monotonic() - started_monotonic) * 1000,
        3,
    )
    evidence = dict(
        (worker_result.get("payload") or {}).get(
            "transaction_evidence"
        )
        or {}
    )
    if not evidence:
        evidence = {
            "source": source,
            "source_version": MOOTDX_TRANSACTION_SOURCE_VERSION,
            "endpoint_id": str((endpoint or {}).get("id") or ""),
            "trade_date": trade_date.isoformat(),
            "requested_codes": list(codes),
            "request_started_at": request_started.isoformat(
                timespec="milliseconds"
            ),
            "request_completed_at": request_completed.isoformat(
                timespec="milliseconds"
            ),
            "by_code": {},
            "error": str(worker_result.get("error") or ""),
        }
    evidence.update(
        {
            "request_deadline_ms": deadline_ms,
            "request_elapsed_ms": elapsed_ms,
            "request_timed_out": bool(
                worker_result.get("request_timed_out")
            ),
            "worker_terminated": bool(
                worker_result.get("worker_terminated")
            ),
            "error_code": str(
                worker_result.get("error_code") or ""
            ),
        }
    )
    evidence.setdefault(
        "endpoint_id",
        str((endpoint or {}).get("id") or ""),
    )
    evidence["transaction_evidence_hash"] = (
        compute_transaction_evidence_hash(evidence, source=source)
    )
    return evidence


def _stable_sample_error_code(exc: Exception) -> str:
    text = str(exc or "").lower()
    if "mootdx_minute_bar_empty" in text:
        return "MOOTDX_MINUTE_BAR_EMPTY"
    if "mootdx_trade_date_rows_empty" in text:
        return "MOOTDX_TRADE_DATE_ROWS_EMPTY"
    if "http_request_failed" in text:
        return "HTTP_REQUEST_FAILED"
    return "PROBE_REQUEST_FAILED"


def _probe_result(
    semantics: str,
    conclusion: str,
    *,
    samples: list[dict[str, Any]],
    reasons: list[str],
    tracked_codes: list[str] | None = None,
    probe_evidence_hash: str = "",
    source: str = "",
    schema_version: str | None = None,
    source_preflight: dict[str, Any] | None = None,
    audit_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verified = conclusion == "VERIFIED"
    contract = build_close_time_contract(
        str(samples[0]["sampled_at"])[:10]
        if samples
        else date.today(),
        minute_label_semantics=semantics,
        verified=verified,
        probe_evidence_hash=(
            probe_evidence_hash if verified else ""
        ),
    )
    result = {
        "status": (
            "MINUTE_LABEL_VERIFIED"
            if verified
            else "MINUTE_LABEL_INCONCLUSIVE"
        ),
        "minute_label_semantics": semantics,
        "minute_label_validation_status": conclusion,
        "source": source,
        "tracked_codes": tracked_codes or [],
        "probe_evidence_hash": probe_evidence_hash,
        "reasons": reasons,
        "samples": samples,
        "recommended_time_contract": contract.as_dict(),
    }
    if schema_version:
        result["probe_evidence_schema_version"] = schema_version
        result["source_preflight"] = source_preflight or {}
        result.update(audit_summary or _probe_audit_summary(samples))
    return result


def _probe_timing_errors(
    by_clock: dict[str, dict[str, Any]],
    expected: list[str],
) -> list[str]:
    errors = []
    for clock in expected:
        sample = by_clock[clock]
        target = _sample_datetime(
            sample.get("target_at") or sample.get("sampled_at")
        )
        started = _sample_datetime(
            sample.get("request_started_at")
            or sample.get("sampled_at")
        )
        completed = _sample_datetime(
            sample.get("request_completed_at")
            or sample.get("sampled_at")
        )
        if target is None or started is None or completed is None:
            errors.append(f"probe_timing_missing:{clock}")
        else:
            if completed < started:
                errors.append(f"probe_completion_before_start:{clock}")
            if (
                started - target
            ).total_seconds() > MAX_PROBE_START_LAG_SECONDS:
                errors.append(f"probe_started_late:{clock}")
        if float(sample.get("schedule_lag_ms") or 0) > (
            MAX_PROBE_START_LAG_SECONDS * 1000
        ):
            errors.append(f"probe_started_late:{clock}")
        if sample.get("sample_window_missed") is True:
            errors.append(f"probe_sample_window_missed:{clock}")
        if sample.get("request_timed_out") is True:
            errors.append(f"probe_request_deadline_exceeded:{clock}")
        if sample.get("error"):
            errors.append(f"probe_request_failed:{clock}")
    return errors


def _probe_evidence_hash(
    samples: list[dict[str, Any]],
    tracked_codes: list[str],
    *,
    source: str = "",
    schema_version: str | None = None,
    source_preflight: dict[str, Any] | None = None,
    audit_summary: dict[str, Any] | None = None,
) -> str:
    resolved_schema = schema_version or PROBE_EVIDENCE_SCHEMA_V1
    if resolved_schema not in {
        PROBE_EVIDENCE_SCHEMA_V1,
        PROBE_EVIDENCE_SCHEMA_V2,
    }:
        raise ValueError(
            f"probe_evidence_schema_unsupported:{resolved_schema}"
        )
    evidence = {
        "source": str(source or "").strip().lower(),
        "tracked_codes": list(tracked_codes),
        "samples": [
            {
                "probe_source": str(
                    item.get("probe_source") or source or ""
                ).strip().lower(),
                "target_at": item.get("target_at")
                or item.get("sampled_at"),
                "request_started_at": item.get(
                    "request_started_at"
                )
                or item.get("sampled_at"),
                "request_completed_at": item.get(
                    "request_completed_at"
                )
                or item.get("sampled_at"),
                "request_elapsed_ms": item.get(
                    "request_elapsed_ms"
                ),
                "covered_codes": sorted(
                    item.get("covered_codes") or []
                ),
                "presence_by_code": item.get(
                    "presence_by_code"
                )
                or {
                    code: code
                    in (item.get("signatures") or {})
                    for code in tracked_codes
                },
                "signatures": item.get("signatures") or {},
                "raw_response_hashes": sorted(
                    item.get("raw_response_hashes") or []
                ),
                "provider_raw_hash": item.get(
                    "provider_raw_hash"
                )
                or "",
                "source_versions": sorted(
                    item.get("source_versions") or []
                ),
                "sample_trade_date": item.get(
                    "sample_trade_date"
                )
                or str(
                    item.get("sampled_at") or ""
                )[:10],
                "error": item.get("error") or "",
            }
            for item in samples
        ],
    }
    if resolved_schema == PROBE_EVIDENCE_SCHEMA_V2:
        evidence = {
            "probe_evidence_schema_version": PROBE_EVIDENCE_SCHEMA_V2,
            "source": evidence["source"],
            "tracked_codes": sorted(evidence["tracked_codes"]),
            "source_preflight": source_preflight or {},
            "audit_summary": {
                key: int((audit_summary or {}).get(key) or 0)
                for key in (
                    "late_start_count",
                    "deadline_exceeded_count",
                    "missed_sample_count",
                    "late_record_count",
                )
            },
            "samples": [
                {
                    **sample,
                    "schedule_lag_ms": item.get("schedule_lag_ms"),
                    "completion_lag_ms": item.get(
                        "completion_lag_ms"
                    ),
                    "request_deadline_ms": item.get(
                        "request_deadline_ms"
                    ),
                    "request_timed_out": bool(
                        item.get("request_timed_out")
                    ),
                    "sample_window_missed": bool(
                        item.get("sample_window_missed")
                    ),
                    "worker_terminated": bool(
                        item.get("worker_terminated")
                    ),
                    "error_code": str(
                        item.get("error_code") or ""
                    ),
                    "endpoint_id": str(
                        item.get("endpoint_id") or ""
                    ),
                    "returned_record_count": int(
                        item.get("returned_record_count") or 0
                    ),
                }
                for sample, item in zip(evidence["samples"], samples)
            ],
        }
    return stable_hash(evidence)


def compute_probe_evidence_hash(
    samples: list[dict[str, Any]],
    tracked_codes: list[str],
    *,
    source: str,
    schema_version: str | None = None,
    source_preflight: dict[str, Any] | None = None,
    audit_summary: dict[str, Any] | None = None,
) -> str:
    normalized_source = str(source or "").strip().lower()
    if not normalized_source:
        raise ValueError("evidence_source_required")
    return _probe_evidence_hash(
        samples,
        tracked_codes,
        source=normalized_source,
        schema_version=schema_version,
        source_preflight=source_preflight,
        audit_summary=audit_summary,
    )


def write_probe_json_atomic(
    result: dict[str, Any],
    output: str | Path,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


def _sample_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)
