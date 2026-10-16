"""One same-round candidate audit, using the existing bounded worker and composer."""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import math
import os
from pathlib import Path
import re
import time
from typing import Callable
import uuid

from overnight_quant.data import complete_industry_candidate as candidate
from overnight_quant.data import eastmoney_industry_strength_candidate as strength
from overnight_quant.data.eastmoney_derived_industry_breadth import (
    EXPECTED_INDUSTRIES, PAGE_SIZE, SOURCE_VERSION as WIDTH_VERSION, _board_code, _strict_int,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import MarketDotNetHttpClientTransport, MARKET_DOTNET_TRANSPORT_VERSION
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.t23_combined_audit import AuditParameters, confirm_inputs
from overnight_quant.data.t25_bounded_executor import (
    AuditRequest, AuditTimeConfiguration, BoundedAuditExecutor, LOCK, EXECUTOR_VERSION,
)

SCHEMA = "complete_industry_same_round_audit_v1"
PLAN_VERSION = "complete_industry_same_round_plan_v1"
TIME_VERSION = "t31_complete_industry_time_configuration_v1"
ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "overnight_quant/data/cache"
STAGES = ("mappings", "first_pages", "additional_pages", "strength")


def safe(**value):
    return candidate._safe(audit_only=True, audit_records=[], qualification_eligible=False,
                           source_qualification_verified=False, **value)


def budget_ms(requests: int, stage_boundaries: int = 3) -> int:
    # Serial, even across hosts: no throughput assumption from two workers.
    return max(3500, requests * 2500 - 500 + 1500 + stage_boundaries * 500)


def frozen_plan(trade_date: str, page_limits=None) -> dict:
    source = candidate.complete_industry_audit_plan(trade_date, page_counts=page_limits)
    config = AuditTimeConfiguration(trade_date).material()
    config["version"] = TIME_VERSION
    config["latest_start_at"] = f"{trade_date}T14:39:00+08:00"
    material = {
        "version": PLAN_VERSION, "trade_date": trade_date, "codes": list(candidate.FIXED_CODES),
        "boards": list(candidate.BOARDS), "time_configuration": config,
        "page_limits": source["page_counts"], "page_size": PAGE_SIZE,
        "requests": source["requests"], "maximum_requests": source["request_count"],
        "max_workers": 2, "host_client_interval_ms": 500, "request_deadline_ms": 2000,
        "conservative_budget_ms": budget_ms(source["request_count"]),
        "cleanup_and_freeze_ms": 1500, "stage_boundary_reserve_ms": 1500,
        "unused_pages": "skip_after_first_total; never_dispatch; no_unresolved_dependency",
        "candidate_contract_hash": candidate.contract_hash(), "executor_version": EXECUTOR_VERSION,
    }
    return {**material, "plan_hash": stable_hash(material)}


def verifier_hash() -> str:
    return stable_hash({"schema": SCHEMA, "plan": PLAN_VERSION, "time_configuration": TIME_VERSION,
        "candidate": candidate.contract_hash(),
        "latest_start": "14:39:00", "pagination": "first_total_required_pages_within_frozen_caps",
        "capture": "copy_native_bytes_and_client_times; parent_observation_boundaries",
        "scope": "strict_bool; worker_test_scope_matches_batch; no_qualification_authority",
        "trace": "recompute_client_order_counts_stages_dependencies_and_composition",
        "budget": "serial_2500_per_remaining_request_plus_cleanup_and_stage_reserves"})


def _jobs(plan, ids):
    selected = [j for j in plan["requests"] if j["request_id"] in ids]
    return [AuditRequest(j["request_id"], j["url"], j["params"],
                         tuple(d for d in j["dependencies"] if d in ids)) for j in selected]


class _IndustryExecutor(BoundedAuditExecutor):
    """Only adds industry response checks and remaining-plan budget admission."""

    def __init__(self, plan):
        self.plan = plan
        self.future_requests = 0
        self.stage_boundaries = 0
        self.totals, self.pages, self.members = {}, {}, {}

    def _required_dispatch_budget_ms(self, pending_count, in_flight_count):
        return budget_ms(pending_count + in_flight_count + self.future_requests, self.stage_boundaries)

    def _validate_response(self, value, job):
        try:
            self._check_response(value, job)
        except (ValueError, RuntimeError, KeyError, TypeError, OSError, OverflowError) as exc:
            reason = str(exc)
            raise ValueError(reason if reason.startswith("AUDIT_") else
                             "AUDIT_RESPONSE_CONTRACT_REJECTED:" + reason) from exc

    def _check_response(self, value, job):
        super()._validate_response(value, job)
        response = value["response"]
        data = candidate._json(base64.b64decode(response["content_base64"], validate=True))
        key = job.request_id
        if key.startswith("10-mapping-"):
            code = key.removeprefix("10-mapping-")
            rows = data.get("ssbk")
            if not isinstance(rows, list):
                raise ValueError("AUDIT_MAPPING_MISSING")
            matches = [(str(r.get("SECURITY_CODE", "")).zfill(6), _board_code(r.get("BOARD_CODE")),
                        str(r.get("BOARD_NAME", "")).strip()) for r in rows
                       if _strict_int(r.get("BOARD_RANK"), "AUDIT_MAPPING_RANK_INVALID") == 2]
            if matches != [(code, *EXPECTED_INDUSTRIES[code])]:
                raise ValueError("AUDIT_MAPPING_IDENTITY_INVALID")
            return
        body = data.get("data") or {}
        if key.startswith("20-page-"):
            _, _, board, page_text = key.split("-")
            page = int(page_text)
            total = _strict_int(body.get("total"), "AUDIT_BOARD_TOTAL_INVALID")
            needed = math.ceil(total / PAGE_SIZE)
            if total <= 0 or needed > self.plan["page_limits"][board]:
                raise ValueError("AUDIT_PAGE_LIMIT_EXCEEDED")
            if page != self.pages.get(board, 0) + 1 or (board in self.totals and self.totals[board] != total):
                raise ValueError("AUDIT_PAGE_SEQUENCE_OR_TOTAL_DRIFT")
            rows = body.get("diff")
            if not isinstance(rows, list) or len(rows) != min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE):
                raise ValueError("AUDIT_BOARD_PAGE_INCOMPLETE")
            seen = set(self.members.get(board, set()))
            for row in rows:
                code = row.get("f12")
                if type(code) is not str or len(code) != 6 or not code.isdigit() or code in seen:
                    raise ValueError("AUDIT_DUPLICATE_OR_INVALID_MEMBER")
                seen.add(code)
                self._native_time(row.get("f124"), response)
            if page == needed:
                expected = {c for c, (b, _) in EXPECTED_INDUSTRIES.items() if b == board}
                if len(seen) != total or not expected <= seen:
                    raise ValueError("AUDIT_MAPPED_STOCK_NOT_IN_BOARD")
            self.members[board], self.totals[board], self.pages[board] = seen, total, page
        else:
            board = key.removeprefix("30-strength-")
            if (data.get("rc") != 0 or type(data.get("rc")) is not int
                    or body.get("f57") != board or body.get("f58") != strength.BOARD_NAMES[board]
                    or type(body.get("f152")) is not int or not 0 <= body["f152"] <= 8
                    or type(body.get("f170")) not in (int, float) or not math.isfinite(body["f170"])):
                raise ValueError("AUDIT_STRENGTH_FIELDS_INVALID")
            self._native_time(body.get("f86"), response)

    def _native_time(self, stamp, response):
        if type(stamp) is not int or stamp <= 0:
            raise ValueError("AUDIT_NATIVE_TIME_INVALID")
        event = datetime.fromtimestamp(stamp, CN_TZ)
        if (event.date().isoformat() != self.plan["trade_date"]
                or event > datetime.fromisoformat(response["request_completed_at"])
                or event > datetime.fromisoformat(self.plan["time_configuration"]["feature_cutoff"])):
            raise ValueError("AUDIT_NATIVE_TIME_INVALID")


@dataclass(frozen=True)
class _TestContext:
    command: list[str]
    payloads: dict
    lock_path: Path
    gate_clock: Callable[[], datetime]
    budget_seconds: float | None = None


def _capture(row, context):
    if row["response"].get("test_only", False) is not context["test_only"]:
        raise ValueError("AUDIT_WORKER_TEST_SCOPE_MISMATCH")
    response = row["response"]["response"]
    is_strength = row["request_id"].startswith("30-strength-")
    capture = {**deepcopy(response), **context,
        "origin_source": "eastmoney", "adapter": "direct_http",
        "source_version": strength.SOURCE_VERSION if is_strength else WIDTH_VERSION,
        "transport_version": row["transport_version"], "http_status_code": response["status_code"],
        "response_url": response["url"], "method": "GET", "url": row["url"], "params": row["params"],
        "request_hash": row["request_hash"], "process_started_at": row["request_started_at"],
        "process_completed_at": row["request_completed_at"], "process_elapsed_ms": row["request_elapsed_ms"],
        "worker_terminated": row["worker_terminated"],
        "observed_at": row["request_completed_at"] if is_strength else row["request_started_at"],
        "available_at": row["request_completed_at"]}
    if is_strength:
        capture["contract_version"] = strength.CONTRACT_VERSION
    elif row["request_id"].startswith("10-mapping-"):
        capture.update(kind="mapping", identity=row["request_id"].removeprefix("10-mapping-"))
    else:
        capture.update(kind="board_page", identity=":".join(row["request_id"].split("-")[2:]))
    return capture


def _assemble(evidence):
    plan = evidence["plan"]
    times = {k: plan["time_configuration"][k] for k in candidate.TIME_FIELDS}
    context = {"batch_id": evidence["batch_id"], "trade_date": plan["trade_date"],
               "test_only": evidence["test_only"], "time_contract_hash": stable_hash(times)}
    tree = {"mappings": [], "boards": {b: [] for b in candidate.BOARDS}}
    captures = []
    for row in evidence["request_trace"]:
        capture = _capture(row, context)
        if row["request_id"].startswith("10-mapping-"):
            tree["mappings"].append(capture)
        elif row["request_id"].startswith("20-page-"):
            tree["boards"][capture["identity"].split(":")[0]].append(capture)
        else:
            captures.append({**context, "board_code": row["request_id"].removeprefix("30-strength-"), "capture": capture})
    return {"schema_version": candidate.INPUT_SCHEMA, **context, "codes": list(candidate.FIXED_CODES),
        "temporal_contract": times, "started_at": evidence["started_at"], "completed_at": evidence["completed_at"],
        "breadth": {**context, "source_version": WIDTH_VERSION, "request_tree": tree},
        "strength": {**context, "source_version": strength.SOURCE_VERSION, "captures": captures}}


def _finish(evidence):
    calls = []
    for row in evidence["request_trace"]:
        raw = (row.get("response") or row.get("captured_worker_response") or {}).get("response") or {}
        if (type(raw.get("client_call_started_monotonic_seconds")) in (int, float)
                and math.isfinite(raw["client_call_started_monotonic_seconds"])
                and candidate._sha(raw.get("raw_hash")) and type(raw.get("request_started_at")) is str):
            calls.append({"request_id": row["request_id"],
                          "client_call_monotonic_seconds": raw["client_call_started_monotonic_seconds"],
                          "request_started_at": raw["request_started_at"], "raw_hash": raw["raw_hash"]})
    evidence["actual_client_call_order"] = sorted(calls, key=lambda r: (r["client_call_monotonic_seconds"], r["request_id"]))
    evidence["dispatched_count"] = len(evidence["request_trace"])
    evidence["network_requests_made"] = (0 if evidence["test_only"] else None if evidence.get("network_activity_unknown") else
                                         len(calls) if len(calls) == evidence["dispatched_count"] else None)
    evidence["evidence_hash"] = stable_hash(evidence)
    return evidence


def validate_only(trade_date, page_limits=None):
    plan = frozen_plan(trade_date, page_limits)
    return safe(status="COMPLETE_INDUSTRY_AUDIT_PLAN_VALIDATED", execution_ok=True,
                plan=plan, network_requests_made=0, dispatched_count=0)


def run_audit(trade_date, *, page_limits=None, network=False, expected_head=None,
              expected_plan_hash=None, calendar_path=None, calendar_sha=None, session_path=None, session_sha=None):
    if type(network) is not bool:
        raise ValueError("AUDIT_NETWORK_CONTROL_INVALID")
    if not network:
        return validate_only(trade_date, page_limits)
    return _run(trade_date, page_limits=page_limits, expected_head=expected_head,
                expected_plan_hash=expected_plan_hash,
                calendar_path=calendar_path, calendar_sha=calendar_sha,
                session_path=session_path, session_sha=session_sha)


def _run(trade_date, *, page_limits=None, expected_head=None, expected_plan_hash=None, calendar_path=None, calendar_sha=None,
         session_path=None, session_sha=None, test: _TestContext | None = None):
    plan = frozen_plan(trade_date, page_limits)
    if expected_plan_hash != plan["plan_hash"]:
        raise ValueError("AUDIT_FROZEN_PLAN_HASH_MISMATCH")
    config = plan["time_configuration"]
    now = test.gate_clock() if test else datetime.now(CN_TZ)
    evidence = safe(status="COMPLETE_INDUSTRY_AUDIT_REJECTED", execution_ok=False,
        schema_version=SCHEMA, verifier_contract_hash=verifier_hash(), plan=plan,
        batch_id=stable_hash({"nonce": uuid.uuid4().hex, "plan_hash": plan["plan_hash"]}),
        started_at=datetime.now(CN_TZ).isoformat(), test_only=test is not None,
        request_trace=[], stages=[], errors=[], candidate_result=None, candidate_batch_complete=False,
        retry_count=0, fallback_count=0, residual_worker_count=0, confirmed_inputs={})
    def finish():
        evidence["completed_at"] = datetime.now(CN_TZ).isoformat()
        return _finish(evidence)
    if not datetime.fromisoformat(config["start_at"]) <= now <= datetime.fromisoformat(config["latest_start_at"]):
        evidence["errors"] = ["AUDIT_WINDOW_MISSED"]
        return finish()
    remaining = (datetime.fromisoformat(config["collection_deadline"]) - now).total_seconds()
    if test and test.budget_seconds is not None:
        remaining = min(remaining, test.budget_seconds)
    if remaining * 1000 < plan["conservative_budget_ms"]:
        evidence["errors"] = ["AUDIT_BATCH_BUDGET_INSUFFICIENT"]
        return finish()
    if not test:
        import subprocess
        try:
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            if type(expected_head) is not str or re.fullmatch(r"[0-9a-f]{40}", expected_head) is None or expected_head != head or subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
                raise ValueError("AUDIT_FIXED_HEAD_OR_WORKTREE_INVALID")
            params = AuditParameters.for_date(trade_date, latest_start_at=config["latest_start_at"],
                feature_cutoff=config["feature_cutoff"], collection_deadline=config["collection_deadline"])
            evidence["confirmed_inputs"] = confirm_inputs(params, calendar_path, calendar_sha, session_path, session_sha)
            evidence["producer_commit_sha"] = head
        except (ValueError, OSError, subprocess.CalledProcessError) as exc:
            evidence["errors"] = [str(exc)]
            return finish()
    else:
        evidence["confirmed_inputs"] = {"scope": "synthetic_test_only_not_session_evidence"}
    executor = _IndustryExecutor(plan)
    command = test.command if test else MarketDotNetHttpClientTransport().worker_command
    lock = test.lock_path if test else LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        evidence["errors"] = ["AUDIT_EXECUTOR_ALREADY_RUNNING"]
        return finish()
    batch_limit = time.perf_counter() + remaining
    try:
        all_ids = {r["request_id"] for r in plan["requests"]}
        groups = [sorted(i for i in all_ids if i.startswith("10-mapping-")),
                  sorted(i for i in all_ids if i.startswith("20-page-") and i.endswith("-1"))]
        for stage_index, stage in enumerate(STAGES):
            if stage_index == 2:
                groups.append(sorted(f"20-page-{board}-{p}" for board in candidate.BOARDS
                    for p in range(2, math.ceil(executor.totals[board] / PAGE_SIZE) + 1)))
            elif stage_index == 3:
                groups.append(sorted(i for i in all_ids if i.startswith("30-strength-")))
            ids = groups[stage_index]
            if not ids:
                evidence["stages"].append({"stage": stage, "status": "UNUSED_RESERVED_PAGES_SKIPPED", "request_ids": []})
                continue
            future = (sum(plan["page_limits"].values()) + 4 if stage_index == 0 else
                      sum(plan["page_limits"].values()) - 4 + 4 if stage_index == 1 else
                      4 if stage_index == 2 else 0)
            executor.future_requests, executor.stage_boundaries = future, 3 - stage_index
            deadline = min(datetime.fromisoformat(config["collection_deadline"]),
                datetime.now(CN_TZ) + timedelta(seconds=max(0, batch_limit - time.perf_counter())))
            result = executor._run(_jobs(plan, set(ids)), deadline=deadline, command=command,
                test_payloads=test.payloads if test else None, lock_path=lock.with_suffix(".stage"))
            evidence["stages"].append({"stage": stage, **result})
            evidence["request_trace"].extend({**row, "stage": stage} for row in result["results"])
            if not result["execution_ok"]:
                evidence["errors"] = [result["status"]]
                return finish()
        evidence["completed_at"] = datetime.now(CN_TZ).isoformat()
        evidence["captured_batch"] = _assemble(evidence)
        composed = candidate.CompleteIndustryCandidateProvider(candidate.CompleteIndustryCandidateEnvelope(
            candidate.PROVIDER_KEY, evidence["captured_batch"])).collect_batch()
        evidence["candidate_result"] = composed
        if not composed["execution_ok"]:
            evidence["errors"] = composed["errors"]
        else:
            evidence.update(status="COMPLETE_INDUSTRY_SAME_ROUND_CANDIDATE_VALIDATED", execution_ok=True,
                            candidate_batch_complete=True, audit_records=composed["audit_records"])
        return _finish(evidence)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, OverflowError) as exc:
        evidence.update(execution_ok=False, candidate_batch_complete=False, audit_records=[],
                        candidate_result=None, network_activity_unknown=True, residual_worker_count=None,
                        errors=["AUDIT_ORCHESTRATION_FAILED:" + str(exc)])
        return finish()
    finally:
        os.close(fd)
        lock.unlink()


def replay_evidence(evidence, *, expected_file_sha256, actual_file_sha256):
    result = safe(status="COMPLETE_INDUSTRY_AUDIT_EVIDENCE_INVALID", execution_ok=False,
                  evidence_integrity_verified=False, candidate_batch_complete=False, failure_reason_verified=False)
    try:
        if not candidate._sha(expected_file_sha256) or expected_file_sha256 != actual_file_sha256:
            raise ValueError("AUDIT_EXTERNAL_SHA_MISMATCH")
        material = {k: v for k, v in evidence.items() if k != "evidence_hash"}
        if (evidence.get("schema_version") != SCHEMA or evidence.get("evidence_hash") != stable_hash(material)
                or evidence.get("verifier_contract_hash") != verifier_hash() or type(evidence.get("test_only")) is not bool):
            raise ValueError("AUDIT_EVIDENCE_HASH_OR_VERSION_INVALID")
        for key in ("data_ready", "hard_gate_authorized", "sampling_authorized", "provider_contract_complete",
                    "qualification_eligible", "source_qualification_verified"):
            if evidence.get(key) is not False:
                raise ValueError("AUDIT_UNSAFE_OUTPUT")
        if any(evidence.get(k) != [] for k in ("records", "candidates", "tickets", "orders")):
            raise ValueError("AUDIT_UNSAFE_OUTPUT")
        plan = evidence["plan"]
        if plan != frozen_plan(plan["trade_date"], plan["page_limits"]):
            raise ValueError("AUDIT_FROZEN_PLAN_INVALID")
        refreshed = _finish(deepcopy(material))
        if any(evidence.get(key) != refreshed.get(key) for key in
               ("actual_client_call_order", "dispatched_count", "network_requests_made")):
            raise ValueError("AUDIT_CLIENT_CALL_TRACE_MISMATCH")
        if evidence["execution_ok"] is True:
            batch = _assemble(evidence)
            if batch != evidence["captured_batch"]:
                raise ValueError("AUDIT_CAPTURE_CHAIN_MISMATCH")
            checker = _IndustryExecutor(plan)
            jobs = {j["request_id"]: j for j in plan["requests"]}
            ids = []
            for stage in STAGES:
                rows = [r for r in evidence["request_trace"] if r["stage"] == stage]
                selected = _jobs(plan, {r["request_id"] for r in rows})
                by_id = {j.request_id: j for j in selected}
                for row in sorted(rows, key=lambda r: r["request_id"]):
                    key = row["request_id"]
                    if key not in jobs or key in ids or row["ok"] is not True:
                        raise ValueError("AUDIT_REQUEST_TRACE_INVALID")
                    ids.append(key)
                    job = by_id[key]
                    if row["plan_item_hash"] != stable_hash(job.material()):
                        raise ValueError("AUDIT_REQUEST_TRACE_INVALID")
                    checker._validate_response(row["response"], job)
                    expected_stage = ("mappings" if key.startswith("10-mapping-") else
                                      "strength" if key.startswith("30-strength-") else
                                      "first_pages" if key.endswith("-1") else "additional_pages")
                    if stage != expected_stage:
                        raise ValueError("AUDIT_STAGE_IDENTITY_MISMATCH")
            required = {i for i in jobs if not i.startswith("20-page-")}
            required |= {f"20-page-{b}-{p}" for b in candidate.BOARDS
                         for p in range(1, math.ceil(checker.totals[b] / PAGE_SIZE) + 1)}
            if set(ids) != required or len(ids) > 16 or len(ids) != len(evidence["request_trace"]):
                raise ValueError("AUDIT_ACTUAL_REQUEST_PLAN_INVALID")
            rows_by_id = {r["request_id"]: r for r in evidence["request_trace"]}
            mapping_end = max(r["request_completed_at"] for k, r in rows_by_id.items() if k.startswith("10-mapping-"))
            pages_end = max(r["request_completed_at"] for k, r in rows_by_id.items() if k.startswith("20-page-"))
            for key, row in rows_by_id.items():
                start = datetime.fromisoformat(row["request_started_at"])
                if key.startswith("20-page-"):
                    page = int(key.split("-")[-1])
                    dependency_end = mapping_end if page == 1 else rows_by_id[key.rsplit("-", 1)[0] + f"-{page-1}"]["request_completed_at"]
                    if start < datetime.fromisoformat(dependency_end):
                        raise ValueError("AUDIT_PAGE_DEPENDENCY_ORDER_INVALID")
                elif key.startswith("30-strength-") and start < datetime.fromisoformat(pages_end):
                    raise ValueError("AUDIT_STRENGTH_BEFORE_PAGES_COMPLETE")
            composed = candidate.CompleteIndustryCandidateProvider(candidate.CompleteIndustryCandidateEnvelope(candidate.PROVIDER_KEY, batch)).collect_batch()
            if not composed["execution_ok"] or composed != evidence["candidate_result"] or evidence["audit_records"] != composed["audit_records"]:
                raise ValueError("AUDIT_COMPOSITION_MISMATCH")
            result.update(candidate_batch_complete=True, candidate_hash=composed["candidate_hash"])
        elif evidence.get("audit_records") != [] or evidence.get("candidate_batch_complete") is not False:
            raise ValueError("AUDIT_FAILED_BATCH_UPGRADED")
        result.update(status="COMPLETE_INDUSTRY_AUDIT_EVIDENCE_VERIFIED", execution_ok=True,
            evidence_integrity_verified=True, input_file_sha256=actual_file_sha256,
            evidence_hash=evidence["evidence_hash"], request_trace_hash=stable_hash(evidence["request_trace"]))
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as exc:
        result["error_code"] = str(exc)
    return {**result, "replay_hash": stable_hash(result)}
