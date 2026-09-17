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

from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
)
from overnight_quant.data.static_source_providers import (
    PROVIDER_KEYS,
    STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION,
    StaticHttpResponse,
    StaticSourceProviders,
    compute_static_source_verifier_contract_hash,
)
from overnight_quant.scripts.run_static_source_validation import (
    compute_evidence_hash,
    write_json_atomic,
)


EVIDENCE_VERIFIED = "STATIC_SOURCE_EVIDENCE_VERIFIED"
EVIDENCE_INVALID = "STATIC_SOURCE_EVIDENCE_INVALID"


class _ReplayTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = deque(dict(item) for item in responses)
        self.request_count = 0

    def request(self, method, url, *, params, data, headers, timeout_seconds):
        self.request_count += 1
        if not self.responses:
            raise ValueError("replay_response_missing")
        item = self.responses.popleft()
        expected_pairs = lambda value: sorted((str(k), str(v)) for k, v in (value or {}).items())
        recorded_params = [tuple(value) for value in (item.get("params") or [])]
        recorded_data = [tuple(value) for value in (item.get("data") or [])]
        if (
            item.get("method") != method
            or item.get("url") != url
            or recorded_params != expected_pairs(params)
            or recorded_data != expected_pairs(data)
        ):
            raise ValueError("replay_request_mismatch")
        raw = base64.b64decode(item.get("content_base64") or "", validate=True)
        if len(raw) != item.get("byte_count"):
            raise ValueError("replay_byte_count_mismatch")
        if hashlib.sha256(raw).hexdigest() != item.get("raw_hash"):
            raise ValueError("replay_raw_hash_mismatch")
        return StaticHttpResponse(
            content=raw,
            status_code=item.get("http_status_code"),
            url=item.get("response_url"),
        )


class _ReplayClock:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        values = []
        for item in responses:
            values.extend([item["observed_at"], item["available_at"]])
        self.values = deque(datetime.fromisoformat(value) for value in values)

    def __call__(self) -> datetime:
        if not self.values:
            raise ValueError("replay_clock_exhausted")
        return self.values.popleft()


def verify_static_source_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_file_sha256: str | None,
) -> dict[str, Any]:
    errors: list[str] = []
    payload = dict(evidence)
    if payload.get("evidence_schema_version") != STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if payload.get("evidence_hash") != compute_evidence_hash(payload):
        errors.append("evidence_hash_mismatch")
    if payload.get("provider_keys") != PROVIDER_KEYS:
        errors.append("provider_keys_mismatch")
    if payload.get("provider_verifier_contract_hash") != compute_static_source_verifier_contract_hash():
        errors.append("verifier_contract_hash_mismatch")
    if payload.get("capability_registry_hash") != compute_source_capability_registry_hash():
        errors.append("capability_registry_hash_mismatch")
    for key in ("data_ready", "hard_gate_authorized", "automatic_configuration_change"):
        if payload.get(key) is not False:
            errors.append(f"safety_flag_invalid:{key}")
    for key in ("candidates", "tickets", "orders"):
        if payload.get(key) != []:
            errors.append(f"safety_output_invalid:{key}")

    replay_records: dict[str, list[dict[str, Any]]] = {}
    calendar_dates: list[str] = []
    order = ("trading_calendar", "daily_bar_qfq", "stock_news", "global_news", "announcement")
    for capability in order:
        responses = list((payload.get("raw_responses") or {}).get(capability) or [])
        original_records = list((payload.get("records_by_capability") or {}).get(capability) or [])
        result = (payload.get("capability_results") or {}).get(capability) or {}
        if result.get("status") == "STATIC_SOURCE_CAPABILITY_FAILED":
            if responses or original_records:
                errors.append(f"{capability}:failed_payload_not_empty")
            replay_records[capability] = []
            continue
        try:
            transport = _ReplayTransport(responses)
            provider = StaticSourceProviders(
                payload.get("requested_codes") or [],
                target_trade_date=payload.get("trade_date"),
                feature_cutoff=payload.get("feature_cutoff"),
                transport=transport,
                clock=_ReplayClock(responses),
            )
            if capability == "trading_calendar":
                batch = provider.collect_trading_calendar_batch()
                if batch.records:
                    calendar_dates = list(batch.records[0]["payload"]["trade_dates"])
            elif capability == "daily_bar_qfq":
                batch = provider.collect_qfq_daily_batch(calendar_dates)
            else:
                batch = getattr(provider, f"collect_{capability}_batch")()
            replayed = list(batch.records)
            replay_records[capability] = replayed
            if replayed != original_records:
                errors.append(f"{capability}:records_replay_mismatch")
            if stable_hash(replayed) != stable_hash(original_records):
                errors.append(f"{capability}:records_hash_mismatch")
            if transport.responses:
                errors.append(f"{capability}:unused_responses")
        except Exception as exc:
            errors.append(f"{capability}:replay_failed:{type(exc).__name__}:{exc}")

    verified = not errors and bool(expected_file_sha256)
    if not expected_file_sha256:
        errors.append("external_sha256_anchor_required")
    replay_hash = stable_hash({
        "evidence_hash": payload.get("evidence_hash"),
        "records_by_capability": replay_records,
        "errors": errors,
    })
    return {
        "status": EVIDENCE_VERIFIED if verified else EVIDENCE_INVALID,
        "execution_ok": True,
        "evidence_integrity_verified": verified,
        "provider_validation_passed": bool(payload.get("provider_validation_passed")) if verified else False,
        "expected_file_sha256": expected_file_sha256 or "",
        "evidence_hash": payload.get("evidence_hash") or "",
        "replay_hash": replay_hash,
        "errors": errors,
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [], "tickets": [], "orders": [],
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
    result = verify_static_source_evidence(
        json.loads(raw.decode("utf-8")),
        expected_file_sha256=expected_file_sha256,
    )
    result["actual_file_sha256"] = actual
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify immutable S1 evidence")
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
