from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.eastmoney_derived_industry_breadth import (
    ALLOWED_HOSTS,
    EVIDENCE_SCHEMA_VERSION,
    EXPECTED_INDUSTRIES,
    MAX_SCHEMA_AUDIT_REQUESTS,
    SOURCE_VERSION,
    EastmoneyDerivedIndustryBreadthProvider,
    ReplayTransport,
    compute_evidence_hash,
    compute_verifier_hash,
    request_leaves,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    FIXED_CODES,
    MARKET_DOTNET_TRANSPORT_VERSION,
    MarketDotNetHttpClientTransport,
    MarketSourceContractError,
)
from overnight_quant.data.point_in_time import parse_cn_datetime, stable_hash
from overnight_quant.scripts.run_market_source_validation import write_json_atomic


def run_audit(
    *,
    network: bool,
    trade_date: str,
    cutoff: str,
    decision_time: str,
    output: str | Path,
    schema_audit_only: bool,
    clock=None,
    transport=None,
    resume_request_tree=None,
) -> dict[str, Any]:
    now = (clock or (lambda: datetime.now(CN_TZ)))()
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    else:
        now = now.astimezone(CN_TZ)
    parsed_cutoff = parse_cn_datetime(cutoff)
    parsed_decision = parse_cn_datetime(decision_time)
    if parsed_cutoff is None or parsed_decision is None or parsed_decision <= parsed_cutoff:
        raise ValueError("derived_industry_time_contract_invalid")
    if not network:
        result = _safe({
            "status": "NETWORK_DISABLED",
            "execution_ok": False,
            "network_requests_made": 0,
        })
        return result
    if now >= parsed_cutoff:
        evidence = _base_evidence(
            trade_date=trade_date,
            cutoff=cutoff,
            decision_time=decision_time,
            schema_audit_only=schema_audit_only,
            status="AUDIT_WINDOW_MISSED",
            execution_ok=False,
            provider_contract_complete=False,
            request_tree={"mappings": [], "boards": {}},
            records=[],
            error_code="AUDIT_WINDOW_MISSED",
        )
        write_json_atomic(output, evidence)
        return evidence
    resumed_leaves = (
        request_leaves(resume_request_tree) if resume_request_tree else []
    )
    live_transport = transport or MarketDotNetHttpClientTransport(
        allowed_hosts=ALLOWED_HOSTS
    )
    selected_transport = (
        _ResumeTransport(resumed_leaves, live_transport)
        if resumed_leaves
        else live_transport
    )
    provider_clock = (
        _ResumeClock(resumed_leaves, clock or (lambda: datetime.now(CN_TZ)))
        if resumed_leaves
        else clock or (lambda: datetime.now(CN_TZ))
    )
    provider = EastmoneyDerivedIndustryBreadthProvider(
        FIXED_CODES,
        trade_date=trade_date,
        cutoff=cutoff,
        decision_time=decision_time,
        transport=selected_transport,
        clock=provider_clock,
        schema_audit_only=schema_audit_only,
    )
    records: list[dict[str, Any]] = []
    error_code = ""
    try:
        records = provider.collect_records()
    except MarketSourceContractError as exc:
        error_code = exc.code
    request_tree = provider.request_tree
    request_count = len(request_leaves(request_tree))
    complete = not error_code and len(records) == len(FIXED_CODES)
    evidence = _base_evidence(
        trade_date=trade_date,
        cutoff=cutoff,
        decision_time=decision_time,
        schema_audit_only=schema_audit_only,
        status=(
            "DERIVED_INDUSTRY_SCHEMA_VALIDATED"
            if complete and schema_audit_only
            else (
                "DERIVED_INDUSTRY_BREADTH_AUDIT_VALIDATED"
                if complete
                else "EASTMONEY_DERIVED_INDUSTRY_BREADTH_UNQUALIFIED"
            )
        ),
        execution_ok=True,
        provider_contract_complete=complete,
        request_tree=request_tree,
        records=records if complete else [],
        error_code=error_code,
    )
    evidence["network_requests_made"] = request_count
    evidence["resumed_request_count"] = len(resumed_leaves)
    evidence["new_network_requests_made"] = request_count - len(resumed_leaves)
    evidence["request_plan_hash"] = stable_hash({
        "mapping_codes": list(FIXED_CODES),
        "board_codes": sorted({item[0] for item in EXPECTED_INDUSTRIES.values()}),
        "max_requests": MAX_SCHEMA_AUDIT_REQUESTS,
    })
    evidence["evidence_hash"] = compute_evidence_hash(evidence)
    write_json_atomic(output, evidence)
    return evidence


def _base_evidence(
    *,
    trade_date: str,
    cutoff: str,
    decision_time: str,
    schema_audit_only: bool,
    status: str,
    execution_ok: bool,
    provider_contract_complete: bool,
    request_tree: dict[str, Any],
    records: list[dict[str, Any]],
    error_code: str,
) -> dict[str, Any]:
    result = _safe({
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": status,
        "execution_ok": execution_ok,
        "audit_only": True,
        "schema_audit_only": schema_audit_only,
        "trade_date": trade_date,
        "cutoff": cutoff,
        "decision_time": decision_time,
        "requested_codes": list(FIXED_CODES),
        "source_version": SOURCE_VERSION,
        "transport_version": MARKET_DOTNET_TRANSPORT_VERSION,
        "verifier_hash": compute_verifier_hash(),
        "producer_commit_sha": _git_head(),
        "request_budget": MAX_SCHEMA_AUDIT_REQUESTS,
        "request_tree": request_tree,
        "network_requests_made": len(request_leaves(request_tree)),
        "retry_count": 0,
        "fallback_count": 0,
        "source_stitch_count": 0,
        "records": records,
        "records_hash": stable_hash(records),
        "provider_contract_complete": provider_contract_complete,
        "qualification_status": "UNQUALIFIED",
        "qualification": "0/3",
        "error_code": error_code,
        "evidence_hash": "",
    })
    result["evidence_hash"] = compute_evidence_hash(result)
    return result


class _ResumeTransport:
    transport_version = MARKET_DOTNET_TRANSPORT_VERSION

    def __init__(self, leaves, live_transport) -> None:
        self._replay = ReplayTransport(leaves)
        self._remaining = len(leaves)
        self._live = live_transport
        self.request_count = 0

    def request(self, method, url, *, params, headers, timeout_seconds):
        self.request_count += 1
        if self._remaining:
            self._remaining -= 1
            return self._replay.request(
                method,
                url,
                params=params,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )
        return self._live.request(
            method,
            url,
            params=params,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )


class _ResumeClock:
    def __init__(self, leaves, live_clock) -> None:
        values = []
        for item in leaves:
            values.extend((item["observed_at"], item["available_at"]))
        self._values = deque(datetime.fromisoformat(value) for value in values)
        self._live_clock = live_clock

    def __call__(self):
        return self._values.popleft() if self._values else self._live_clock()


def _load_resume_tree(path: str | Path, expected_sha256: str) -> dict[str, Any]:
    source = Path(path)
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ValueError("resume_file_sha256_mismatch")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("evidence_hash") != compute_evidence_hash(payload):
        raise ValueError("resume_evidence_hash_mismatch")
    tree = payload.get("request_tree")
    leaves = request_leaves(tree or {})
    if (
        payload.get("source_version") != SOURCE_VERSION
        or payload.get("provider_contract_complete") is not False
        or payload.get("records") != []
        or len(leaves) != payload.get("network_requests_made")
        or any(item.get("kind") != "mapping" for item in leaves)
        or [item.get("identity") for item in leaves]
        != list(FIXED_CODES[: len(leaves)])
    ):
        raise ValueError("resume_evidence_contract_invalid")
    return dict(tree)


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


def _git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Run the audit-only derived Eastmoney industry breadth provider"
    )
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--date", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--decision-time", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--schema-audit-only", action="store_true")
    parser.add_argument("--resume-from")
    parser.add_argument("--resume-file-sha256")
    args = parser.parse_args(argv)
    try:
        resume_tree = None
        if args.resume_from or args.resume_file_sha256:
            if not args.resume_from or not args.resume_file_sha256:
                raise ValueError("resume_anchor_required")
            resume_tree = _load_resume_tree(
                args.resume_from,
                args.resume_file_sha256,
            )
        result = run_audit(
            network=args.network,
            trade_date=args.date,
            cutoff=args.cutoff,
            decision_time=args.decision_time,
            output=args.output,
            schema_audit_only=args.schema_audit_only,
            resume_request_tree=resume_tree,
        )
    except (FileExistsError, ValueError) as exc:
        print(json.dumps(_safe({
            "status": "DERIVED_INDUSTRY_AUDIT_REQUEST_INVALID",
            "execution_ok": False,
            "error": str(exc),
        }), ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("provider_contract_complete") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
