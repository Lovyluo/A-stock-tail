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
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
    validate_source_provenance_batch,
)
from overnight_quant.data.static_source_providers import (
    PROVIDER_KEYS,
    STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION,
    StaticSourceContractError,
    StaticSourceProviders,
    StaticUrllibTransport,
    compute_static_source_verifier_contract_hash,
    validate_static_provider_records,
)


DEFAULT_CODES = ("000001", "000333", "600000", "600519", "601318")
CACHE_ROOT = (ROOT / "overnight_quant" / "data" / "cache").resolve()


def run_static_source_validation(
    *,
    network: bool,
    trade_date: str,
    codes: Iterable[str] = DEFAULT_CODES,
    output: str | Path | None = None,
    timeout_seconds: float = 10.0,
    transport: Any | None = None,
    clock: Any | None = None,
) -> dict[str, Any]:
    if not network:
        return _safe({
            "status": "STATIC_SOURCE_NETWORK_NOT_REQUESTED",
            "execution_ok": True,
            "network_mode": False,
            "network_requests_made": 0,
            "evidence_hash": "",
        })
    if output is None:
        return _safe({
            "status": "STATIC_SOURCE_REQUEST_INVALID",
            "execution_ok": False,
            "network_mode": True,
            "reason": "ignored_cache_output_required",
            "network_requests_made": 0,
            "evidence_hash": "",
        })
    target = _resolve_cache_output(output)
    if target.exists():
        raise FileExistsError(f"static_source_output_exists:{target}")

    active_transport = transport or StaticUrllibTransport()
    active_clock = clock or (lambda: datetime.now(CN_TZ))
    cutoff = f"{trade_date}T14:50:00+08:00"
    provider = StaticSourceProviders(
        codes,
        target_trade_date=trade_date,
        feature_cutoff=cutoff,
        transport=active_transport,
        clock=active_clock,
        timeout_seconds=timeout_seconds,
    )
    results: dict[str, dict[str, Any]] = {}
    records_by_capability: dict[str, list[dict[str, Any]]] = {}
    raw_responses: dict[str, list[dict[str, Any]]] = {}
    calendar_dates: list[str] = []
    methods = (
        ("trading_calendar", lambda: provider.collect_trading_calendar_batch()),
        ("daily_bar_qfq", lambda: provider.collect_qfq_daily_batch(calendar_dates)),
        ("stock_news", provider.collect_stock_news_batch),
        ("global_news", provider.collect_global_news_batch),
        ("announcement", provider.collect_announcement_batch),
    )
    for capability, method in methods:
        started = datetime.now(CN_TZ)
        try:
            batch = method()
            records = list(batch.records)
            if capability == "trading_calendar" and records:
                calendar_dates[:] = list(records[0]["payload"]["trade_dates"])
            contract = validate_static_provider_records(
                capability,
                records,
                target_trade_date=trade_date,
                codes=codes,
                calendar_trade_dates=calendar_dates,
            )
            provenance = validate_source_provenance_batch(
                capability,
                records,
                require_hard_gate=False,
                environ={},
            )
            provenance_ok = (
                provenance["status"] == "SOURCE_PROVENANCE_ACCEPTED"
                or (not records and batch.status == "AVAILABLE_EMPTY")
            )
            accepted = contract["valid"] and provenance_ok
            results[capability] = {
                "status": (
                    "STATIC_SOURCE_CAPABILITY_VALIDATED"
                    if accepted
                    else "STATIC_SOURCE_CAPABILITY_REJECTED"
                ),
                "batch_status": batch.status,
                "record_count": len(records),
                "records_hash": contract["records_hash"],
                "contract_status": contract["status"],
                "provenance_status": provenance["status"],
                "started_at": started.isoformat(timespec="microseconds"),
                "completed_at": datetime.now(CN_TZ).isoformat(timespec="microseconds"),
                "errors": contract["errors"],
            }
            records_by_capability[capability] = records
            raw_responses[capability] = [
                _serialize_response(item) for item in batch.responses
            ]
        except (StaticSourceContractError, ValueError, KeyError, json.JSONDecodeError) as exc:
            captured = getattr(exc, "response_evidence", None)
            results[capability] = {
                "status": "STATIC_SOURCE_CAPABILITY_FAILED",
                "error_code": getattr(exc, "code", type(exc).__name__),
                "record_count": 0,
                "response_captured": captured is not None,
                "started_at": started.isoformat(timespec="microseconds"),
                "completed_at": datetime.now(CN_TZ).isoformat(timespec="microseconds"),
            }
            records_by_capability[capability] = []
            raw_responses[capability] = (
                [_serialize_response(captured)] if captured is not None else []
            )

    passed = all(
        item["status"] == "STATIC_SOURCE_CAPABILITY_VALIDATED"
        for item in results.values()
    )
    request_count = getattr(active_transport, "request_count", None)
    evidence = _safe({
        "evidence_schema_version": STATIC_SOURCE_EVIDENCE_SCHEMA_VERSION,
        "status": (
            "STATIC_SOURCE_NETWORK_VALIDATED"
            if passed
            else "STATIC_SOURCE_NETWORK_VALIDATION_FAILED"
        ),
        "execution_ok": True,
        "network_mode": True,
        "trade_date": trade_date,
        "feature_cutoff": cutoff,
        "requested_codes": sorted(str(code) for code in codes),
        "provider_keys": dict(PROVIDER_KEYS),
        "producer_commit_sha": _git_head(),
        "provider_verifier_contract_hash": compute_static_source_verifier_contract_hash(),
        "capability_registry_hash": compute_source_capability_registry_hash(),
        "capability_results": results,
        "records_by_capability": records_by_capability,
        "raw_responses": raw_responses,
        "network_requests_made": request_count if type(request_count) is int else None,
        "upstream_network_activity": "measured" if type(request_count) is int else "unknown",
        "provider_validation_passed": passed,
        "evidence_integrity_verified": False,
        "evidence_hash": "",
    })
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
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _serialize_response(item: dict[str, Any]) -> dict[str, Any]:
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
        raise ValueError("static_source_output_must_be_in_ignored_cache")
    return resolved


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate S1 static providers")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--date", required=True)
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        result = run_static_source_validation(
            network=args.network,
            trade_date=args.date,
            codes=tuple(code.strip() for code in args.codes.split(",") if code.strip()),
            output=args.output,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("provider_validation_passed") is True else 2
    except Exception as exc:
        print(json.dumps(_safe({
            "status": "STATIC_SOURCE_VALIDATION_FAILED",
            "execution_ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }), ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
