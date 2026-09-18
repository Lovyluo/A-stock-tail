from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import FIXED_CODES
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_adapters import (
    get_source_adapter_registry,
)
from overnight_quant.data.source_capability_registry import (
    get_source_capability_registry,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_ADAPTER,
    TENCENT_ORIGIN_SOURCE,
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_SOURCE_VERSION,
    compute_tencent_request_hash,
)


SESSION_CONFIRMATION_SCHEMA_VERSION = "market_session_confirmation_v1"
SESSION_CONFIRMATION_VERIFIER_VERSION = (
    "market_session_confirmation_verifier_v1"
)
SESSION_CONFIRMED = "MARKET_SESSION_CONFIRMED"
SESSION_REJECTED = "MARKET_SESSION_CONFIRMATION_REJECTED"
SESSION_OPEN_TIME = time(9, 30)
_SHA256 = frozenset("0123456789abcdef")


def build_market_session_confirmation(
    *,
    trade_date: str,
    records: Iterable[Mapping[str, Any]],
    provider_key: str,
    adapter_execution: Mapping[str, Any],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    normalized_records = sorted(
        (dict(record) for record in records),
        key=lambda row: (
            str((row.get("payload") or {}).get("code") or ""),
            stable_hash(row),
        ),
    )
    errors = _confirmation_errors(
        trade_date=trade_date,
        records=normalized_records,
        provider_key=provider_key,
        adapter_execution=adapter_execution,
        started_at=started_at,
        completed_at=completed_at,
    )
    confirmed_codes = sorted(
        {
            str((record.get("payload") or {}).get("code") or "")
            for record in normalized_records
            if str((record.get("payload") or {}).get("code") or "")
        }
    )
    request_hashes = sorted(
        {str(record.get("request_hash") or "") for record in normalized_records}
    )
    raw_hashes = sorted(
        {str(record.get("raw_hash") or "") for record in normalized_records}
    )
    result = _safe(
        {
            "evidence_schema_version": SESSION_CONFIRMATION_SCHEMA_VERSION,
            "verifier_contract_version": SESSION_CONFIRMATION_VERIFIER_VERSION,
            "verifier_contract_hash": compute_session_verifier_contract_hash(),
            "status": SESSION_CONFIRMED if not errors else SESSION_REJECTED,
            "execution_ok": True,
            "evidence_integrity_verified": False,
            "trade_date": trade_date,
            "started_at": started_at,
            "completed_at": completed_at,
            "fixed_codes": list(FIXED_CODES),
            "capability": "quote",
            "origin_source": TENCENT_ORIGIN_SOURCE,
            "adapter": TENCENT_ADAPTER,
            "source_version": TENCENT_SOURCE_VERSION,
            "provider_key": provider_key,
            "qualification_status": "not_required",
            "adapter_execution": _adapter_summary(adapter_execution),
            "records": normalized_records,
            "record_count": len(normalized_records),
            "confirmed_codes": confirmed_codes,
            "request_hashes": request_hashes,
            "raw_response_hashes": raw_hashes,
            "confirmation_errors": errors,
            "market_session_confirmation_evidence_hash": "",
        }
    )
    result["market_session_confirmation_evidence_hash"] = (
        compute_market_session_confirmation_hash(result)
    )
    return result


def verify_market_session_confirmation_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_file_sha256: str | None,
    actual_file_sha256: str | None = None,
) -> dict[str, Any]:
    payload = dict(evidence)
    errors: list[str] = []
    if payload.get("evidence_schema_version") != SESSION_CONFIRMATION_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if payload.get("verifier_contract_version") != SESSION_CONFIRMATION_VERIFIER_VERSION:
        errors.append("verifier_contract_version_invalid")
    if payload.get("verifier_contract_hash") != compute_session_verifier_contract_hash():
        errors.append("verifier_contract_hash_mismatch")
    if payload.get("market_session_confirmation_evidence_hash") != (
        compute_market_session_confirmation_hash(payload)
    ):
        errors.append("evidence_hash_mismatch")
    if payload.get("fixed_codes") != list(FIXED_CODES):
        errors.append("fixed_codes_mismatch")
    if (
        payload.get("capability"),
        payload.get("origin_source"),
        payload.get("adapter"),
        payload.get("source_version"),
        payload.get("provider_key"),
    ) != (
        "quote",
        TENCENT_ORIGIN_SOURCE,
        TENCENT_ADAPTER,
        TENCENT_SOURCE_VERSION,
        TENCENT_QUOTE_PROVIDER_KEY,
    ):
        errors.append("confirmation_identity_mismatch")
    if payload.get("qualification_status") != "not_required":
        errors.append("qualification_status_invalid")
    if not _is_sha256(expected_file_sha256):
        errors.append("external_sha256_anchor_required")
    elif actual_file_sha256 != expected_file_sha256:
        errors.append("external_sha256_anchor_mismatch")

    contract_errors = _confirmation_errors(
        trade_date=str(payload.get("trade_date") or ""),
        records=payload.get("records") or [],
        provider_key=str(payload.get("provider_key") or ""),
        adapter_execution=payload.get("adapter_execution") or {},
        started_at=str(payload.get("started_at") or ""),
        completed_at=str(payload.get("completed_at") or ""),
    )
    if payload.get("confirmation_errors") != contract_errors:
        errors.append("confirmation_errors_mismatch")
    expected_status = SESSION_CONFIRMED if not contract_errors else SESSION_REJECTED
    if payload.get("status") != expected_status:
        errors.append("status_mismatch")
    records = list(payload.get("records") or [])
    codes = sorted(
        str((record.get("payload") or {}).get("code") or "")
        for record in records
        if isinstance(record, Mapping)
    )
    if payload.get("record_count") != len(records):
        errors.append("record_count_mismatch")
    if payload.get("confirmed_codes") != codes:
        errors.append("confirmed_codes_mismatch")
    if payload.get("request_hashes") != sorted(
        {str(record.get("request_hash") or "") for record in records}
    ):
        errors.append("request_hashes_mismatch")
    if payload.get("raw_response_hashes") != sorted(
        {str(record.get("raw_hash") or "") for record in records}
    ):
        errors.append("raw_hashes_mismatch")
    _append_safety_errors(payload, errors)
    verified = not errors
    return _safe(
        {
            "status": (
                "MARKET_SESSION_CONFIRMATION_EVIDENCE_VERIFIED"
                if verified
                else "MARKET_SESSION_CONFIRMATION_EVIDENCE_INVALID"
            ),
            "execution_ok": True,
            "evidence_integrity_verified": verified,
            "session_confirmed": verified and expected_status == SESSION_CONFIRMED,
            "expected_file_sha256": expected_file_sha256 or "",
            "actual_file_sha256": actual_file_sha256 or "",
            "market_session_confirmation_evidence_hash": payload.get(
                "market_session_confirmation_evidence_hash"
            )
            or "",
            "errors": errors,
        }
    )


def compute_market_session_confirmation_hash(
    evidence: Mapping[str, Any],
) -> str:
    material = dict(evidence)
    material.pop("market_session_confirmation_evidence_hash", None)
    return stable_hash(material)


def compute_session_verifier_contract_hash() -> str:
    return stable_hash(
        {
            "schema_version": SESSION_CONFIRMATION_SCHEMA_VERSION,
            "verifier_contract_version": SESSION_CONFIRMATION_VERIFIER_VERSION,
            "fixed_codes": list(FIXED_CODES),
            "identity": [
                "quote",
                TENCENT_ORIGIN_SOURCE,
                TENCENT_ADAPTER,
                TENCENT_SOURCE_VERSION,
                TENCENT_QUOTE_PROVIDER_KEY,
            ],
            "session_open_time": SESSION_OPEN_TIME.isoformat(),
            "rules": [
                "production_binding_exact",
                "fixed_codes_5_of_5",
                "event_date_equals_trade_date",
                "event_time_at_or_after_session_open",
                "event_time_le_observed_at_le_available_at",
                "request_and_raw_hash_sha256",
                "external_file_sha256_required",
                "safety_outputs_empty",
            ],
        }
    )


def write_market_session_confirmation_json_atomic(
    path: str | Path, payload: Mapping[str, Any]
) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _confirmation_errors(
    *,
    trade_date: str,
    records: Iterable[Mapping[str, Any]],
    provider_key: str,
    adapter_execution: Mapping[str, Any],
    started_at: str,
    completed_at: str,
) -> list[str]:
    errors: list[str] = []
    target = _parse_date(trade_date)
    started = _parse_datetime(started_at)
    completed = _parse_datetime(completed_at)
    if target is None:
        errors.append("trade_date_invalid")
    if not started or not completed or started > completed:
        errors.append("collection_time_order_invalid")
    if provider_key != TENCENT_QUOTE_PROVIDER_KEY:
        errors.append("provider_key_invalid")
    errors.extend(_production_binding_errors())
    if not _adapter_execution_valid(adapter_execution, provider_key):
        errors.append("production_adapter_execution_invalid")

    raw_records = list(records)
    rows = [dict(record) for record in raw_records if isinstance(record, Mapping)]
    if len(rows) != len(raw_records):
        errors.append("record_mapping_invalid")
    by_code: dict[str, dict[str, Any]] = {}
    expected_request_hash = compute_tencent_request_hash(FIXED_CODES)
    for index, record in enumerate(rows):
        payload = record.get("payload")
        code = str((payload or {}).get("code") or "") if isinstance(payload, Mapping) else ""
        if code not in FIXED_CODES:
            errors.append(f"record_code_invalid:{index}")
            continue
        if code in by_code:
            errors.append(f"record_code_duplicate:{code}")
            continue
        by_code[code] = record
        if (
            record.get("capability") != "quote"
            or record.get("origin_source") != TENCENT_ORIGIN_SOURCE
            or record.get("adapter") != TENCENT_ADAPTER
            or record.get("source_version") != TENCENT_SOURCE_VERSION
        ):
            errors.append(f"record_source_identity_invalid:{code}")
        event = _parse_datetime(record.get("event_time"))
        observed = _parse_datetime(record.get("observed_at"))
        available = _parse_datetime(record.get("available_at"))
        if not event or not observed or not available:
            errors.append(f"record_time_invalid:{code}")
        else:
            if target and (
                event.date() != target
                or observed.date() != target
                or available.date() != target
            ):
                errors.append(f"record_trade_date_mismatch:{code}")
            if event.timetz().replace(tzinfo=None) < SESSION_OPEN_TIME:
                errors.append(f"record_before_market_session:{code}")
            if event > observed:
                errors.append(f"record_event_after_observed:{code}")
            if available < observed:
                errors.append(f"record_available_before_observed:{code}")
        if record.get("request_hash") != expected_request_hash:
            errors.append(f"record_request_hash_invalid:{code}")
        if not _is_sha256(record.get("raw_hash")):
            errors.append(f"record_raw_hash_invalid:{code}")
    if tuple(sorted(by_code)) != tuple(sorted(FIXED_CODES)):
        errors.append("fixed_code_coverage_incomplete")
    if len({row.get("request_hash") for row in rows}) != 1:
        errors.append("request_hash_batch_mismatch")
    if len({row.get("raw_hash") for row in rows}) != 1:
        errors.append("raw_hash_batch_mismatch")
    return _unique(errors)


def _production_binding_errors() -> list[str]:
    errors = []
    capability_rows = [
        row
        for row in get_source_capability_registry()
        if (
            row.get("capability"),
            row.get("origin_source"),
            row.get("adapter"),
            row.get("source_version"),
        )
        == ("quote", TENCENT_ORIGIN_SOURCE, TENCENT_ADAPTER, TENCENT_SOURCE_VERSION)
    ]
    adapter_rows = [
        row
        for row in get_source_adapter_registry()
        if (
            row.get("capability"),
            row.get("origin_source"),
            row.get("adapter"),
            row.get("source_version"),
        )
        == ("quote", TENCENT_ORIGIN_SOURCE, TENCENT_ADAPTER, TENCENT_SOURCE_VERSION)
    ]
    if len(capability_rows) != 1:
        errors.append("production_capability_binding_missing")
    else:
        row = capability_rows[0]
        if (
            row.get("enabled_by_policy") is not True
            or row.get("qualification_status") != "not_required"
        ):
            errors.append("production_capability_binding_invalid")
    if len(adapter_rows) != 1:
        errors.append("production_adapter_binding_missing")
    else:
        row = adapter_rows[0]
        if (
            row.get("implementation_status") != "bound"
            or row.get("provider_key") != TENCENT_QUOTE_PROVIDER_KEY
            or row.get("candidate_provider_key") != ""
        ):
            errors.append("production_adapter_binding_invalid")
    return errors


def _adapter_execution_valid(result: Mapping[str, Any], provider_key: str) -> bool:
    binding = result.get("binding") or {}
    return (
        result.get("status") == "SOURCE_ADAPTER_BOUND"
        and result.get("execution_ok") is True
        and result.get("provider_called") is True
        and result.get("selection_registry_scope") == "production"
        and result.get("requested_identity")
        == ["quote", TENCENT_ORIGIN_SOURCE, TENCENT_ADAPTER, TENCENT_SOURCE_VERSION]
        and isinstance(binding, Mapping)
        and binding.get("implementation_status") == "bound"
        and binding.get("provider_key") == provider_key
    )


def _adapter_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    binding = result.get("binding") or {}
    return {
        "status": result.get("status"),
        "execution_ok": result.get("execution_ok"),
        "provider_called": result.get("provider_called"),
        "selection_registry_scope": result.get("selection_registry_scope"),
        "production_adapter_registry_hash": result.get(
            "production_adapter_registry_hash"
        ),
        "selection_adapter_registry_hash": result.get(
            "selection_adapter_registry_hash"
        ),
        "requested_identity": result.get("requested_identity"),
        "binding": dict(binding) if isinstance(binding, Mapping) else {},
    }


def _append_safety_errors(payload: Mapping[str, Any], errors: list[str]) -> None:
    for key in (
        "data_ready",
        "hard_gate_authorized",
        "automatic_configuration_change",
        "automatic_qualification_change",
    ):
        if payload.get(key) is not False:
            errors.append(f"safety_flag_invalid:{key}")
    for key in ("candidates", "tickets", "orders"):
        if payload.get(key) != []:
            errors.append(f"safety_output_invalid:{key}")


def _safe(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(payload),
        "sampling_authorized": False,
        "automatic_configuration_change": False,
        "automatic_qualification_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(CN_TZ)


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and set(text) <= _SHA256


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "SESSION_CONFIRMATION_SCHEMA_VERSION",
    "SESSION_CONFIRMATION_VERIFIER_VERSION",
    "SESSION_CONFIRMED",
    "SESSION_REJECTED",
    "build_market_session_confirmation",
    "compute_market_session_confirmation_hash",
    "compute_session_verifier_contract_hash",
    "file_sha256",
    "verify_market_session_confirmation_evidence",
    "write_market_session_confirmation_json_atomic",
]
