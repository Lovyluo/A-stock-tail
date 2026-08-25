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
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION,
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
        replay_material = _verify_v2(payload)
    except (TencentEvidenceVerificationError, TencentProviderContractError) as exc:
        code = getattr(exc, "code", "TENCENT_EVIDENCE_CONTRACT_INVALID")
        return _invalid(str(code), file_hash)
    return _safe_result(
        {
            "status": EVIDENCE_VERIFIED,
            "execution_ok": True,
            "evidence_schema_version": (
                TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION
            ),
            "file_sha256": file_hash,
            "stored_evidence_hash": payload["evidence_hash"],
            "recomputed_evidence_hash": _recompute_evidence_hash(payload),
            "replay_hash": stable_hash(replay_material),
            "verified_capabilities": list(EXPECTED_CAPABILITIES),
            "covered_codes": list(EXPECTED_CODES),
            "network_requests_made": payload["network_requests_made"],
            "upstream_network_activity": payload[
                "upstream_network_activity"
            ],
        }
    )


def _verify_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
    if (
        payload.get("evidence_schema_version")
        != TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION
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
            _verify_capability(capability, raw_entry, records, summary)
        )
    return {
        "evidence_schema_version": TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION,
        "requested_codes": list(EXPECTED_CODES),
        "network_requests_made": len(EXPECTED_CAPABILITIES),
        "capabilities": replay_capabilities,
    }


def _verify_capability(
    capability: str,
    raw_entry: object,
    records: object,
    summary: object,
) -> dict[str, Any]:
    if not isinstance(raw_entry, dict):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_RAW_ENTRY_INVALID"
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


def _verify_summary(
    capability: str,
    summary: object,
    records: list[dict[str, Any]],
) -> None:
    if not isinstance(summary, dict):
        raise TencentEvidenceVerificationError(
            "TENCENT_EVIDENCE_SUMMARY_INVALID"
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
    stored_hash = payload.get("evidence_hash")
    recomputed_hash = _recompute_evidence_hash(payload)
    if stored_hash != recomputed_hash:
        return _invalid("TENCENT_LEGACY_EVIDENCE_HASH_MISMATCH", file_hash)
    try:
        _validate_safe_output(payload)
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
