from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    DEFAULT_BATCH_DEADLINE_MS,
    EVIDENCE_SCHEMA_VERSION,
    FIXED_CODES,
    PROVIDER_KEYS,
    compute_market_source_verifier_contract_hash,
    validate_market_source_records,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.probe_worker_process import run_probe_worker_process
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
    validate_source_provenance_batch,
)


CACHE_ROOT = (ROOT / "overnight_quant" / "data" / "cache").resolve()
CAPABILITIES = ("market_breadth", "industry_snapshot", "fund_flow")


def run_market_source_validation(
    *,
    network: bool,
    trade_date: str,
    output: str | Path | None = None,
    codes: Iterable[str] = FIXED_CODES,
    feature_cutoff: str | None = None,
    collection_deadline: str | None = None,
    batch_deadline_ms: int = DEFAULT_BATCH_DEADLINE_MS,
    worker_runner: Any = run_probe_worker_process,
) -> dict[str, Any]:
    if not network:
        return _safe({
            "status": "MARKET_SOURCE_NETWORK_NOT_REQUESTED",
            "execution_ok": True,
            "network_mode": False,
            "network_requests_made": 0,
            "provider_validation_passed": False,
            "evidence_hash": "",
        })
    if output is None:
        return _safe({
            "status": "MARKET_SOURCE_REQUEST_INVALID",
            "execution_ok": False,
            "network_mode": True,
            "reason": "ignored_cache_output_required",
            "provider_validation_passed": False,
            "evidence_hash": "",
        })
    target = _resolve_cache_output(output)
    if target.exists():
        raise FileExistsError(f"market_source_output_exists:{target}")
    normalized_codes = tuple(sorted(str(code).zfill(6) for code in codes))
    cutoff = feature_cutoff or f"{trade_date}T14:50:00+08:00"
    deadline = collection_deadline or f"{trade_date}T14:51:05+08:00"
    results: dict[str, dict[str, Any]] = {}
    records_by_capability: dict[str, list[dict[str, Any]]] = {}
    raw_responses: dict[str, list[dict[str, Any]]] = {}
    for capability in CAPABILITIES:
        task = {
            "capability": capability,
            "codes": normalized_codes,
            "trade_date": trade_date,
            "feature_cutoff": cutoff,
            "collection_deadline": deadline,
            "request_timeout_seconds": 2.0,
        }
        started = datetime.now(CN_TZ)
        worker = worker_runner(
            task,
            int(batch_deadline_ms),
            worker_command=[
                sys.executable,
                "-m",
                "overnight_quant.data.market_source_worker",
            ],
        )
        completed = datetime.now(CN_TZ)
        if worker.get("ok") is True:
            payload = worker.get("payload") or {}
            records = list(payload.get("records") or [])
            contract = validate_market_source_records(
                capability, records, codes=normalized_codes
            )
            provenance = validate_source_provenance_batch(
                capability,
                records,
                require_hard_gate=False,
                environ={},
            )
            passed = (
                contract["valid"]
                and provenance["status"] == "SOURCE_PROVENANCE_ACCEPTED"
            )
            results[capability] = {
                "status": (
                    "MARKET_SOURCE_CAPABILITY_VALIDATED"
                    if passed else "MARKET_SOURCE_CAPABILITY_REJECTED"
                ),
                "record_count": len(records),
                "records_hash": contract["records_hash"],
                "provenance_status": provenance["status"],
                "errors": [
                    *contract["errors"],
                    *(
                        []
                        if provenance["status"] == "SOURCE_PROVENANCE_ACCEPTED"
                        else [f"provenance:{provenance['status']}"]
                    ),
                ],
                "started_at": started.isoformat(timespec="microseconds"),
                "completed_at": completed.isoformat(timespec="microseconds"),
                "elapsed_ms": worker.get("elapsed_ms"),
                "request_timed_out": False,
                "worker_terminated": False,
            }
            records_by_capability[capability] = records if passed else []
            raw_responses[capability] = list(payload.get("responses") or [])
        else:
            results[capability] = {
                "status": "MARKET_SOURCE_CAPABILITY_FAILED",
                "error_code": str(worker.get("error_code") or "MARKET_SOURCE_WORKER_FAILED"),
                "started_at": started.isoformat(timespec="microseconds"),
                "completed_at": completed.isoformat(timespec="microseconds"),
                "elapsed_ms": worker.get("elapsed_ms"),
                "request_timed_out": bool(worker.get("request_timed_out")),
                "worker_terminated": bool(worker.get("worker_terminated")),
                "record_count": 0,
            }
            records_by_capability[capability] = []
            raw_responses[capability] = []
    passed = all(
        result.get("status") == "MARKET_SOURCE_CAPABILITY_VALIDATED"
        for result in results.values()
    )
    evidence = _safe({
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": (
            "MARKET_SOURCE_NETWORK_VALIDATED"
            if passed else "MARKET_SOURCE_NETWORK_VALIDATION_FAILED"
        ),
        "execution_ok": True,
        "network_mode": True,
        "trade_date": trade_date,
        "feature_cutoff": cutoff,
        "collection_deadline": deadline,
        "requested_codes": list(normalized_codes),
        "provider_keys": dict(PROVIDER_KEYS),
        "producer_commit_sha": _git_head(),
        "provider_verifier_contract_hash": compute_market_source_verifier_contract_hash(),
        "capability_registry_hash": compute_source_capability_registry_hash(),
        "capability_results": results,
        "records_by_capability": records_by_capability,
        "raw_responses": raw_responses,
        "network_requests_made": None,
        "upstream_network_activity": "isolated_workers",
        "provider_validation_passed": passed,
        "evidence_integrity_verified": False,
        "qualification_status": "PM_REVIEW_REQUIRED" if passed else "UNQUALIFIED",
        "automatic_qualification_change": False,
        "consecutive_qualified_days": 0,
        "evidence_hash": "",
    })
    evidence["evidence_hash"] = compute_evidence_hash(evidence)
    write_json_atomic(target, evidence)
    return evidence


def compute_evidence_hash(evidence: Mapping[str, Any]) -> str:
    material = dict(evidence)
    material.pop("evidence_hash", None)
    return stable_hash(material)


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json_atomic(target: str | Path, payload: Mapping[str, Any]) -> Path:
    path = _resolve_cache_output(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
        raise ValueError("market_source_output_must_be_in_ignored_cache")
    return resolved


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate S2 market source candidates")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--date", required=True)
    parser.add_argument("--output")
    parser.add_argument("--batch-deadline-ms", type=int, default=DEFAULT_BATCH_DEADLINE_MS)
    args = parser.parse_args(argv)
    try:
        result = run_market_source_validation(
            network=args.network,
            trade_date=args.date,
            output=args.output,
            batch_deadline_ms=args.batch_deadline_ms,
        )
    except (FileExistsError, ValueError) as exc:
        print(json.dumps(_safe({
            "status": "MARKET_SOURCE_REQUEST_INVALID",
            "execution_ok": False,
            "error": str(exc),
        }), ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("provider_validation_passed") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
