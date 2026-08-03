from __future__ import annotations

from typing import Any

from overnight_quant.data.minute_label_probe import (
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
    if not samples:
        errors.append("minute_probe_samples_missing")
    if not codes:
        errors.append("minute_probe_codes_missing")
    sample_sources = {
        str(sample.get("probe_source") or "").strip().lower()
        for sample in samples
    }
    if sample_sources != {normalized_source}:
        errors.append("minute_probe_sources_mixed_or_mismatched")
    try:
        expected_minute_hash = compute_probe_evidence_hash(
            samples,
            codes,
            source=normalized_source,
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
        "probe_evidence_hash": expected_minute_hash,
        "transaction_evidence_hash": expected_transaction_hash,
        "combined_evidence_hash": expected_combined_hash,
        "errors": unique_errors,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
