from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    DEFAULT_BATCH_DEADLINE_MS,
    EVIDENCE_SCHEMA_VERSION,
    FIXED_CODES,
    PRODUCTION_TRANSPORT_VERSION,
    PROVIDER_KEYS,
    compute_market_source_verifier_contract_hash,
    validate_market_source_records,
)
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash
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
    evidence_scope: str = "qualification",
    audit_market_feature_cutoff: str | None = None,
    worker_runner: Any = run_probe_worker_process,
    clock: Callable[[], datetime] | None = None,
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
    if evidence_scope not in {"qualification", "audit_only"}:
        raise ValueError("market_source_evidence_scope_invalid")
    target = _resolve_cache_output(output)
    if target.exists():
        raise FileExistsError(f"market_source_output_exists:{target}")
    normalized_codes = tuple(sorted(str(code).zfill(6) for code in codes))
    cutoff = feature_cutoff or f"{trade_date}T14:50:00+08:00"
    deadline = collection_deadline or f"{trade_date}T14:51:05+08:00"
    if audit_market_feature_cutoff and evidence_scope != "audit_only":
        raise ValueError("audit_market_feature_cutoff_requires_audit_only")
    if audit_market_feature_cutoff:
        parsed_audit_cutoff = parse_cn_datetime(audit_market_feature_cutoff)
        if parsed_audit_cutoff is None:
            raise ValueError("audit_market_feature_cutoff_invalid")
        current = (clock or (lambda: datetime.now(CN_TZ)))()
        if current.tzinfo is None:
            current = current.replace(tzinfo=CN_TZ)
        else:
            current = current.astimezone(CN_TZ)
        if current >= parsed_audit_cutoff:
            missed = _safe({
                "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
                "status": "AUDIT_WINDOW_MISSED",
                "execution_ok": False,
                "network_mode": True,
                "network_requests_made": 0,
                "evidence_scope": "audit_only",
                "trade_date": trade_date,
                "audit_cutoff": parsed_audit_cutoff.isoformat(),
                "checked_at": current.isoformat(timespec="microseconds"),
                "requested_codes": list(normalized_codes),
                "provider_validation_passed": False,
                "transport_qualified": False,
                "provider_contract_complete": False,
                "qualification_status": "UNQUALIFIED",
                "automatic_qualification_change": False,
                "consecutive_qualified_days": 0,
                "source_qualification_count": 0,
                "evidence_hash": "",
            })
            missed["evidence_hash"] = compute_evidence_hash(missed)
            write_json_atomic(target, missed)
            return missed
    capability_feature_cutoffs = {
        capability: (
            audit_market_feature_cutoff
            if evidence_scope == "audit_only"
            and audit_market_feature_cutoff
            and capability in {"market_breadth", "industry_snapshot"}
            else cutoff
        )
        for capability in CAPABILITIES
    }
    results: dict[str, dict[str, Any]] = {}
    records_by_capability: dict[str, list[dict[str, Any]]] = {}
    raw_responses: dict[str, list[dict[str, Any]]] = {}
    transport_attempts: dict[str, list[dict[str, Any]]] = {}
    for capability in CAPABILITIES:
        task = {
            "capability": capability,
            "codes": normalized_codes,
            "trade_date": trade_date,
            "feature_cutoff": capability_feature_cutoffs[capability],
            "collection_deadline": deadline,
            "request_timeout_seconds": 2.0,
        }
        started = datetime.now(CN_TZ)
        worker = worker_runner(
            task,
            int(batch_deadline_ms) * _batch_request_budget(capability),
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
            provider_complete = (
                payload.get("provider_contract_complete") is True
                and
                contract["valid"]
                and provenance["status"] == "SOURCE_PROVENANCE_ACCEPTED"
            )
            results[capability] = {
                "status": (
                    "MARKET_SOURCE_CAPABILITY_VALIDATED"
                    if provider_complete
                    else (
                        "EASTMONEY_PROVIDER_CONTRACT_INCOMPLETE"
                        if payload.get("transport_qualified") is True
                        else "MARKET_SOURCE_CAPABILITY_FAILED"
                    )
                ),
                "error_code": str(payload.get("error_code") or ""),
                "provider_error_code": str(payload.get("provider_error_code") or ""),
                "transport_qualified": payload.get("transport_qualified") is True,
                "provider_contract_complete": provider_complete,
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
            records_by_capability[capability] = records if provider_complete else []
            raw_responses[capability] = list(payload.get("responses") or [])
            transport_attempts[capability] = list(payload.get("transport_attempts") or [])
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
                "transport_qualified": False,
                "provider_contract_complete": False,
            }
            records_by_capability[capability] = []
            raw_responses[capability] = []
            transport_attempts[capability] = list(worker.get("transport_attempts") or [])
    provider_complete = all(
        result.get("status") == "MARKET_SOURCE_CAPABILITY_VALIDATED"
        for result in results.values()
    )
    transport_qualified = all(
        result.get("transport_qualified") is True for result in results.values()
    )
    evidence = _safe({
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": (
            "MARKET_SOURCE_NETWORK_VALIDATED"
            if provider_complete
            else (
                "EASTMONEY_PROVIDER_CONTRACT_INCOMPLETE"
                if transport_qualified
                else "MARKET_SOURCE_NETWORK_VALIDATION_FAILED"
            )
        ),
        "execution_ok": True,
        "network_mode": True,
        "evidence_scope": evidence_scope,
        "trade_date": trade_date,
        "feature_cutoff": cutoff,
        "capability_feature_cutoffs": capability_feature_cutoffs,
        "collection_deadline": deadline,
        "requested_codes": list(normalized_codes),
        "provider_keys": dict(PROVIDER_KEYS),
        "transport_version": PRODUCTION_TRANSPORT_VERSION,
        "producer_commit_sha": _git_head(),
        "provider_verifier_contract_hash": compute_market_source_verifier_contract_hash(),
        "capability_registry_hash": compute_source_capability_registry_hash(),
        "capability_results": results,
        "records_by_capability": records_by_capability,
        "raw_responses": raw_responses,
        "transport_attempts": transport_attempts,
        "network_requests_made": None,
        "upstream_network_activity": "isolated_workers",
        "transport_qualified": transport_qualified,
        "provider_contract_complete": provider_complete,
        "provider_validation_passed": provider_complete,
        "evidence_integrity_verified": False,
        "qualification_status": (
            "PM_REVIEW_REQUIRED"
            if provider_complete and evidence_scope == "qualification"
            else "UNQUALIFIED"
        ),
        "automatic_qualification_change": False,
        "consecutive_qualified_days": 0,
        "source_qualification_count": 0,
        "evidence_hash": "",
    })
    evidence["evidence_hash"] = compute_evidence_hash(evidence)
    write_json_atomic(target, evidence)
    return evidence


def compute_evidence_hash(evidence: Mapping[str, Any]) -> str:
    material = dict(evidence)
    material.pop("evidence_hash", None)
    return stable_hash(material)


def _batch_request_budget(capability: str) -> int:
    return {"market_breadth": 2, "industry_snapshot": 12, "fund_flow": 5}[
        capability
    ]


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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Validate S2 market source candidates")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--date", required=True)
    parser.add_argument("--output")
    parser.add_argument("--batch-deadline-ms", type=int, default=DEFAULT_BATCH_DEADLINE_MS)
    parser.add_argument("--feature-cutoff")
    parser.add_argument("--collection-deadline")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--audit-market-feature-cutoff")
    args = parser.parse_args(argv)
    try:
        result = run_market_source_validation(
            network=args.network,
            trade_date=args.date,
            output=args.output,
            batch_deadline_ms=args.batch_deadline_ms,
            feature_cutoff=args.feature_cutoff,
            collection_deadline=args.collection_deadline,
            evidence_scope="audit_only" if args.audit_only else "qualification",
            audit_market_feature_cutoff=args.audit_market_feature_cutoff,
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
