from __future__ import annotations

import argparse
import base64
from collections import deque
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_source_providers import (
    EVIDENCE_SCHEMA_VERSION,
    FIXED_CODES,
    LEGACY_EVIDENCE_SCHEMA_VERSION,
    PRODUCTION_TRANSPORT_VERSION,
    PREVIOUS_EVIDENCE_SCHEMA_VERSION,
    SECONDARY_PREVIOUS_EVIDENCE_SCHEMA_VERSION,
    PROVIDER_KEYS,
    EastmoneyMarketSourceProviders,
    MarketHttpResponse,
    MarketSourceContractError,
    compute_legacy_market_source_verifier_contract_hash,
    compute_previous_market_source_verifier_contract_hash,
    compute_v2_market_source_verifier_contract_hash,
    compute_market_source_verifier_contract_hash,
    validate_market_source_records,
)
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
    validate_source_provenance_batch,
)
from overnight_quant.scripts.run_market_source_validation import (
    compute_evidence_hash,
    write_json_atomic,
)


EVIDENCE_VERIFIED = "MARKET_SOURCE_EVIDENCE_VERIFIED"
EVIDENCE_INVALID = "MARKET_SOURCE_EVIDENCE_INVALID"
LEGACY_CAPABILITY_REGISTRY_HASH = (
    "f4e91460aa67674ce536b70184d85b05cfc6fedc8526fa27f16e3f3f616fd833"
)


class ReplayTransport:
    def __init__(
        self,
        capability: str,
        responses: list[Mapping[str, Any]],
    ) -> None:
        self.capability = capability
        self.responses = deque(dict(item) for item in responses)
        self.request_count = 0

    def request(self, method, url, *, params, headers, timeout_seconds):
        self.request_count += 1
        if not self.responses:
            raise ValueError("replay_response_missing")
        item = self.responses.popleft()
        expected_params = sorted((str(key), str(value)) for key, value in params.items())
        expected_request_hash = stable_hash(
            {"method": method, "url": url, "params": expected_params}
        )
        if item.get("capability") != self.capability:
            raise ValueError("replay_capability_mismatch")
        if item.get("method") != method or item.get("url") != url or [tuple(value) for value in item.get("params") or []] != expected_params:
            raise ValueError("replay_request_mismatch")
        if item.get("request_hash") != expected_request_hash:
            raise ValueError("replay_request_hash_mismatch")
        if item.get("transport_version") != PRODUCTION_TRANSPORT_VERSION:
            raise ValueError("replay_transport_version_mismatch")
        request_started = parse_cn_datetime(item.get("request_started_at"))
        request_completed = parse_cn_datetime(item.get("request_completed_at"))
        if (
            request_started is None
            or request_completed is None
            or request_completed < request_started
        ):
            raise ValueError("replay_request_time_invalid")
        raw = base64.b64decode(item.get("content_base64") or "", validate=True)
        if len(raw) != item.get("byte_count") or hashlib.sha256(raw).hexdigest() != item.get("raw_hash"):
            raise ValueError("replay_raw_hash_mismatch")
        return MarketHttpResponse(
            raw,
            item.get("http_status_code"),
            item.get("response_url"),
            {},
            item.get("request_started_at"),
            item.get("request_completed_at"),
            item.get("transport_version"),
            item.get("process_started_at") or "",
            item.get("process_completed_at") or "",
            item.get("process_elapsed_ms"),
            item.get("worker_terminated") is True,
        )


class ReplayClock:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        values = []
        for item in responses:
            values.extend([item["observed_at"], item["available_at"]])
        self.values = deque(datetime.fromisoformat(value) for value in values)

    def __call__(self) -> datetime:
        if not self.values:
            raise ValueError("replay_clock_exhausted")
        return self.values.popleft()


def verify_market_source_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_file_sha256: str | None,
) -> dict[str, Any]:
    payload = dict(evidence)
    if payload.get("evidence_schema_version") in {
        LEGACY_EVIDENCE_SCHEMA_VERSION,
        PREVIOUS_EVIDENCE_SCHEMA_VERSION,
        SECONDARY_PREVIOUS_EVIDENCE_SCHEMA_VERSION,
    }:
        return _verify_legacy_evidence(
            payload,
            expected_file_sha256=expected_file_sha256,
        )
    errors: list[str] = []
    if payload.get("evidence_schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if payload.get("evidence_hash") != compute_evidence_hash(payload):
        errors.append("evidence_hash_mismatch")
    if payload.get("provider_keys") != PROVIDER_KEYS:
        errors.append("provider_keys_mismatch")
    if payload.get("transport_version") != PRODUCTION_TRANSPORT_VERSION:
        errors.append("transport_version_mismatch")
    if payload.get("requested_codes") != list(FIXED_CODES):
        errors.append("fixed_codes_mismatch")
    if payload.get("provider_verifier_contract_hash") != compute_market_source_verifier_contract_hash():
        errors.append("verifier_contract_hash_mismatch")
    if payload.get("capability_registry_hash") != compute_source_capability_registry_hash():
        errors.append("capability_registry_hash_mismatch")
    trade_date = str(payload.get("trade_date") or "")
    evidence_scope = payload.get("evidence_scope")
    if evidence_scope not in {"qualification", "audit_only"}:
        errors.append("evidence_scope_invalid")
    expected_cutoff = parse_cn_datetime(
        f"{trade_date}T14:50:00+08:00"
        if evidence_scope == "qualification"
        else payload.get("feature_cutoff")
    )
    expected_deadline = parse_cn_datetime(
        f"{trade_date}T14:51:05+08:00"
        if evidence_scope == "qualification"
        else payload.get("collection_deadline")
    )
    if (
        expected_cutoff is None
        or parse_cn_datetime(payload.get("feature_cutoff")) != expected_cutoff
    ):
        errors.append("feature_cutoff_contract_mismatch")
    if (
        expected_deadline is None
        or parse_cn_datetime(payload.get("collection_deadline"))
        != expected_deadline
    ):
        errors.append("collection_deadline_contract_mismatch")
    capability_feature_cutoffs = payload.get("capability_feature_cutoffs")
    expected_capabilities = {"market_breadth", "industry_snapshot", "fund_flow"}
    if (
        not isinstance(capability_feature_cutoffs, Mapping)
        or set(capability_feature_cutoffs) != expected_capabilities
    ):
        errors.append("capability_feature_cutoffs_mismatch")
        capability_feature_cutoffs = {
            capability: payload.get("feature_cutoff")
            for capability in expected_capabilities
        }
    else:
        parsed_capability_cutoffs = {
            capability: parse_cn_datetime(value)
            for capability, value in capability_feature_cutoffs.items()
        }
        if any(value is None for value in parsed_capability_cutoffs.values()):
            errors.append("capability_feature_cutoff_invalid")
        if evidence_scope == "qualification" and any(
            value != expected_cutoff
            for value in parsed_capability_cutoffs.values()
        ):
            errors.append("qualification_capability_cutoff_mismatch")
        if (
            evidence_scope == "audit_only"
            and parsed_capability_cutoffs.get("fund_flow") != expected_cutoff
        ):
            errors.append("audit_fund_flow_cutoff_mismatch")
    for field in (
        "capability_results",
        "records_by_capability",
        "raw_responses",
        "transport_attempts",
    ):
        value = payload.get(field)
        if not isinstance(value, Mapping) or set(value) != expected_capabilities:
            errors.append(f"{field}_capabilities_mismatch")
    for key in ("data_ready", "hard_gate_authorized", "automatic_configuration_change", "automatic_qualification_change"):
        if payload.get(key) is not False:
            errors.append(f"safety_flag_invalid:{key}")
    for key in ("candidates", "tickets", "orders"):
        if payload.get(key) != []:
            errors.append(f"safety_output_invalid:{key}")
    replayed_by_capability: dict[str, list[dict[str, Any]]] = {}
    validated_capabilities: set[str] = set()
    for capability, method_name in (
        ("market_breadth", "collect_market_breadth_batch"),
        ("industry_snapshot", "collect_industry_batch"),
        ("fund_flow", "collect_fund_flow_batch"),
    ):
        result = (payload.get("capability_results") or {}).get(capability) or {}
        original = list((payload.get("records_by_capability") or {}).get(capability) or [])
        responses = list((payload.get("raw_responses") or {}).get(capability) or [])
        if result.get("status") == "MARKET_SOURCE_CAPABILITY_FAILED":
            if original:
                errors.append(f"{capability}:failed_batch_records_not_empty")
            replayed_by_capability[capability] = []
            continue
        try:
            transport = ReplayTransport(capability, responses)
            provider = EastmoneyMarketSourceProviders(
                payload.get("requested_codes") or [],
                trade_date=payload.get("trade_date"),
                feature_cutoff=capability_feature_cutoffs.get(capability),
                collection_deadline=payload.get("collection_deadline"),
                transport=transport,
                clock=ReplayClock(responses),
            )
            try:
                batch = getattr(provider, method_name)()
            except MarketSourceContractError as exc:
                if result.get("status") != "EASTMONEY_PROVIDER_CONTRACT_INCOMPLETE":
                    raise
                if result.get("provider_error_code") != exc.code:
                    errors.append(f"{capability}:provider_error_code_mismatch")
                if original:
                    errors.append(f"{capability}:incomplete_batch_records_not_empty")
                if transport.responses:
                    errors.append(f"{capability}:unused_responses")
                replayed_by_capability[capability] = []
                continue
            replayed = list(batch.records)
            replayed_by_capability[capability] = replayed
            contract = validate_market_source_records(capability, replayed)
            if not contract["valid"]:
                errors.extend(f"{capability}:{error}" for error in contract["errors"])
            provenance = validate_source_provenance_batch(
                capability,
                replayed,
                require_hard_gate=False,
                environ={},
            )
            if provenance["status"] != "SOURCE_PROVENANCE_ACCEPTED":
                errors.append(
                    f"{capability}:provenance:{provenance['status']}"
                )
            if replayed != original:
                errors.append(f"{capability}:records_replay_mismatch")
            original_contract = validate_market_source_records(capability, original)
            if not original_contract["valid"]:
                errors.extend(
                    f"{capability}:{error}"
                    for error in original_contract["errors"]
                )
            if result.get("status") != "MARKET_SOURCE_CAPABILITY_VALIDATED":
                errors.append(f"{capability}:capability_status_mismatch")
            if result.get("record_count") != len(original):
                errors.append(f"{capability}:record_count_mismatch")
            if result.get("records_hash") != original_contract["records_hash"]:
                errors.append(f"{capability}:records_hash_mismatch")
            if result.get("provenance_status") != "SOURCE_PROVENANCE_ACCEPTED":
                errors.append(f"{capability}:provenance_status_mismatch")
            if contract["valid"] and provenance["status"] == "SOURCE_PROVENANCE_ACCEPTED":
                validated_capabilities.add(capability)
            if transport.responses:
                errors.append(f"{capability}:unused_responses")
        except Exception as exc:
            errors.append(f"{capability}:replay_failed:{type(exc).__name__}:{exc}")
    if not expected_file_sha256:
        errors.append("external_sha256_anchor_required")
    expected_provider_passed = validated_capabilities == expected_capabilities
    if payload.get("provider_validation_passed") is not expected_provider_passed:
        errors.append("provider_validation_summary_mismatch")
    if payload.get("provider_contract_complete") is not expected_provider_passed:
        errors.append("provider_contract_summary_mismatch")
    expected_transport_qualified = all(
        ((payload.get("capability_results") or {}).get(capability) or {}).get(
            "transport_qualified"
        )
        is True
        for capability in expected_capabilities
    )
    if payload.get("transport_qualified") is not expected_transport_qualified:
        errors.append("transport_qualification_summary_mismatch")
    if payload.get("source_qualification_count") != 0:
        errors.append("source_qualification_count_changed")
    verified = not errors and bool(expected_file_sha256)
    replay_hash = stable_hash({
        "evidence_hash": payload.get("evidence_hash"),
        "records_by_capability": replayed_by_capability,
        "errors": errors,
    })
    return {
        "status": EVIDENCE_VERIFIED if verified else EVIDENCE_INVALID,
        "execution_ok": True,
        "evidence_integrity_verified": verified,
        "provider_validation_passed": bool(payload.get("provider_validation_passed")) if verified else False,
        "audit_only": evidence_scope == "audit_only",
        "qualification_eligible": bool(
            verified
            and evidence_scope == "qualification"
            and payload.get("provider_validation_passed") is True
        ),
        "expected_file_sha256": expected_file_sha256 or "",
        "evidence_hash": payload.get("evidence_hash") or "",
        "replay_hash": replay_hash,
        "errors": errors,
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [], "tickets": [], "orders": [],
    }


def _verify_legacy_evidence(
    payload: Mapping[str, Any],
    *,
    expected_file_sha256: str | None,
) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("evidence_hash") != compute_evidence_hash(payload):
        errors.append("evidence_hash_mismatch")
    if payload.get("provider_keys") != PROVIDER_KEYS:
        errors.append("provider_keys_mismatch")
    if payload.get("requested_codes") != list(FIXED_CODES):
        errors.append("fixed_codes_mismatch")
    schema = payload.get("evidence_schema_version")
    expected_contract_hash = {
        LEGACY_EVIDENCE_SCHEMA_VERSION: (
            compute_legacy_market_source_verifier_contract_hash()
        ),
        SECONDARY_PREVIOUS_EVIDENCE_SCHEMA_VERSION: (
            compute_v2_market_source_verifier_contract_hash()
        ),
        PREVIOUS_EVIDENCE_SCHEMA_VERSION: (
            compute_previous_market_source_verifier_contract_hash()
        ),
    }.get(schema)
    if payload.get("provider_verifier_contract_hash") != expected_contract_hash:
        errors.append("legacy_verifier_contract_hash_mismatch")
    expected_registry_hash = {
        LEGACY_EVIDENCE_SCHEMA_VERSION: LEGACY_CAPABILITY_REGISTRY_HASH,
        SECONDARY_PREVIOUS_EVIDENCE_SCHEMA_VERSION: (
            "eba7d83802c164ff747d0271d2b799724bbc15ee9886c031b3db59511f8d15ed"
        ),
        PREVIOUS_EVIDENCE_SCHEMA_VERSION: (
            "500b99686e856770a340a8319bdb695a05c7e239c0b30256246bf301aace0d1e"
        ),
    }.get(schema)
    if payload.get("capability_registry_hash") != expected_registry_hash:
        errors.append("legacy_capability_registry_hash_mismatch")
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
    if not expected_file_sha256:
        errors.append("external_sha256_anchor_required")
    verified = not errors and bool(expected_file_sha256)
    return {
        "status": EVIDENCE_VERIFIED if verified else EVIDENCE_INVALID,
        "execution_ok": True,
        "evidence_integrity_verified": verified,
        "provider_validation_passed": False,
        "audit_only": True,
        "qualification_eligible": False,
        "expected_file_sha256": expected_file_sha256 or "",
        "evidence_hash": payload.get("evidence_hash") or "",
        "replay_hash": stable_hash({
            "evidence_hash": payload.get("evidence_hash"),
            "audit_only": True,
            "errors": errors,
        }),
        "errors": errors,
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


def verify_file(path: str | Path, *, expected_file_sha256: str) -> dict[str, Any]:
    target = Path(path).resolve()
    raw = target.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_file_sha256:
        return {
            "status": EVIDENCE_INVALID,
            "execution_ok": True,
            "evidence_integrity_verified": False,
            "provider_validation_passed": False,
            "expected_file_sha256": expected_file_sha256,
            "actual_file_sha256": actual,
            "errors": ["external_sha256_anchor_mismatch"],
            "automatic_configuration_change": False,
            "data_ready": False,
            "hard_gate_authorized": False,
            "candidates": [], "tickets": [], "orders": [],
        }
    result = verify_market_source_evidence(
        json.loads(raw.decode("utf-8")),
        expected_file_sha256=expected_file_sha256,
    )
    result["actual_file_sha256"] = actual
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify immutable S2 evidence")
    parser.add_argument("path")
    parser.add_argument("--expected-file-sha256", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = verify_file(args.path, expected_file_sha256=args.expected_file_sha256)
    if args.output:
        write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == EVIDENCE_VERIFIED else 2


if __name__ == "__main__":
    raise SystemExit(main())
