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
from overnight_quant.data.probe_v2_contract import (
    validate_probe_v2_semantics,
)


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

    samples_value = payload.get("samples")
    samples = (
        [item for item in samples_value if isinstance(item, dict)]
        if isinstance(samples_value, list)
        else []
    )
    if samples_value is not None and (
        not isinstance(samples_value, list)
        or len(samples) != len(samples_value)
    ):
        errors.append("minute_probe_samples_invalid")
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
    preflight_value = payload.get("source_preflight")
    source_preflight = (
        preflight_value if isinstance(preflight_value, dict) else {}
    )
    if preflight_value is not None and not isinstance(preflight_value, dict):
        errors.append("probe_source_preflight_invalid")
    audit_summary, audit_errors = _normalized_audit_summary(payload)
    if schema_version == PROBE_EVIDENCE_SCHEMA_V2:
        errors.extend(audit_errors)
    if schema_version == PROBE_EVIDENCE_SCHEMA_V2:
        if not source_preflight:
            errors.append("probe_source_preflight_missing")
        errors.extend(
            validate_probe_v2_semantics(
                payload,
                source=normalized_source,
                tracked_codes=(payload.get("tracked_codes") or []),
            )
        )
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

    transaction_value = payload.get("transaction_evidence")
    transaction = (
        transaction_value if isinstance(transaction_value, dict) else {}
    )
    attribution_value = payload.get("transaction_attribution")
    attribution = (
        attribution_value if isinstance(attribution_value, dict) else {}
    )
    if transaction_value is not None and not isinstance(
        transaction_value,
        dict,
    ):
        errors.append("transaction_evidence_invalid")
    if attribution_value is not None and not isinstance(
        attribution_value,
        dict,
    ):
        errors.append("transaction_attribution_invalid")
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
        except (KeyError, TypeError, ValueError):
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


def _normalized_audit_summary(
    payload: dict[str, Any],
) -> tuple[dict[str, int], list[str]]:
    summary = {}
    errors = []
    for key in (
        "late_start_count",
        "deadline_exceeded_count",
        "missed_sample_count",
        "late_record_count",
    ):
        value = payload.get(key)
        try:
            number = float(value or 0)
        except (TypeError, ValueError):
            number = -1.0
        if number < 0 or not number.is_integer():
            errors.append(f"probe_audit_summary_invalid:{key}")
            summary[key] = 0
        else:
            summary[key] = int(number)
    return summary, errors
