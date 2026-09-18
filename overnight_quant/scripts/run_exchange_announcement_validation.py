from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.exchange_announcement_providers import (
    EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION,
    ExchangeAnnouncementContractError,
    ExchangeAnnouncementProviders,
    ExchangeUrllibTransport,
    PROVIDER_KEYS,
    SOURCE_IDENTITIES,
    compute_exchange_announcement_verifier_contract_hash,
    validate_exchange_announcement_records,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
)


DEFAULT_CODES = {
    "sse": ("600000", "600519", "601318"),
    "szse": ("000001", "000333"),
    "bse": ("920925",),
}
CACHE_ROOT = (ROOT / "overnight_quant" / "data" / "cache").resolve()


def run_exchange_announcement_validation(
    *,
    network: bool,
    source: str,
    trade_date: str,
    codes: Iterable[str] | None = None,
    output: str | Path | None = None,
    timeout_seconds: float = 10.0,
    transport: Any | None = None,
    clock: Any | None = None,
) -> dict[str, Any]:
    requested_codes = tuple(codes or DEFAULT_CODES.get(source, ()))
    if source not in SOURCE_IDENTITIES:
        return _safe(
            {
                "status": "EXCHANGE_ANNOUNCEMENT_REQUEST_INVALID",
                "execution_ok": False,
                "reason": "unknown_source",
                "network_mode": bool(network),
                "network_requests_made": 0,
                "evidence_hash": "",
            }
        )
    if not network:
        return _safe(
            {
                "status": "EXCHANGE_ANNOUNCEMENT_NETWORK_NOT_REQUESTED",
                "execution_ok": True,
                "source": source,
                "network_mode": False,
                "network_requests_made": 0,
                "evidence_hash": "",
            }
        )
    if output is None:
        return _safe(
            {
                "status": "EXCHANGE_ANNOUNCEMENT_REQUEST_INVALID",
                "execution_ok": False,
                "reason": "ignored_cache_output_required",
                "source": source,
                "network_mode": True,
                "network_requests_made": 0,
                "evidence_hash": "",
            }
        )
    target = _resolve_cache_output(output)
    if target.exists():
        raise FileExistsError(f"exchange_announcement_output_exists:{target}")

    active_transport = transport or ExchangeUrllibTransport()
    active_clock = clock or (lambda: datetime.now(CN_TZ))
    cutoff = f"{trade_date}T14:50:00+08:00"
    provider = ExchangeAnnouncementProviders(
        requested_codes,
        feature_cutoff=cutoff,
        transport=active_transport,
        clock=active_clock,
        timeout_seconds=timeout_seconds,
    )
    started = datetime.now(CN_TZ)
    batch = None
    error_code = ""
    captured_response = None
    try:
        batch = getattr(provider, f"collect_{source}_batch")()
        records = list(batch.records)
        contract = validate_exchange_announcement_records(
            source,
            records,
            feature_cutoff=cutoff,
            codes=requested_codes,
        )
        passed = bool(contract["valid"])
        result = {
            "status": (
                "EXCHANGE_ANNOUNCEMENT_SOURCE_VALIDATED"
                if passed
                else "EXCHANGE_ANNOUNCEMENT_SOURCE_REJECTED"
            ),
            "batch_status": batch.status,
            "record_count": len(records),
            "records_hash": contract["records_hash"],
            "contract_status": contract["status"],
            "errors": contract["errors"],
        }
        responses = list(batch.responses)
    except (ExchangeAnnouncementContractError, ValueError, KeyError) as exc:
        passed = False
        records = []
        error_code = getattr(exc, "code", type(exc).__name__)
        captured_response = getattr(exc, "response_evidence", None)
        responses = [captured_response] if captured_response is not None else []
        result = {
            "status": "EXCHANGE_ANNOUNCEMENT_SOURCE_FAILED",
            "error_code": error_code,
            "record_count": 0,
            "response_captured": captured_response is not None,
        }

    result["started_at"] = started.isoformat(timespec="microseconds")
    result["completed_at"] = datetime.now(CN_TZ).isoformat(
        timespec="microseconds"
    )
    request_count = getattr(active_transport, "request_count", None)
    evidence = _safe(
        {
            "evidence_schema_version": (
                EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION
            ),
            "status": (
                "EXCHANGE_ANNOUNCEMENT_NETWORK_VALIDATED"
                if passed
                else "EXCHANGE_ANNOUNCEMENT_NETWORK_VALIDATION_FAILED"
            ),
            "execution_ok": True,
            "network_mode": True,
            "source": source,
            "origin_source": SOURCE_IDENTITIES[source][0],
            "source_version": SOURCE_IDENTITIES[source][2],
            "provider_key": PROVIDER_KEYS[source],
            "trade_date": trade_date,
            "feature_cutoff": cutoff,
            "requested_codes": sorted(requested_codes),
            "producer_commit_sha": _git_head(),
            "provider_verifier_contract_hash": (
                compute_exchange_announcement_verifier_contract_hash()
            ),
            "capability_registry_hash": (
                compute_source_capability_registry_hash()
            ),
            "capability_result": result,
            "records": sorted(
                records,
                key=lambda row: (
                    row["code"],
                    row["published_at"],
                    row["announcement_id"],
                ),
            ),
            "raw_responses": [_serialize_response(item) for item in responses],
            "network_requests_made": (
                request_count if type(request_count) is int else None
            ),
            "upstream_network_activity": (
                "measured" if type(request_count) is int else "unknown"
            ),
            "provider_validation_passed": passed,
            "evidence_integrity_verified": False,
            "error_code": error_code,
            "evidence_hash": "",
        }
    )
    evidence["evidence_hash"] = compute_evidence_hash(evidence)
    write_json_atomic(target, evidence)
    return evidence


def compute_evidence_hash(evidence: dict[str, Any]) -> str:
    material = dict(evidence)
    material.pop("evidence_hash", None)
    return stable_hash(material)


def write_json_atomic(target: str | Path, payload: dict[str, Any]) -> Path:
    path = _resolve_cache_output(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _serialize_response(item: Mapping[str, Any]) -> dict[str, Any]:
    raw = item["raw_bytes"]
    return {
        key: value for key, value in item.items() if key != "raw_bytes"
    } | {
        "encoding": "base64",
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "byte_count": len(raw),
    }


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


def _resolve_cache_output(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = CACHE_ROOT / path
    resolved = path.resolve()
    if CACHE_ROOT != resolved and CACHE_ROOT not in resolved.parents:
        raise ValueError("exchange_announcement_output_must_be_in_ignored_cache")
    return resolved


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate one official exchange announcement source"
    )
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--source", required=True, choices=sorted(SOURCE_IDENTITIES))
    parser.add_argument("--date", required=True)
    parser.add_argument("--codes")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    codes = (
        tuple(code.strip() for code in args.codes.split(",") if code.strip())
        if args.codes
        else DEFAULT_CODES[args.source]
    )
    try:
        result = run_exchange_announcement_validation(
            network=args.network,
            source=args.source,
            trade_date=args.date,
            codes=codes,
            output=args.output,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("provider_validation_passed") is True else 2
    except Exception as exc:
        print(
            json.dumps(
                _safe(
                    {
                        "status": "EXCHANGE_ANNOUNCEMENT_VALIDATION_FAILED",
                        "execution_ok": False,
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

