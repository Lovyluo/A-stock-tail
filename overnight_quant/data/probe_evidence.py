from __future__ import annotations

from typing import Any

from overnight_quant.data.minute_label_probe import (
    PROBE_EVIDENCE_SCHEMA_V1,
    PROBE_EVIDENCE_SCHEMA_V2,
    compute_probe_evidence_hash,
)
from overnight_quant.data.minute_probe_sources import (
    normalize_probe_source,
)
from overnight_quant.data.transaction_attribution import (
    attribute_mootdx_minute_intervals,
    compute_combined_evidence_hash,
    compute_transaction_evidence_hash,
)
from overnight_quant.data.point_in_time import stable_hash


def verify_probe_evidence(
    payload: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    normalized_source = normalize_probe_source(source)
    errors = []
    schema_version = str(
        payload.get("probe_evidence_schema_version")
        or PROBE_EVIDENCE_SCHEMA_V1
    ).strip().lower()
    if schema_version not in {
        PROBE_EVIDENCE_SCHEMA_V1,
        PROBE_EVIDENCE_SCHEMA_V2,
    }:
        errors.append(
            f"probe_evidence_schema_unsupported:{schema_version}"
        )
    payload_source = str(payload.get("source") or "").strip().lower()
    if payload_source != normalized_source:
        errors.append(
            "probe_source_mismatch:"
            f"{payload_source or '<empty>'}:{normalized_source}"
        )

    samples = list(payload.get("samples") or [])
    codes = sorted(
        str(code).strip().zfill(6)
        for code in (payload.get("tracked_codes") or [])
        if str(code).strip()
    )
    preflight_failed_evidence = (
        schema_version == PROBE_EVIDENCE_SCHEMA_V2
        and payload.get("status") == "SOURCE_PREFLIGHT_FAILED"
    )
    if not samples and not preflight_failed_evidence:
        errors.append("minute_probe_samples_missing")
    if not codes:
        errors.append("minute_probe_codes_missing")
    sample_sources = {
        str(sample.get("probe_source") or "").strip().lower()
        for sample in samples
    }
    if samples and sample_sources != {normalized_source}:
        errors.append("minute_probe_sources_mixed_or_mismatched")
    source_preflight = dict(payload.get("source_preflight") or {})
    audit_summary = {
        key: int(payload.get(key) or 0)
        for key in (
            "late_start_count",
            "deadline_exceeded_count",
            "missed_sample_count",
            "late_record_count",
        )
    }
    if schema_version == PROBE_EVIDENCE_SCHEMA_V2:
        if not source_preflight:
            errors.append("probe_source_preflight_missing")
        if preflight_failed_evidence and source_preflight.get(
            "status"
        ) != "SOURCE_PREFLIGHT_FAILED":
            errors.append("probe_source_preflight_status_mismatch")
        errors.extend(_v2_audit_errors(samples, audit_summary))
    try:
        expected_minute_hash = compute_probe_evidence_hash(
            samples,
            codes,
            source=normalized_source,
            schema_version=schema_version,
            source_preflight=source_preflight,
            audit_summary=audit_summary,
        )
    except ValueError:
        expected_minute_hash = ""
    actual_minute_hash = str(
        payload.get("probe_evidence_hash") or ""
    )
    if actual_minute_hash != expected_minute_hash:
        errors.append("minute_probe_evidence_hash_drift")

    transaction = dict(payload.get("transaction_evidence") or {})
    attribution = dict(payload.get("transaction_attribution") or {})
    expected_transaction_hash = ""
    expected_combined_hash = ""
    if transaction or attribution:
        transaction_source = str(
            transaction.get("source") or ""
        ).strip().lower()
        attribution_source = str(
            attribution.get("source") or ""
        ).strip().lower()
        if transaction_source != normalized_source:
            errors.append("transaction_source_mismatch")
        if attribution_source != normalized_source:
            errors.append("attribution_source_mismatch")
        try:
            expected_transaction_hash = (
                compute_transaction_evidence_hash(
                    transaction,
                    source=normalized_source,
                )
            )
        except ValueError:
            expected_transaction_hash = ""
        if str(payload.get("transaction_evidence_hash") or "") != (
            expected_transaction_hash
        ):
            errors.append("transaction_evidence_hash_drift")
        try:
            if normalized_source == "mootdx":
                expected_attribution = (
                    attribute_mootdx_minute_intervals(
                        payload,
                        transaction,
                    )
                )
                expected_combined_hash = str(
                    expected_attribution.get(
                        "combined_evidence_hash"
                    )
                    or ""
                )
                if stable_hash(attribution) != stable_hash(
                    expected_attribution
                ):
                    errors.append(
                        "transaction_attribution_derivation_drift"
                    )
            else:
                expected_combined_hash = compute_combined_evidence_hash(
                    attribution,
                    source=normalized_source,
                )
        except (KeyError, ValueError):
            expected_combined_hash = ""
        if str(payload.get("combined_evidence_hash") or "") != (
            expected_combined_hash
        ):
            errors.append("combined_evidence_hash_drift")

    unique_errors = sorted(set(errors))
    return {
        "status": (
            "PROBE_EVIDENCE_VERIFIED"
            if not unique_errors
            else "PROBE_EVIDENCE_INVALID"
        ),
        "execution_ok": True,
        "data_ready": False,
        "source": normalized_source,
        "probe_evidence_schema_version": schema_version,
        "probe_evidence_hash": expected_minute_hash,
        "transaction_evidence_hash": expected_transaction_hash,
        "combined_evidence_hash": expected_combined_hash,
        "errors": unique_errors,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _v2_audit_errors(
    samples: list[dict[str, Any]],
    summary: dict[str, int],
) -> list[str]:
    required_fields = (
        "target_at",
        "request_started_at",
        "request_completed_at",
        "schedule_lag_ms",
        "completion_lag_ms",
        "request_deadline_ms",
        "request_timed_out",
        "sample_window_missed",
        "worker_terminated",
        "error_code",
    )
    errors = []
    for sample in samples:
        clock = str(sample.get("target_at") or "")[11:19]
        for field in required_fields:
            if field not in sample:
                errors.append(
                    f"probe_v2_audit_field_missing:{clock}:{field}"
                )
    expected = {
        "late_start_count": sum(
            float(sample.get("schedule_lag_ms") or 0) > 2000
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
        "late_record_count": sum(
            int(sample.get("returned_record_count") or 0)
            for sample in samples
            if not sample.get("error")
            and float(sample.get("completion_lag_ms") or 0)
            > float(sample.get("request_deadline_ms") or 0)
        ),
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            errors.append(f"probe_audit_summary_mismatch:{key}")
    return errors
