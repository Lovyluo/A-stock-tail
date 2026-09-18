from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.exchange_announcement_providers import (
    EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION,
    PROVIDER_KEYS,
    SOURCE_IDENTITIES,
    compute_exchange_announcement_verifier_contract_hash,
    replay_exchange_announcement_responses,
    validate_exchange_announcement_records,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
)
from overnight_quant.scripts.run_exchange_announcement_validation import (
    CACHE_ROOT,
    compute_evidence_hash,
)


EVIDENCE_VERIFIED = "EXCHANGE_ANNOUNCEMENT_EVIDENCE_VERIFIED"
EVIDENCE_INVALID = "EXCHANGE_ANNOUNCEMENT_EVIDENCE_INVALID"


def verify_exchange_announcement_evidence(
    payload: Mapping[str, Any],
    *,
    expected_file_sha256: str,
) -> dict[str, Any]:
    errors = []
    source = str(payload.get("source") or "")
    if payload.get("evidence_schema_version") != (
        EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION
    ):
        errors.append("evidence_schema_mismatch")
    if source not in SOURCE_IDENTITIES:
        errors.append("source_unknown")
    else:
        if payload.get("origin_source") != SOURCE_IDENTITIES[source][0]:
            errors.append("origin_source_mismatch")
        if payload.get("source_version") != SOURCE_IDENTITIES[source][2]:
            errors.append("source_version_mismatch")
        if payload.get("provider_key") != PROVIDER_KEYS[source]:
            errors.append("provider_key_mismatch")
    if payload.get("provider_verifier_contract_hash") != (
        compute_exchange_announcement_verifier_contract_hash()
    ):
        errors.append("verifier_contract_hash_mismatch")
    if payload.get("capability_registry_hash") != (
        compute_source_capability_registry_hash()
    ):
        errors.append("capability_registry_hash_mismatch")
    if payload.get("evidence_hash") != compute_evidence_hash(dict(payload)):
        errors.append("evidence_hash_mismatch")
    if not _sha256(expected_file_sha256):
        errors.append("external_file_sha256_required")
    for key in (
        "automatic_configuration_change",
        "data_ready",
        "hard_gate_authorized",
    ):
        if payload.get(key) is not False:
            errors.append(f"unsafe_{key}")
    for key in ("candidates", "tickets", "orders"):
        if payload.get(key) != []:
            errors.append(f"unsafe_{key}")

    responses = []
    for index, item in enumerate(payload.get("raw_responses") or []):
        try:
            raw = base64.b64decode(item["content_base64"], validate=True)
        except Exception:
            errors.append(f"response_{index}:base64_invalid")
            continue
        if len(raw) != item.get("byte_count"):
            errors.append(f"response_{index}:byte_count_mismatch")
        responses.append(
            {key: value for key, value in item.items() if key not in {
                "content_base64", "encoding", "byte_count"
            }} | {"raw_bytes": raw}
        )
    replay_records = []
    if source in SOURCE_IDENTITIES and not errors:
        replay_records, replay_errors = replay_exchange_announcement_responses(
            source,
            responses,
            feature_cutoff=str(payload.get("feature_cutoff") or ""),
            codes=payload.get("requested_codes") or [],
        )
        errors.extend(replay_errors)
        expected_records = sorted(
            [dict(row) for row in payload.get("records") or []],
            key=lambda row: (
                str(row.get("code") or ""),
                str(row.get("published_at") or ""),
                str(row.get("announcement_id") or ""),
            ),
        )
        if stable_hash(replay_records) != stable_hash(expected_records):
            errors.append("records_replay_mismatch")
        contract = validate_exchange_announcement_records(
            source,
            expected_records,
            feature_cutoff=str(payload.get("feature_cutoff") or ""),
            codes=payload.get("requested_codes") or [],
        )
        if not contract["valid"]:
            errors.extend(f"record_contract:{item}" for item in contract["errors"])

    passed = bool(payload.get("provider_validation_passed"))
    capability_result = payload.get("capability_result") or {}
    if passed:
        if payload.get("status") != "EXCHANGE_ANNOUNCEMENT_NETWORK_VALIDATED":
            errors.append("validated_status_mismatch")
        if capability_result.get("status") != (
            "EXCHANGE_ANNOUNCEMENT_SOURCE_VALIDATED"
        ):
            errors.append("capability_status_mismatch")
    elif payload.get("status") != (
        "EXCHANGE_ANNOUNCEMENT_NETWORK_VALIDATION_FAILED"
    ):
        errors.append("failure_status_mismatch")

    verified = not errors
    return _safe(
        {
            "status": EVIDENCE_VERIFIED if verified else EVIDENCE_INVALID,
            "execution_ok": True,
            "source": source,
            "evidence_integrity_verified": verified,
            "provider_validation_passed": passed if verified else False,
            "evidence_hash": str(payload.get("evidence_hash") or ""),
            "replay_hash": stable_hash(
                {
                    "source": source,
                    "records": replay_records,
                    "response_hashes": [
                        response.get("raw_hash") for response in responses
                    ],
                }
            ) if verified else "",
            "errors": sorted(set(errors)),
            "external_file_sha256": expected_file_sha256,
        }
    )


def verify_file(
    path: str | Path,
    *,
    expected_file_sha256: str,
) -> dict[str, Any]:
    file_path = _resolve_cache_input(path)
    raw = file_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_file_sha256:
        return _safe(
            {
                "status": EVIDENCE_INVALID,
                "execution_ok": True,
                "evidence_integrity_verified": False,
                "provider_validation_passed": False,
                "evidence_hash": "",
                "replay_hash": "",
                "errors": ["external_file_sha256_mismatch"],
                "external_file_sha256": expected_file_sha256,
                "actual_file_sha256": actual,
            }
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _safe(
            {
                "status": EVIDENCE_INVALID,
                "execution_ok": True,
                "evidence_integrity_verified": False,
                "provider_validation_passed": False,
                "evidence_hash": "",
                "replay_hash": "",
                "errors": ["strict_utf8_json_required"],
                "external_file_sha256": expected_file_sha256,
                "actual_file_sha256": actual,
            }
        )
    return verify_exchange_announcement_evidence(
        payload,
        expected_file_sha256=expected_file_sha256,
    ) | {"actual_file_sha256": actual}


def write_replay_atomic(target: str | Path, payload: dict[str, Any]) -> Path:
    from overnight_quant.scripts.run_exchange_announcement_validation import (
        write_json_atomic,
    )

    return write_json_atomic(target, payload)


def _safe(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def _resolve_cache_input(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = CACHE_ROOT / path
    resolved = path.resolve()
    if CACHE_ROOT != resolved and CACHE_ROOT not in resolved.parents:
        raise ValueError("exchange_announcement_input_must_be_in_ignored_cache")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify official exchange announcement evidence"
    )
    parser.add_argument("evidence")
    parser.add_argument("--expected-file-sha256", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = verify_file(
        args.evidence,
        expected_file_sha256=args.expected_file_sha256,
    )
    if args.output:
        write_replay_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == EVIDENCE_VERIFIED else 2


if __name__ == "__main__":
    raise SystemExit(main())

