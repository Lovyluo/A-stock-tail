from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    validate_source_provenance_batch,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_ADAPTER,
    TENCENT_ORIGIN_SOURCE,
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2,
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3,
    TENCENT_QUOTE_ENDPOINT,
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_SOURCE_VERSION,
    TENCENT_VALUATION_PROVIDER_KEY,
    TencentProviderContractError,
    build_tencent_payload,
    build_tencent_request_url,
    compute_tencent_request_hash,
    parse_tencent_response_bytes,
)
from overnight_quant.scripts.run_tencent_provider_validation import (
    write_validation_json_atomic,
)


EVIDENCE_VERIFIED = "TENCENT_PROVIDER_EVIDENCE_VERIFIED"
EVIDENCE_INVALID = "TENCENT_PROVIDER_EVIDENCE_INVALID"
EVIDENCE_LEGACY_AUDIT_ONLY = (
    "TENCENT_PROVIDER_EVIDENCE_LEGACY_AUDIT_ONLY"
)
EXPECTED_CODES = ("000001", "000333", "600000", "600519", "601318")
EXPECTED_CAPABILITIES = ("quote", "valuation")
EXPECTED_PROVIDER_KEYS = {
    "quote": TENCENT_QUOTE_PROVIDER_KEY,
    "valuation": TENCENT_VALUATION_PROVIDER_KEY,
}
V3_TOP_LEVEL_KEYS = {
    "automatic_configuration_change",
    "candidate_provider_validation_only",
    "candidates",
    "capability_attempts",
    "capability_results",
    "data_ready",
    "endpoint",
    "evidence_hash",
    "evidence_integrity_verified",
    "evidence_schema_version",
    "execution_ok",
    "hard_gate_authorized",
    "network_mode",
    "network_requests_made",
    "orders",
    "provider_keys",
    "provider_validation_passed",
    "raw_responses",
    "records_by_capability",
    "requested_codes",
    "status",
    "tickets",
    "upstream_network_activity",
}
V1_TOP_LEVEL_KEYS = {
    "automatic_configuration_change",
    "candidate_provider_validation_only",
    "candidates",
    "capability_results",
    "data_ready",
    "endpoint",
    "evidence_hash",
    "execution_ok",
    "hard_gate_authorized",
    "network_mode",
    "network_requests_made",
    "orders",
    "provider_keys",
    "records_by_capability",
    "requested_codes",
    "status",
    "tickets",
}
ALLOWED_PROVIDER_FAILURE_CODES = {
    "TENCENT_CLOCK_INVALID",
    "TENCENT_HTTP_STATUS_INVALID",
    "TENCENT_NUMERIC_FIELD_INVALID",
    "TENCENT_REQUEST_FAILED",
    "TENCENT_REQUEST_TIMEOUT",
    "TENCENT_REQUIRED_FIELD_INVALID",
    "TENCENT_REQUIRED_FIELD_MISSING",
    "TENCENT_RESPONSE_CODE_MISMATCH",
    "TENCENT_RESPONSE_COVERAGE_INCOMPLETE",
    "TENCENT_RESPONSE_DUPLICATE_CODE",
    "TENCENT_RESPONSE_EMPTY",
    "TENCENT_RESPONSE_FIELD_COUNT_INVALID",
    "TENCENT_RESPONSE_GBK_INVALID",
    "TENCENT_RESPONSE_LINE_INVALID",
    "TENCENT_RESPONSE_MARKET_CODE_MISMATCH",
    "TENCENT_RESPONSE_SOURCE_INVALID",
    "TENCENT_RESPONSE_UNREQUESTED_CODE",
    "TENCENT_SOURCE_TIME_AFTER_OBSERVED_AT",
    "TENCENT_SOURCE_TIME_INVALID",
    "TENCENT_TIME_ORDER_INVALID",
    "TENCENT_TRANSPORT_CONTRACT_INVALID",
    "TENCENT_TRANSPORT_RESPONSE_INVALID",
}
QUOTE_PAYLOAD_KEYS = {
    "amount",
    "change_pct",
    "code",
    "field_units",
    "high",
    "limit_down",
    "limit_up",
    "low",
    "name",
    "open",
    "order_book",
    "prev_close",
    "price",
    "source_field_count",
    "turnover_pct",
    "volume",
}
VALUATION_PAYLOAD_KEYS = {
    "amplitude_pct",
    "code",
    "field_indices",
    "field_units",
    "float_market_cap",
    "market_cap",
    "name",
    "pb",
    "pe_static",
    "pe_ttm",
    "source_field_count",
    "valuation_availability",
}
QUOTE_FIELD_UNITS = {
    "price": "CNY_per_share",
    "prev_close": "CNY_per_share",
    "open": "CNY_per_share",
    "high": "CNY_per_share",
    "low": "CNY_per_share",
    "change_pct": "percent",
    "volume": "lot",
    "amount": "CNY_10k",
    "turnover_pct": "percent",
    "limit_up": "CNY_per_share",
    "limit_down": "CNY_per_share",
    "order_book.price": "CNY_per_share",
    "order_book.volume": "lot",
}
VALUATION_FIELD_INDICES = {
    "pe_ttm": 39,
    "amplitude_pct": 43,
    "market_cap": 44,
    "float_market_cap": 45,
    "pb": 46,
    "pe_static": 52,
}
VALUATION_FIELD_UNITS = {
    "pe_ttm": "ratio",
    "market_cap": "CNY_100m",
    "float_market_cap": "CNY_100m",
    "pb": "ratio",
    "pe_static": "ratio",
    "amplitude_pct": "percent",
}


class TencentEvidenceVerificationError(ValueError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def verify_tencent_provider_evidence(path: str | Path) -> dict[str, Any]:
    evidence_path = Path(path).resolve()
    try:
        raw_file = evidence_path.read_bytes()
    except OSError:
        return _invalid("TENCENT_EVIDENCE_FILE_UNREADABLE")
    file_hash = hashlib.sha256(raw_file).hexdigest()
    if raw_file.startswith(b"\xef\xbb\xbf"):
        return _invalid("TENCENT_EVIDENCE_UTF8_BOM_FORBIDDEN", file_hash)
    try:
        payload = json.loads(raw_file.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _invalid("TENCENT_EVIDENCE_JSON_INVALID", file_hash)
    if not isinstance(payload, dict):
        return _invalid("TENCENT_EVIDENCE_OBJECT_REQUIRED", file_hash)
    if "evidence_schema_version" not in payload:
        return _verify_legacy_audit(payload, file_hash)
    try:
        schema_version = payload.get("evidence_schema_version")
        if schema_version == TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2:
            replay_material = _verify_v2(payload)
            return _safe_result(
                {
                    "status": EVIDENCE_VERIFIED,
                    "execution_ok": True,
                    "evidence_schema_version": (
                        TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2
                    ),
                    "file_sha256": file_hash,
                    "stored_evidence_hash": payload["evidence_hash"],
                    "recomputed_evidence_hash": (
                        _recompute_evidence_hash(payload)
                    ),
                    "replay_hash": stable_hash(replay_material),
                    "verified_capabilities": list(EXPECTED_CAPABILITIES),
                    "covered_codes": list(EXPECTED_CODES),
                    "network_requests_made": payload[
                        "network_requests_made"
                    ],
                    "upstream_network_activity": payload[
                        "upstream_network_activity"
                    ],
                }
            )
        if schema_version == TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3:
            provider_passed, replay_material = _verify_v3(payload)
            return _safe_result(
                {
                    "status": EVIDENCE_VERIFIED,
                    "execution_ok": True,
                    "evidence_schema_version": (
                        TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3
                    ),
                    "evidence_integrity_verified": True,
                    "provider_validation_passed": provider_passed,
                    "file_sha256": file_hash,
                    "stored_evidence_hash": payload["evidence_hash"],
                    "recomputed_evidence_hash": (
                        _recompute_evidence_hash(payload)
                    ),
                    "replay_hash": stable_hash(replay_material),
                    "verified_capabilities": sorted(
                        capability
                        for capability, outcome in replay_material[
                            "capability_outcomes"
                        ].items()
                        if outcome["status"] == "validated"
                    ),
                    "evaluated_capabilities": list(EXPECTED_CAPABILITIES),
                    "covered_codes": replay_material["covered_codes"],
                    "network_requests_made": payload.get(
                        "network_requests_made"
                    ),
                    "upstream_network_activity": payload.get(
                        "upstream_network_activity"
                    ),
                }
            )
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_SCHEMA_INVALID"
        )
    except (TencentEvidenceVerificationError, TencentProviderContractError) as exc:
        code = getattr(exc, "code", "TENCENT_EVIDENCE_CONTRACT_INVALID")
        return _invalid(str(code), file_hash)


def _verify_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
    if (
        payload.get("evidence_schema_version")
        != TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2
    ):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_SCHEMA_INVALID"
        )
    if payload.get("status") != "TENCENT_PROVIDER_NETWORK_VALIDATED":
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_STATUS_INVALID"
        )
    if payload.get("execution_ok") is not True:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_EXECUTION_STATUS_INVALID"
        )
    if payload.get("network_mode") is not True:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_NETWORK_MODE_INVALID"
        )
    _validate_safe_output(payload)
    if payload.get("endpoint") != TENCENT_QUOTE_ENDPOINT:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_ENDPOINT_INVALID"
        )
    if payload.get("provider_keys") != EXPECTED_PROVIDER_KEYS:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_PROVIDER_KEYS_INVALID"
        )
    if payload.get("requested_codes") != list(EXPECTED_CODES):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_REQUESTED_CODES_INVALID"
        )
    if payload.get("network_requests_made") != len(EXPECTED_CAPABILITIES):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_NETWORK_REQUEST_COUNT_INVALID"
        )
    if payload.get("upstream_network_activity") != "measured":
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_NETWORK_ACTIVITY_INVALID"
        )
    if _recompute_evidence_hash(payload) != payload.get("evidence_hash"):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_HASH_MISMATCH"
        )

    raw_responses = payload.get("raw_responses")
    records_by_capability = payload.get("records_by_capability")
    capability_results = payload.get("capability_results")
    for value in (raw_responses, records_by_capability, capability_results):
        if not isinstance(value, dict) or set(value) != set(
            EXPECTED_CAPABILITIES
        ):
            raise TencentEvidenceVerificationError(
                "TENCENT_EVIDENCE_CAPABILITY_SET_INVALID"
            )

    replay_capabilities = []
    for capability in EXPECTED_CAPABILITIES:
        raw_entry = raw_responses[capability]
        records = records_by_capability[capability]
        summary = capability_results[capability]
        replay_capabilities.append(
            _verify_capability(
                capability,
                raw_entry,
                records,
                summary,
                require_http_identity=False,
            )
        )
    return {
        "evidence_schema_version": TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2,
        "requested_codes": list(EXPECTED_CODES),
        "network_requests_made": len(EXPECTED_CAPABILITIES),
        "capabilities": replay_capabilities,
    }


def _verify_v3(
    payload: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if set(payload) != V3_TOP_LEVEL_KEYS:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_TOP_LEVEL_FIELDS_INVALID"
        )
    if (
        payload.get("evidence_schema_version")
        != TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3
    ):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_SCHEMA_INVALID"
        )
    if payload.get("execution_ok") is not True:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_EXECUTION_STATUS_INVALID"
        )
    if payload.get("network_mode") is not True:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_NETWORK_MODE_INVALID"
        )
    if payload.get("evidence_integrity_verified") is not False:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_SELF_VERIFICATION_INVALID"
        )
    if type(payload.get("provider_validation_passed")) is not bool:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_PROVIDER_STATUS_INVALID"
        )
    _validate_safe_output(payload)
    if payload.get("endpoint") != TENCENT_QUOTE_ENDPOINT:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_ENDPOINT_INVALID"
        )
    if payload.get("provider_keys") != EXPECTED_PROVIDER_KEYS:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_PROVIDER_KEYS_INVALID"
        )
    if payload.get("requested_codes") != list(EXPECTED_CODES):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_REQUESTED_CODES_INVALID"
        )
    if _recompute_evidence_hash(payload) != payload.get("evidence_hash"):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_HASH_MISMATCH"
        )
    _verify_network_activity(payload)

    records_by_capability = payload.get("records_by_capability")
    capability_results = payload.get("capability_results")
    capability_attempts = payload.get("capability_attempts")
    for value in (
        records_by_capability,
        capability_results,
        capability_attempts,
    ):
        if not isinstance(value, dict) or set(value) != set(
            EXPECTED_CAPABILITIES
        ):
            raise TencentEvidenceVerificationError(
                "TENCENT_EVIDENCE_CAPABILITY_SET_INVALID"
            )
    raw_responses = payload.get("raw_responses")
    if not isinstance(raw_responses, dict) or not set(raw_responses).issubset(
        EXPECTED_CAPABILITIES
    ):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_CAPABILITY_SET_INVALID"
        )

    outcomes: dict[str, dict[str, Any]] = {}
    for capability in EXPECTED_CAPABILITIES:
        summary = capability_results[capability]
        records = records_by_capability[capability]
        attempt = capability_attempts[capability]
        status = summary.get("status") if isinstance(summary, dict) else None
        if status == "TENCENT_PROVIDER_CAPABILITY_VALIDATED":
            if capability not in raw_responses:
                raise TencentEvidenceVerificationError(
                    f"TENCENT_EVIDENCE_RAW_RESPONSE_MISSING:{capability}"
                )
            _verify_attempt(
                capability,
                attempt,
                expected_outcome="response_received",
                expected_error_code="",
                response_captured=True,
            )
            verified = _verify_capability(
                capability,
                raw_responses[capability],
                records,
                summary,
                require_http_identity=True,
            )
            outcomes[capability] = {
                "status": "validated",
                **verified,
            }
            continue
        if status == "TENCENT_PROVIDER_CAPABILITY_FAILED":
            if capability in raw_responses:
                raise TencentEvidenceVerificationError(
                    f"TENCENT_EVIDENCE_FAILED_RAW_RESPONSE_PRESENT:{capability}"
                )
            error_code = _verify_failure_summary(capability, summary, records)
            _verify_attempt(
                capability,
                attempt,
                expected_outcome="failed",
                expected_error_code=error_code,
                response_captured=False,
            )
            outcomes[capability] = {
                "capability": capability,
                "status": "failed",
                "error_code": error_code,
                "records": [],
            }
            continue
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_CAPABILITY_STATUS_INVALID:{capability}"
        )

    provider_passed = all(
        outcome["status"] == "validated" for outcome in outcomes.values()
    )
    covered_codes = sorted(
        {
            record["payload"]["code"]
            for outcome in outcomes.values()
            if outcome["status"] == "validated"
            for record in outcome["records"]
        }
    )
    if payload.get("provider_validation_passed") is not provider_passed:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_PROVIDER_STATUS_MISMATCH"
        )
    expected_status = (
        "TENCENT_PROVIDER_NETWORK_VALIDATED"
        if provider_passed
        else "TENCENT_PROVIDER_NETWORK_VALIDATION_FAILED"
    )
    if payload.get("status") != expected_status:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_STATUS_INVALID"
        )
    return provider_passed, {
        "evidence_schema_version": TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3,
        "requested_codes": list(EXPECTED_CODES),
        "network_requests_made": payload.get("network_requests_made"),
        "upstream_network_activity": payload.get(
            "upstream_network_activity"
        ),
        "provider_validation_passed": provider_passed,
        "covered_codes": covered_codes,
        "capability_outcomes": outcomes,
    }


def _verify_capability(
    capability: str,
    raw_entry: object,
    records: object,
    summary: object,
    *,
    require_http_identity: bool,
) -> dict[str, Any]:
    if not isinstance(raw_entry, dict):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_ENTRY_INVALID"
        )
    expected_raw_keys = {
        "available_at",
        "byte_count",
        "capability",
        "content_base64",
        "encoding",
        "observed_at",
        "raw_hash",
        "request_hash",
        "request_url",
    }
    if require_http_identity:
        expected_raw_keys.update({"http_status_code", "response_url"})
    if set(raw_entry) != expected_raw_keys:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_FIELDS_INVALID"
        )
    if raw_entry.get("capability") != capability:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_CAPABILITY_IDENTITY_INVALID"
        )
    if raw_entry.get("encoding") != "base64":
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_ENCODING_INVALID"
        )
    encoded = raw_entry.get("content_base64")
    if not isinstance(encoded, str) or not encoded:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_CONTENT_MISSING"
        )
    try:
        raw_response = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_BASE64_INVALID"
        ) from None
    raw_hash = hashlib.sha256(raw_response).hexdigest()
    if raw_entry.get("byte_count") != len(raw_response):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_SIZE_INVALID"
        )
    if raw_entry.get("raw_hash") != raw_hash:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_HASH_MISMATCH"
        )
    request_url = build_tencent_request_url(EXPECTED_CODES)
    request_hash = compute_tencent_request_hash(EXPECTED_CODES)
    if raw_entry.get("request_url") != request_url:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_REQUEST_URL_INVALID"
        )
    if raw_entry.get("request_hash") != request_hash:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_REQUEST_HASH_INVALID"
        )
    if require_http_identity:
        _verify_http_identity(raw_entry)
    observed_at = _parse_strict_datetime(raw_entry.get("observed_at"))
    available_at = _parse_strict_datetime(raw_entry.get("available_at"))
    if observed_at > available_at:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_TIME_ORDER_INVALID"
        )

    parsed_rows = parse_tencent_response_bytes(raw_response, EXPECTED_CODES)
    if not isinstance(records, list) or len(records) != len(EXPECTED_CODES):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RECORD_COUNT_INVALID"
        )
    expected_records = []
    for code, values, event_time in parsed_rows:
        if event_time > observed_at:
            raise TencentEvidenceVerificationError(
                "TENCENT_EVIDENCE_SOURCE_TIME_INVALID"
            )
        expected_records.append(
            {
                "capability": capability,
                "origin_source": TENCENT_ORIGIN_SOURCE,
                "adapter": TENCENT_ADAPTER,
                "source_version": TENCENT_SOURCE_VERSION,
                "event_time": event_time.isoformat(timespec="seconds"),
                "observed_at": raw_entry["observed_at"],
                "available_at": raw_entry["available_at"],
                "request_hash": request_hash,
                "raw_hash": raw_hash,
                "payload": build_tencent_payload(capability, code, values),
            }
        )
    if records != expected_records:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_REPARSED_RECORDS_MISMATCH"
        )
    provenance = validate_source_provenance_batch(
        capability,
        records,
        require_hard_gate=False,
        environ={},
    )
    if provenance.get("status") != "SOURCE_PROVENANCE_ACCEPTED":
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_PROVENANCE_REJECTED"
        )
    _verify_summary(capability, summary, expected_records)
    return {
        "capability": capability,
        "request_hash": request_hash,
        "raw_hash": raw_hash,
        "records": expected_records,
    }


def _verify_http_identity(raw_entry: Mapping[str, Any]) -> None:
    if (
        type(raw_entry.get("http_status_code")) is not int
        or raw_entry.get("http_status_code") != 200
    ):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_HTTP_STATUS_INVALID"
        )
    response_url = raw_entry.get("response_url")
    if not isinstance(response_url, str) or not response_url:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RESPONSE_URL_INVALID"
        )
    parsed = urlparse(response_url)
    if parsed.scheme != "https" or parsed.hostname != "qt.gtimg.cn":
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RESPONSE_SOURCE_INVALID"
        )


def _verify_network_activity(payload: Mapping[str, Any]) -> None:
    activity = payload.get("upstream_network_activity")
    count = payload.get("network_requests_made")
    if activity == "measured":
        if type(count) is not int or count != len(EXPECTED_CAPABILITIES):
            raise TencentEvidenceVerificationError(
                "TENCENT_EVIDENCE_NETWORK_REQUEST_COUNT_INVALID"
            )
        return
    if activity == "unknown" and count is None:
        return
    raise TencentEvidenceVerificationError(
        "TENCENT_EVIDENCE_NETWORK_ACTIVITY_INVALID"
    )


def _verify_attempt(
    capability: str,
    attempt: object,
    *,
    expected_outcome: str,
    expected_error_code: str,
    response_captured: bool,
) -> None:
    if not isinstance(attempt, dict) or set(attempt) != {
        "capability",
        "completed_at",
        "error_code",
        "outcome",
        "response_captured",
        "started_at",
    }:
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_ATTEMPT_INVALID:{capability}"
        )
    started_at = _parse_strict_datetime(attempt.get("started_at"))
    completed_at = _parse_strict_datetime(attempt.get("completed_at"))
    if completed_at < started_at:
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_ATTEMPT_TIME_INVALID:{capability}"
        )
    expected = {
        "capability": capability,
        "outcome": expected_outcome,
        "error_code": expected_error_code,
        "response_captured": response_captured,
    }
    if any(attempt.get(key) != value for key, value in expected.items()):
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_ATTEMPT_MISMATCH:{capability}"
        )


def _verify_failure_summary(
    capability: str,
    summary: object,
    records: object,
) -> str:
    if not isinstance(summary, dict) or set(summary) != {
        "coverage_ratio",
        "covered_codes",
        "elapsed_ms",
        "error_code",
        "record_count",
        "status",
    }:
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_FAILURE_SUMMARY_INVALID:{capability}"
        )
    elapsed = summary.get("elapsed_ms")
    if (
        not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
    ):
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_ELAPSED_INVALID:{capability}"
        )
    error_code = summary.get("error_code")
    if error_code not in ALLOWED_PROVIDER_FAILURE_CODES:
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_FAILURE_CODE_INVALID:{capability}"
        )
    if (
        summary.get("status") != "TENCENT_PROVIDER_CAPABILITY_FAILED"
        or summary.get("record_count") != 0
        or summary.get("covered_codes") != []
        or summary.get("coverage_ratio") != 0.0
        or records != []
    ):
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_FAILURE_SUMMARY_MISMATCH:{capability}"
        )
    return str(error_code)


def _verify_summary(
    capability: str,
    summary: object,
    records: list[dict[str, Any]],
) -> None:
    if not isinstance(summary, dict):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_SUMMARY_INVALID"
        )
    if set(summary) != {
        "coverage_ratio",
        "covered_codes",
        "elapsed_ms",
        "provenance_status",
        "raw_hashes",
        "record_count",
        "request_hashes",
        "source_event_times",
        "source_field_counts",
        "status",
    }:
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_SUMMARY_FIELDS_INVALID:{capability}"
        )
    elapsed = summary.get("elapsed_ms")
    if (
        not isinstance(elapsed, (int, float))
        or isinstance(elapsed, bool)
        or not math.isfinite(float(elapsed))
        or float(elapsed) < 0
    ):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_ELAPSED_INVALID"
        )
    covered_codes = [row["payload"]["code"] for row in records]
    expected = {
        "status": "TENCENT_PROVIDER_CAPABILITY_VALIDATED",
        "record_count": len(records),
        "covered_codes": covered_codes,
        "coverage_ratio": 1.0,
        "source_event_times": sorted(
            {row["event_time"] for row in records}
        ),
        "source_field_counts": [88],
        "request_hashes": sorted(
            {row["request_hash"] for row in records}
        ),
        "raw_hashes": sorted({row["raw_hash"] for row in records}),
        "provenance_status": "SOURCE_PROVENANCE_ACCEPTED",
    }
    actual = {key: summary.get(key) for key in expected}
    if actual != expected:
        raise TencentEvidenceVerificationError(
            f"TENCENT_EVIDENCE_SUMMARY_MISMATCH:{capability}"
        )


def _validate_safe_output(payload: Mapping[str, Any]) -> None:
    expected = {
        "candidate_provider_validation_only": True,
        "data_ready": False,
        "hard_gate_authorized": False,
        "automatic_configuration_change": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_SAFETY_OUTPUT_INVALID"
        )


def _verify_legacy_audit(
    payload: Mapping[str, Any],
    file_hash: str,
) -> dict[str, Any]:
    if set(payload) != V1_TOP_LEVEL_KEYS:
        return _invalid("TENCENT_LEGACY_EVIDENCE_FIELDS_INVALID", file_hash)
    stored_hash = payload.get("evidence_hash")
    recomputed_hash = _recompute_evidence_hash(payload)
    if stored_hash != recomputed_hash:
        return _invalid("TENCENT_LEGACY_EVIDENCE_HASH_MISMATCH", file_hash)
    try:
        _validate_safe_output(payload)
        _verify_legacy_contract(payload)
    except TencentEvidenceVerificationError as exc:
        return _invalid(exc.code, file_hash)
    return _safe_result(
        {
            "status": EVIDENCE_LEGACY_AUDIT_ONLY,
            "execution_ok": True,
            "evidence_schema_version": "legacy_v1_unspecified",
            "file_sha256": file_hash,
            "stored_evidence_hash": stored_hash,
            "recomputed_evidence_hash": recomputed_hash,
            "replay_hash": "",
            "network_requests_made": payload.get("network_requests_made"),
            "upstream_network_activity": "legacy_unknown",
        }
    )


def _verify_legacy_contract(payload: Mapping[str, Any]) -> None:
    expected_top = {
        "status": "TENCENT_PROVIDER_NETWORK_VALIDATED",
        "execution_ok": True,
        "network_mode": True,
        "endpoint": TENCENT_QUOTE_ENDPOINT,
        "requested_codes": list(EXPECTED_CODES),
        "provider_keys": EXPECTED_PROVIDER_KEYS,
        "network_requests_made": len(EXPECTED_CAPABILITIES),
    }
    if any(payload.get(key) != value for key, value in expected_top.items()):
        raise TencentEvidenceVerificationError(
            "TENCENT_LEGACY_EVIDENCE_CONTRACT_INVALID"
        )
    records_by_capability = payload.get("records_by_capability")
    capability_results = payload.get("capability_results")
    for value in (records_by_capability, capability_results):
        if not isinstance(value, dict) or set(value) != set(
            EXPECTED_CAPABILITIES
        ):
            raise TencentEvidenceVerificationError(
                "TENCENT_LEGACY_CAPABILITY_SET_INVALID"
            )
    for capability in EXPECTED_CAPABILITIES:
        records = records_by_capability[capability]
        if not isinstance(records, list) or len(records) != len(EXPECTED_CODES):
            raise TencentEvidenceVerificationError(
                f"TENCENT_LEGACY_RECORD_COUNT_INVALID:{capability}"
            )
        covered_codes = []
        for record in records:
            _verify_legacy_record(capability, record)
            covered_codes.append(record["payload"]["code"])
        if covered_codes != list(EXPECTED_CODES):
            raise TencentEvidenceVerificationError(
                f"TENCENT_LEGACY_COVERAGE_INVALID:{capability}"
            )
        provenance = validate_source_provenance_batch(
            capability,
            records,
            require_hard_gate=False,
            environ={},
        )
        if provenance.get("status") != "SOURCE_PROVENANCE_ACCEPTED":
            raise TencentEvidenceVerificationError(
                f"TENCENT_LEGACY_PROVENANCE_REJECTED:{capability}"
            )
        _verify_summary(
            capability,
            capability_results[capability],
            records,
        )


def _verify_legacy_record(capability: str, record: object) -> None:
    if not isinstance(record, dict) or set(record) != {
        "adapter",
        "available_at",
        "capability",
        "event_time",
        "observed_at",
        "origin_source",
        "payload",
        "raw_hash",
        "request_hash",
        "source_version",
    }:
        raise TencentEvidenceVerificationError(
            f"TENCENT_LEGACY_RECORD_FIELDS_INVALID:{capability}"
        )
    expected_identity = {
        "capability": capability,
        "origin_source": TENCENT_ORIGIN_SOURCE,
        "adapter": TENCENT_ADAPTER,
        "source_version": TENCENT_SOURCE_VERSION,
        "request_hash": compute_tencent_request_hash(EXPECTED_CODES),
    }
    if any(record.get(key) != value for key, value in expected_identity.items()):
        raise TencentEvidenceVerificationError(
            f"TENCENT_LEGACY_RECORD_IDENTITY_INVALID:{capability}"
        )
    if not _is_sha256(record.get("raw_hash")):
        raise TencentEvidenceVerificationError(
            f"TENCENT_LEGACY_RAW_HASH_INVALID:{capability}"
        )
    event_time = _parse_strict_datetime(record.get("event_time"))
    observed_at = _parse_strict_datetime(record.get("observed_at"))
    available_at = _parse_strict_datetime(record.get("available_at"))
    if not event_time <= observed_at <= available_at:
        raise TencentEvidenceVerificationError(
            f"TENCENT_LEGACY_TIME_ORDER_INVALID:{capability}"
        )
    _verify_legacy_payload(capability, record.get("payload"))


def _verify_legacy_payload(capability: str, payload: object) -> None:
    if not isinstance(payload, dict):
        raise TencentEvidenceVerificationError(
            f"TENCENT_LEGACY_PAYLOAD_INVALID:{capability}"
        )
    expected_keys = (
        QUOTE_PAYLOAD_KEYS if capability == "quote" else VALUATION_PAYLOAD_KEYS
    )
    if set(payload) != expected_keys:
        raise TencentEvidenceVerificationError(
            f"TENCENT_LEGACY_PAYLOAD_FIELDS_INVALID:{capability}"
        )
    code = payload.get("code")
    if (
        code not in EXPECTED_CODES
        or not isinstance(payload.get("name"), str)
        or not payload.get("name")
    ):
        raise TencentEvidenceVerificationError(
            f"TENCENT_LEGACY_PAYLOAD_IDENTITY_INVALID:{capability}"
        )
    if type(payload.get("source_field_count")) is not int or payload.get(
        "source_field_count"
    ) != 88:
        raise TencentEvidenceVerificationError(
            f"TENCENT_LEGACY_FIELD_COUNT_INVALID:{capability}"
        )
    if capability == "quote":
        if payload.get("field_units") != QUOTE_FIELD_UNITS:
            raise TencentEvidenceVerificationError(
                "TENCENT_LEGACY_QUOTE_UNITS_INVALID"
            )
        _verify_legacy_order_book(payload.get("order_book"))
        for field in (
            "amount",
            "change_pct",
            "high",
            "limit_down",
            "limit_up",
            "low",
            "open",
            "prev_close",
            "price",
            "turnover_pct",
            "volume",
        ):
            if not _is_finite_number(payload.get(field)):
                raise TencentEvidenceVerificationError(
                    f"TENCENT_LEGACY_QUOTE_VALUE_INVALID:{field}"
                )
        return
    if payload.get("field_indices") != VALUATION_FIELD_INDICES:
        raise TencentEvidenceVerificationError(
            "TENCENT_LEGACY_VALUATION_INDICES_INVALID"
        )
    if payload.get("field_units") != VALUATION_FIELD_UNITS:
        raise TencentEvidenceVerificationError(
            "TENCENT_LEGACY_VALUATION_UNITS_INVALID"
        )
    mapped_fields = (
        "pe_ttm",
        "market_cap",
        "float_market_cap",
        "pb",
        "pe_static",
    )
    expected_availability = {
        field: "available" if payload.get(field) is not None else "missing"
        for field in mapped_fields
    }
    if payload.get("valuation_availability") != expected_availability:
        raise TencentEvidenceVerificationError(
            "TENCENT_LEGACY_VALUATION_AVAILABILITY_INVALID"
        )
    for field in (*mapped_fields, "amplitude_pct"):
        if payload.get(field) is not None and not _is_finite_number(
            payload.get(field)
        ):
            raise TencentEvidenceVerificationError(
                f"TENCENT_LEGACY_VALUATION_VALUE_INVALID:{field}"
            )


def _verify_legacy_order_book(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"asks", "bids"}:
        raise TencentEvidenceVerificationError(
            "TENCENT_LEGACY_ORDER_BOOK_INVALID"
        )
    for side in ("bids", "asks"):
        rows = value.get(side)
        if not isinstance(rows, list) or len(rows) != 5:
            raise TencentEvidenceVerificationError(
                "TENCENT_LEGACY_ORDER_BOOK_INVALID"
            )
        if [row.get("level") for row in rows if isinstance(row, dict)] != list(
            range(1, 6)
        ) or any(not isinstance(row, dict) or set(row) != {
            "level",
            "price",
            "volume",
        } for row in rows):
            raise TencentEvidenceVerificationError(
                "TENCENT_LEGACY_ORDER_BOOK_INVALID"
            )
        for row in rows:
            for field in ("price", "volume"):
                if row.get(field) is not None and not _is_finite_number(
                    row.get(field)
                ):
                    raise TencentEvidenceVerificationError(
                        "TENCENT_LEGACY_ORDER_BOOK_VALUE_INVALID"
                    )


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _recompute_evidence_hash(payload: Mapping[str, Any]) -> str:
    material = deepcopy(dict(payload))
    material.pop("evidence_hash", None)
    return stable_hash(material)


def _parse_strict_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_TIME_INVALID"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_TIME_INVALID"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_TIME_INVALID"
        )
    return parsed


def _invalid(code: str, file_hash: str = "") -> dict[str, Any]:
    return _safe_result(
        {
            "status": EVIDENCE_INVALID,
            "execution_ok": False,
            "evidence_integrity_verified": False,
            "provider_validation_passed": False,
            "error_code": code,
            "file_sha256": file_hash,
            "replay_hash": "",
            "network_requests_made": None,
            "upstream_network_activity": "unknown",
        }
    )


def _safe_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["data_ready"] = False
    result["hard_gate_authorized"] = False
    result["automatic_configuration_change"] = False
    result["candidates"] = []
    result["tickets"] = []
    result["orders"] = []
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Tencent provider evidence without network access",
    )
    parser.add_argument("evidence")
    parser.add_argument(
        "--output",
        help="Optional ignored cache path for deterministic replay JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = verify_tencent_provider_evidence(args.evidence)
    if args.output:
        try:
            write_validation_json_atomic(args.output, result)
        except (FileExistsError, TencentProviderContractError) as exc:
            result = _invalid(
                getattr(exc, "code", "TENCENT_REPLAY_OUTPUT_EXISTS")
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in {
        EVIDENCE_VERIFIED,
        EVIDENCE_LEGACY_AUDIT_ONLY,
    } else 3


if __name__ == "__main__":
    raise SystemExit(main())
