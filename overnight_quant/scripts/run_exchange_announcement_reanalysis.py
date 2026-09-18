from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.exchange_announcement_providers import (
    EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_V1,
    EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION,
    compute_exchange_announcement_verifier_contract_hash,
    replay_exchange_announcement_response_sets,
    validate_exchange_announcement_audit_records,
    validate_exchange_announcement_records,
)
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
)
from overnight_quant.scripts.run_exchange_announcement_evidence_verify import (
    EVIDENCE_VERIFIED,
    verify_exchange_announcement_evidence,
)
from overnight_quant.scripts.run_exchange_announcement_validation import (
    CACHE_ROOT,
    _git_head,
    _safe,
    compute_evidence_hash,
    write_json_atomic,
)


def build_exchange_announcement_reanalysis(
    source_path: str | Path,
    *,
    expected_file_sha256: str,
) -> dict[str, Any]:
    path = _resolve_cache_input(source_path)
    raw_file = path.read_bytes()
    actual_file_sha256 = hashlib.sha256(raw_file).hexdigest()
    if actual_file_sha256 != expected_file_sha256:
        return _invalid("external_file_sha256_mismatch", actual_file_sha256)
    try:
        source_evidence = json.loads(raw_file.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _invalid("strict_utf8_json_required", actual_file_sha256)
    if source_evidence.get("evidence_schema_version") != (
        EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_V1
    ):
        return _invalid("v1_source_evidence_required", actual_file_sha256)
    source_verification = verify_exchange_announcement_evidence(
        source_evidence,
        expected_file_sha256=expected_file_sha256,
    )
    if source_verification.get("status") != EVIDENCE_VERIFIED:
        return _invalid("source_evidence_invalid", actual_file_sha256)

    source = str(source_evidence["source"])
    responses = [_decode_response(item) for item in source_evidence["raw_responses"]]
    records, audit_records, replay_errors = (
        replay_exchange_announcement_response_sets(
            source,
            responses,
            feature_cutoff=source_evidence["feature_cutoff"],
            codes=source_evidence["requested_codes"],
            contract_version="v2",
        )
    )
    record_contract = validate_exchange_announcement_records(
        source,
        records,
        feature_cutoff=source_evidence["feature_cutoff"],
        codes=source_evidence["requested_codes"],
    )
    audit_contract = validate_exchange_announcement_audit_records(
        source,
        audit_records,
        feature_cutoff=source_evidence["feature_cutoff"],
        codes=source_evidence["requested_codes"],
    )
    passed = bool(
        source_evidence.get("provider_validation_passed") is True
        and not replay_errors
        and record_contract["valid"]
        and audit_contract["valid"]
    )
    capability_result = {
        "status": (
            "EXCHANGE_ANNOUNCEMENT_SOURCE_VALIDATED"
            if passed
            else "EXCHANGE_ANNOUNCEMENT_SOURCE_REJECTED"
        ),
        "batch_status": "AVAILABLE" if records else "AVAILABLE_EMPTY",
        "record_count": len(records),
        "records_hash": record_contract["records_hash"],
        "audit_record_count": len(audit_records),
        "audit_records_hash": audit_contract["records_hash"],
        "contract_status": record_contract["status"],
        "audit_contract_status": audit_contract["status"],
        "errors": (
            list(replay_errors)
            + record_contract["errors"]
            + audit_contract["errors"]
        ),
    }
    derived = _safe(
        {
            **{
                key: source_evidence[key]
                for key in (
                    "execution_ok",
                    "network_mode",
                    "source",
                    "origin_source",
                    "source_version",
                    "provider_key",
                    "trade_date",
                    "feature_cutoff",
                    "requested_codes",
                    "network_requests_made",
                    "upstream_network_activity",
                )
            },
            "evidence_schema_version": (
                EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION
            ),
            "status": (
                "EXCHANGE_ANNOUNCEMENT_NETWORK_VALIDATED"
                if passed
                else "EXCHANGE_ANNOUNCEMENT_NETWORK_VALIDATION_FAILED"
            ),
            "producer_commit_sha": _git_head(),
            "provider_verifier_contract_hash": (
                compute_exchange_announcement_verifier_contract_hash()
            ),
            "capability_registry_hash": (
                compute_source_capability_registry_hash()
            ),
            "capability_result": capability_result,
            "records": records,
            "audit_records": audit_records,
            "raw_responses": source_evidence["raw_responses"],
            "provider_validation_passed": passed,
            "evidence_integrity_verified": False,
            "error_code": "" if passed else "REANALYSIS_CONTRACT_REJECTED",
            "derived_from": {
                "schema_version": EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_V1,
                "file_sha256": actual_file_sha256,
                "evidence_hash": source_evidence["evidence_hash"],
            },
            "evidence_hash": "",
        }
    )
    derived["evidence_hash"] = compute_evidence_hash(derived)
    return derived


def _decode_response(item: dict[str, Any]) -> dict[str, Any]:
    raw = base64.b64decode(item["content_base64"], validate=True)
    if len(raw) != item.get("byte_count"):
        raise ValueError("response_byte_count_mismatch")
    return {
        key: value
        for key, value in item.items()
        if key not in {"content_base64", "encoding", "byte_count"}
    } | {"raw_bytes": raw}


def _invalid(reason: str, actual_file_sha256: str) -> dict[str, Any]:
    return _safe(
        {
            "status": "EXCHANGE_ANNOUNCEMENT_REANALYSIS_INPUT_INVALID",
            "execution_ok": False,
            "reason": reason,
            "actual_file_sha256": actual_file_sha256,
            "provider_validation_passed": False,
            "evidence_hash": "",
        }
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
        description="Reanalyse immutable v1 exchange announcement evidence"
    )
    parser.add_argument("evidence")
    parser.add_argument("--expected-file-sha256", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = build_exchange_announcement_reanalysis(
        args.evidence,
        expected_file_sha256=args.expected_file_sha256,
    )
    if result.get("status") != "EXCHANGE_ANNOUNCEMENT_REANALYSIS_INPUT_INVALID":
        write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("provider_validation_passed") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
