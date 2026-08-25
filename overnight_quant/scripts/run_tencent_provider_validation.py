from __future__ import annotations

import argparse
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Callable, Iterable
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    validate_source_provenance_batch,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_QUOTE_ENDPOINT,
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION,
    TENCENT_VALUATION_PROVIDER_KEY,
    TencentDirectHttpProviders,
    TencentProviderContractError,
    TencentUrllibTransport,
)


DEFAULT_CODES = ("000001", "000333", "600000", "600519", "601318")
CACHE_ROOT = (ROOT / "overnight_quant" / "data" / "cache").resolve()


def run_tencent_provider_validation(
    *,
    network: bool,
    codes: Iterable[str] = DEFAULT_CODES,
    output: str | Path | None = None,
    timeout_seconds: float = 5.0,
    transport: Any | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    requested_codes = tuple(str(code) for code in codes)
    if not network:
        return _safe_result(
            {
                "status": "TENCENT_PROVIDER_NETWORK_NOT_REQUESTED",
                "execution_ok": True,
                "network_mode": False,
                "evidence_schema_version": (
                    TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION
                ),
                "endpoint": TENCENT_QUOTE_ENDPOINT,
                "requested_codes": sorted(requested_codes),
                "provider_keys": {
                    "quote": TENCENT_QUOTE_PROVIDER_KEY,
                    "valuation": TENCENT_VALUATION_PROVIDER_KEY,
                },
                "network_requests_made": 0,
                "upstream_network_activity": "not_requested",
                "evidence_hash": "",
            }
        )
    if output is None:
        return _safe_result(
            {
                "status": "TENCENT_PROVIDER_NETWORK_REQUEST_INVALID",
                "execution_ok": False,
                "network_mode": True,
                "evidence_schema_version": (
                    TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION
                ),
                "reason": "ignored_cache_output_required",
                "network_requests_made": 0,
                "upstream_network_activity": "not_started",
                "evidence_hash": "",
            }
        )

    target = _resolve_cache_output(output)
    if target.exists():
        raise FileExistsError(f"tencent_validation_output_exists:{target}")
    active_transport = transport or TencentUrllibTransport()
    active_clock = clock or (lambda: datetime.now(CN_TZ))
    provider = TencentDirectHttpProviders(
        requested_codes,
        transport=active_transport,
        clock=active_clock,
        timeout_seconds=timeout_seconds,
    )
    capability_results: dict[str, dict[str, Any]] = {}
    records_by_capability: dict[str, list[dict[str, Any]]] = {}
    raw_responses: dict[str, dict[str, Any]] = {}
    capability_attempts: dict[str, dict[str, Any]] = {}
    methods = {
        "quote": provider.collect_quote_batch,
        "valuation": provider.collect_valuation_batch,
    }
    for capability, method in methods.items():
        started = perf_counter()
        attempt_started_at = datetime.now(CN_TZ)
        try:
            batch = method()
            records = list(batch.records)
            provenance = validate_source_provenance_batch(
                capability,
                records,
                require_hard_gate=False,
                environ={},
            )
            coverage = sorted(
                str(record.get("payload", {}).get("code") or "")
                for record in records
            )
            accepted = (
                provenance["status"] == "SOURCE_PROVENANCE_ACCEPTED"
                and coverage == sorted(provider.codes)
            )
            capability_results[capability] = {
                "status": (
                    "TENCENT_PROVIDER_CAPABILITY_VALIDATED"
                    if accepted
                    else "TENCENT_PROVIDER_PROVENANCE_REJECTED"
                ),
                "record_count": len(records),
                "covered_codes": coverage,
                "coverage_ratio": (
                    len(coverage) / len(provider.codes) if provider.codes else 0.0
                ),
                "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                "source_event_times": sorted(
                    {record["event_time"] for record in records}
                ),
                "source_field_counts": sorted(
                    {
                        int(record["payload"]["source_field_count"])
                        for record in records
                    }
                ),
                "request_hashes": sorted(
                    {record["request_hash"] for record in records}
                ),
                "raw_hashes": sorted(
                    {record["raw_hash"] for record in records}
                ),
                "provenance_status": provenance["status"],
            }
            records_by_capability[capability] = records
            raw_responses[capability] = {
                "capability": capability,
                "encoding": "base64",
                "content_base64": base64.b64encode(
                    batch.raw_response_bytes
                ).decode("ascii"),
                "byte_count": len(batch.raw_response_bytes),
                "request_url": batch.request_url,
                "request_hash": batch.request_hash,
                "raw_hash": batch.raw_hash,
                "observed_at": batch.observed_at,
                "available_at": batch.available_at,
                "http_status_code": batch.http_status_code,
                "response_url": batch.response_url,
            }
            capability_attempts[capability] = {
                "capability": capability,
                "outcome": "response_received",
                "error_code": "",
                "started_at": attempt_started_at.isoformat(
                    timespec="microseconds"
                ),
                "completed_at": datetime.now(CN_TZ).isoformat(
                    timespec="microseconds"
                ),
                "response_captured": True,
            }
        except TencentProviderContractError as exc:
            capability_results[capability] = {
                "status": "TENCENT_PROVIDER_CAPABILITY_FAILED",
                "error_code": exc.code,
                "record_count": 0,
                "covered_codes": [],
                "coverage_ratio": 0.0,
                "elapsed_ms": round((perf_counter() - started) * 1000, 3),
            }
            records_by_capability[capability] = []
            capability_attempts[capability] = {
                "capability": capability,
                "outcome": "failed",
                "error_code": exc.code,
                "started_at": attempt_started_at.isoformat(
                    timespec="microseconds"
                ),
                "completed_at": datetime.now(CN_TZ).isoformat(
                    timespec="microseconds"
                ),
                "response_captured": False,
            }

    validated = all(
        result.get("status") == "TENCENT_PROVIDER_CAPABILITY_VALIDATED"
        for result in capability_results.values()
    )
    request_count = getattr(active_transport, "request_count", None)
    if type(request_count) is int and request_count >= 0:
        measured_request_count: int | None = request_count
        upstream_network_activity = "measured"
    else:
        measured_request_count = None
        upstream_network_activity = "unknown"
    result = _safe_result(
        {
            "evidence_schema_version": (
                TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION
            ),
            "status": (
                "TENCENT_PROVIDER_NETWORK_VALIDATED"
                if validated
                else "TENCENT_PROVIDER_NETWORK_VALIDATION_FAILED"
            ),
            "execution_ok": True,
            "network_mode": True,
            "endpoint": TENCENT_QUOTE_ENDPOINT,
            "requested_codes": list(provider.codes),
            "provider_keys": {
                "quote": TENCENT_QUOTE_PROVIDER_KEY,
                "valuation": TENCENT_VALUATION_PROVIDER_KEY,
            },
            "capability_results": capability_results,
            "capability_attempts": capability_attempts,
            "records_by_capability": records_by_capability,
            "raw_responses": raw_responses,
            "evidence_integrity_verified": False,
            "provider_validation_passed": validated,
            "network_requests_made": measured_request_count,
            "upstream_network_activity": upstream_network_activity,
        }
    )
    result["evidence_hash"] = stable_hash(result)
    written = write_validation_json_atomic(target, result)
    result["output_path"] = str(written)
    return result


def write_validation_json_atomic(
    output: str | Path,
    payload: dict[str, Any],
) -> Path:
    target = _resolve_cache_output(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"tencent_validation_output_exists:{target}")
    temporary = target.with_name(
        f"{target.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _resolve_cache_output(output: str | Path) -> Path:
    target = Path(output)
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    try:
        target.relative_to(CACHE_ROOT)
    except ValueError as exc:
        raise TencentProviderContractError(
            "TENCENT_VALIDATION_OUTPUT_OUTSIDE_CACHE"
        ) from exc
    if target.suffix.lower() != ".json":
        raise TencentProviderContractError(
            "TENCENT_VALIDATION_OUTPUT_INVALID"
        )
    return target


def _safe_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.setdefault("evidence_integrity_verified", False)
    result.setdefault("provider_validation_passed", False)
    result["candidate_provider_validation_only"] = True
    result["data_ready"] = False
    result["hard_gate_authorized"] = False
    result["automatic_configuration_change"] = False
    result["candidates"] = []
    result["tickets"] = []
    result["orders"] = []
    return result


def _cli_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key)
        for key in (
            "status",
            "execution_ok",
            "evidence_schema_version",
            "evidence_integrity_verified",
            "provider_validation_passed",
            "network_mode",
            "network_requests_made",
            "upstream_network_activity",
            "requested_codes",
            "provider_keys",
            "capability_results",
            "evidence_hash",
            "output_path",
            "data_ready",
            "hard_gate_authorized",
            "candidates",
            "tickets",
            "orders",
        )
        if key in result
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Tencent quote/valuation provider candidates",
    )
    parser.add_argument(
        "--network",
        action="store_true",
        help="Explicitly enable read-only access to qt.gtimg.cn",
    )
    parser.add_argument(
        "--codes",
        default=",".join(DEFAULT_CODES),
        help="Comma-separated A-share codes",
    )
    parser.add_argument(
        "--output",
        help="Ignored cache JSON path; required with --network",
    )
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    codes = [item.strip() for item in args.codes.split(",") if item.strip()]
    try:
        result = run_tencent_provider_validation(
            network=bool(args.network),
            codes=codes,
            output=args.output,
            timeout_seconds=args.timeout_seconds,
        )
    except (TencentProviderContractError, FileExistsError) as exc:
        error_code = (
            exc.code
            if isinstance(exc, TencentProviderContractError)
            else "TENCENT_VALIDATION_OUTPUT_EXISTS"
        )
        result = _safe_result(
            {
                "status": "TENCENT_PROVIDER_NETWORK_VALIDATION_FAILED",
                "execution_ok": False,
                "evidence_schema_version": (
                    TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION
                ),
                "network_mode": bool(args.network),
                "error_code": error_code,
                "network_requests_made": None if args.network else 0,
                "upstream_network_activity": (
                    "unknown" if args.network else "not_started"
                ),
                "evidence_hash": "",
            }
        )
    print(
        json.dumps(
            _cli_summary(result),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result["status"] in {
        "TENCENT_PROVIDER_NETWORK_NOT_REQUESTED",
        "TENCENT_PROVIDER_NETWORK_VALIDATED",
    } else 3


if __name__ == "__main__":
    raise SystemExit(main())
