from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from overnight_quant.data.eastmoney_derived_industry_breadth import (
    ALLOWED_HOSTS, EXPECTED_INDUSTRIES, MAX_SCHEMA_AUDIT_REQUESTS,
    SOURCE_VERSION as INDUSTRY_VERSION, EastmoneyDerivedIndustryBreadthProvider,
    request_leaves,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_session_confirmation import (
    verify_market_session_confirmation_evidence,
)
from overnight_quant.data.market_source_go_nogo import _completed_calendar_errors
from overnight_quant.data.market_source_providers import (
    FIXED_CODES, PRODUCTION_TRANSPORT_VERSION, SOURCE_IDENTITIES,
    EastmoneyMarketSourceProviders, MarketDotNetHttpClientTransport,
    MarketSourceContractError,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.scripts.run_market_source_validation import write_json_atomic


SCHEMA_VERSION = "t23_combined_audit_evidence_v1"
ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "overnight_quant" / "data" / "cache"


def safe(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload, "audit_only": True, "qualification": "0/3",
        "sampling_authorized": False, "qualification_eligible": False,
        "automatic_qualification_change": False,
        "automatic_configuration_change": False, "data_ready": False,
        "hard_gate_authorized": False, "candidates": [], "tickets": [], "orders": [],
    }


def _time(value: str | datetime, trade_date: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if (parsed.tzinfo is None or parsed.utcoffset() != timedelta(hours=8)
            or parsed.date().isoformat() != trade_date):
        raise ValueError("AUDIT_TIME_DATE_OR_ZONE_INVALID")
    return parsed.astimezone(CN_TZ)


@dataclass(frozen=True)
class AuditParameters:
    trade_date: str
    start_at: datetime
    latest_start_at: datetime
    feature_cutoff: datetime
    collection_deadline: datetime
    request_deadline_ms: int = 2000

    @classmethod
    def for_date(cls, trade_date: str, **overrides) -> "AuditParameters":
        if date.fromisoformat(trade_date).isoformat() != trade_date:
            raise ValueError("AUDIT_TRADE_DATE_INVALID")
        defaults = {"start_at": "14:37:00", "latest_start_at": "14:39:30",
                    "feature_cutoff": "14:40:00", "collection_deadline": "14:40:10"}
        values = {
            key: _time(overrides.get(key) or f"{trade_date}T{clock}+08:00", trade_date)
            for key, clock in defaults.items()
        }
        return cls(trade_date, **values)

    def material(self) -> dict[str, Any]:
        return {"trade_date": self.trade_date,
                **{key: getattr(self, key).isoformat() for key in (
                    "start_at", "latest_start_at", "feature_cutoff", "collection_deadline")},
                "request_deadline_ms": self.request_deadline_ms}


def request_plan() -> dict[str, Any]:
    boards = sorted({row[0] for row in EXPECTED_INDUSTRIES.values()})
    minimum_industry = len(FIXED_CODES) + len(boards)
    return {
        "market_breadth_requests": 2, "mapping_codes": list(FIXED_CODES),
        "board_codes": boards, "derived_industry_minimum_requests": minimum_industry,
        "derived_industry_maximum_requests": MAX_SCHEMA_AUDIT_REQUESTS,
        "planned_request_count": 2 + minimum_industry,
        "maximum_request_count": 2 + MAX_SCHEMA_AUDIT_REQUESTS,
        "pagination": "one_request_per_page_only_if_declared_total_requires_it",
    }


def verifier_contract_hash() -> str:
    return stable_hash({"schema": SCHEMA_VERSION, "plan": request_plan(),
                        "request_deadline_ms": 2000, "timezone": "+08:00",
                        "native_event_rule": "native_event<=feature_cutoff",
                        "collection_rule": "feature_cutoff<collection_deadline",
                        "source_versions": [SOURCE_IDENTITIES["market_breadth"], INDUSTRY_VERSION]})


class _NoNetworkTransport:
    def request(self, *args, **kwargs):
        raise AssertionError("offline_parameter_check_must_not_request")


def precheck(parameters: AuditParameters) -> dict[str, Any]:
    for key in ("start_at", "latest_start_at", "feature_cutoff", "collection_deadline"):
        _time(getattr(parameters, key), parameters.trade_date)
    if type(parameters.request_deadline_ms) is not int or parameters.request_deadline_ms != 2000:
        raise ValueError("AUDIT_REQUEST_DEADLINE_INVALID")
    if not parameters.start_at <= parameters.latest_start_at < parameters.feature_cutoff:
        raise ValueError("AUDIT_START_WINDOW_INVALID")
    no_network = _NoNetworkTransport()
    clock = lambda: parameters.start_at
    # Exercise the actual constructors, including their strict deadline ordering.
    EastmoneyMarketSourceProviders(
        FIXED_CODES, trade_date=parameters.trade_date,
        feature_cutoff=parameters.feature_cutoff,
        collection_deadline=parameters.collection_deadline,
        transport=no_network, clock=clock, timeout_seconds=2.0,
    )
    EastmoneyDerivedIndustryBreadthProvider(
        FIXED_CODES, trade_date=parameters.trade_date, cutoff=parameters.feature_cutoff,
        decision_time=parameters.collection_deadline,
        transport=no_network, clock=clock, timeout_seconds=2.0,
    )
    plan = request_plan()
    required_ms = plan["maximum_request_count"] * parameters.request_deadline_ms
    if (parameters.collection_deadline - parameters.latest_start_at).total_seconds() * 1000 < required_ms:
        raise ValueError("AUDIT_BATCH_BUDGET_INSUFFICIENT")
    return safe({"status": "COMBINED_AUDIT_PARAMETERS_VALID", "execution_ok": True,
                 "parameters": parameters.material(), "request_plan": plan,
                 "request_plan_hash": stable_hash(plan), "network_requests_made": 0,
                 "provider_constructor_checks": ["market_breadth", "derived_industry"]})


def confirm_inputs(parameters, calendar_path, calendar_sha, session_path, session_sha):
    if not all((calendar_path, calendar_sha, session_path, session_sha)):
        raise ValueError("AUDIT_CONFIRMED_INPUTS_REQUIRED")
    files = {}
    for name, path, expected in (("calendar", calendar_path, calendar_sha),
                                 ("session", session_path, session_sha)):
        raw = Path(path).read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError(f"AUDIT_{name.upper()}_EXTERNAL_SHA_MISMATCH")
        files[name] = json.loads(raw.decode("utf-8", errors="strict"))
    errors = _completed_calendar_errors(
        files["calendar"], parameters.trade_date,
        expected_file_sha256=calendar_sha, actual_file_sha256=calendar_sha,
    )
    session = verify_market_session_confirmation_evidence(
        files["session"], expected_file_sha256=session_sha, actual_file_sha256=session_sha,
    )
    if (errors or session.get("evidence_integrity_verified") is not True
            or session.get("session_confirmed") is not True
            or files["session"].get("trade_date") != parameters.trade_date):
        raise ValueError("AUDIT_CALENDAR_OR_SESSION_REJECTED")
    return {"completed_calendar_file_sha256": calendar_sha,
            "session_confirmation_file_sha256": session_sha}


class _BudgetTransport:
    transport_version = PRODUCTION_TRANSPORT_VERSION

    def __init__(self, transport, parameters, clock, maximum_requests, budget_end):
        self.transport = transport
        self.parameters = parameters
        self.clock = clock
        self.maximum_requests = maximum_requests
        self.budget_end = budget_end
        self.attempts: list[dict[str, Any]] = []

    def request(self, method, url, *, params, headers, timeout_seconds):
        now = _time(self.clock(), self.parameters.trade_date)
        remaining = min((self.parameters.collection_deadline - now).total_seconds(),
                        self.budget_end - time.monotonic())
        if remaining <= 0:
            raise MarketSourceContractError("AUDIT_BATCH_DEADLINE_EXCEEDED")
        if len(self.attempts) >= self.maximum_requests:
            raise MarketSourceContractError("AUDIT_REQUEST_PLAN_EXCEEDED")
        timeout = min(2.0, timeout_seconds, remaining)
        attempt = {"url": url, "request_hash": stable_hash({
            "method": method, "url": url,
            "params": sorted((str(k), str(v)) for k, v in params.items())}),
            "started_at": now.isoformat(), "timeout_ms": timeout * 1000,
            "ok": False, "error_code": ""}
        self.attempts.append(attempt)
        try:
            response = self.transport.request(
                method, url, params=params, headers=headers, timeout_seconds=timeout,
            )
            raw = bytes(response.content)
            attempt["response"] = {
                "content_base64": base64.b64encode(raw).decode("ascii"),
                "raw_hash": hashlib.sha256(raw).hexdigest(), "byte_count": len(raw),
                "http_status_code": response.status_code, "response_url": response.url,
                "request_started_at": response.request_started_at,
                "request_completed_at": response.request_completed_at,
                "process_started_at": response.process_started_at,
                "process_completed_at": response.process_completed_at,
                "process_elapsed_ms": response.process_elapsed_ms,
                "worker_terminated": response.worker_terminated,
                "transport_version": response.transport_version,
            }
            elapsed = response.process_elapsed_ms
            if elapsed is None or elapsed < 0 or elapsed > timeout * 1000:
                raise MarketSourceContractError("AUDIT_REQUEST_DEADLINE_EXCEEDED")
            completed = _time(self.clock(), self.parameters.trade_date)
            if completed > self.parameters.collection_deadline:
                raise MarketSourceContractError("AUDIT_BATCH_DEADLINE_EXCEEDED")
            attempt["ok"] = True
            return response
        except Exception as exc:
            attempt["error_code"] = getattr(exc, "code", type(exc).__name__)
            raise
        finally:
            attempt["completed_at"] = self.clock().isoformat()


def _serialize_response(item):
    raw = bytes(item["raw_bytes"])
    return {**{key: value for key, value in item.items() if key != "raw_bytes"},
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64", "byte_count": len(raw)}


def _collect_module(name, parameters, transport, clock, budget_end):
    maximum = 2 if name == "market_breadth" else MAX_SCHEMA_AUDIT_REQUESTS
    guarded = _BudgetTransport(transport, parameters, clock, maximum, budget_end)
    provider = None
    records = []
    error = ""
    try:
        if name == "market_breadth":
            provider = EastmoneyMarketSourceProviders(
                FIXED_CODES, trade_date=parameters.trade_date,
                feature_cutoff=parameters.feature_cutoff,
                collection_deadline=parameters.collection_deadline,
                transport=guarded, clock=clock, timeout_seconds=2.0,
            )
            records = list(provider.collect_market_breadth_batch().records)
            payload = records[0]["payload"]
            native_events = [row["raw_event_time"] for row in payload["index_breadth"].values()]
            native_events.append(payload["raw_benchmark_event_time"])
            if any(_time(value, parameters.trade_date) > parameters.feature_cutoff for value in native_events):
                raise MarketSourceContractError("AUDIT_NATIVE_EVENT_AFTER_CUTOFF")
        else:
            provider = EastmoneyDerivedIndustryBreadthProvider(
                FIXED_CODES, trade_date=parameters.trade_date,
                cutoff=parameters.feature_cutoff, decision_time=parameters.collection_deadline,
                transport=guarded, clock=clock, timeout_seconds=2.0,
            )
            records = provider.collect_records()
        if _time(clock(), parameters.trade_date) > parameters.collection_deadline:
            raise MarketSourceContractError("AUDIT_BATCH_DEADLINE_EXCEEDED")
    except Exception as exc:
        error = getattr(exc, "code", type(exc).__name__)
        records = []
    tree = provider.request_tree if name == "derived_industry" and provider else None
    responses = ([_serialize_response(row) for row in provider.captured_responses]
                 if name == "market_breadth" and provider else request_leaves(tree or {}))
    return {"status": "AUDIT_MODULE_VALIDATED" if not error else "AUDIT_MODULE_FAILED",
            "error_code": error, "provider_contract_complete": not error,
            "records": records, "responses": responses, "request_tree": tree,
            "request_attempt_count": len(guarded.attempts), "attempts": guarded.attempts,
            "raw_response_count": sum("response" in row for row in guarded.attempts)}


def run_combined_audit(
    parameters: AuditParameters, *, network: bool, output: str | Path | None,
    expected_head: str | None = None, calendar_path=None, calendar_sha=None,
    session_path=None, session_sha=None, clock: Callable[[], datetime] | None = None,
    _test_transport_factory=None,
) -> dict[str, Any]:
    runtime_clock = clock or (lambda: datetime.now(CN_TZ))
    try:
        if type(network) is not bool:
            raise ValueError("AUDIT_NETWORK_FLAG_INVALID")
        checked = precheck(parameters)
        if not network:
            return checked
        now = _time(runtime_clock(), parameters.trade_date)
        if now < parameters.start_at:
            raise ValueError("AUDIT_WINDOW_NOT_OPEN")
        if now > parameters.latest_start_at or now >= parameters.feature_cutoff:
            raise ValueError("AUDIT_WINDOW_MISSED")
        if output is None:
            raise ValueError("AUDIT_OUTPUT_REQUIRED")
        target = Path(output).resolve()
        if _test_transport_factory is None:
            if not target.is_relative_to(CACHE.resolve()):
                raise ValueError("AUDIT_IGNORED_CACHE_REQUIRED")
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
            if not expected_head or head != expected_head or dirty:
                raise ValueError("AUDIT_FIXED_CLEAN_HEAD_REQUIRED")
            confirmed = confirm_inputs(parameters, calendar_path, calendar_sha, session_path, session_sha)
        else:
            head, confirmed = "TEST_ONLY", {}
        if target.exists():
            raise FileExistsError("AUDIT_OUTPUT_EXISTS")
        target.parent.mkdir(parents=True, exist_ok=True)
    except (ValueError, MarketSourceContractError, OSError, subprocess.SubprocessError) as exc:
        return safe({"status": getattr(exc, "code", str(exc)), "execution_ok": False,
                     "provider_contract_complete": False, "network_requests_made": 0})

    # Reserve the entire batch before constructing any network transport.
    lock = target.with_name(target.name + ".running")
    try:
        with lock.open("x", encoding="utf-8"):
            pass
    except FileExistsError:
        return safe({"status": "AUDIT_ALREADY_RUNNING", "execution_ok": False,
                     "provider_contract_complete": False, "network_requests_made": 0})
    try:
        if target.exists():
            return safe({"status": "AUDIT_OUTPUT_EXISTS", "execution_ok": False,
                         "provider_contract_complete": False, "network_requests_made": 0})
        now = _time(runtime_clock(), parameters.trade_date)
        if now < parameters.start_at or now > parameters.latest_start_at or now >= parameters.feature_cutoff:
            return safe({"status": "AUDIT_WINDOW_MISSED", "execution_ok": False,
                         "provider_contract_complete": False, "network_requests_made": 0})
        factory = _test_transport_factory or (
            lambda name: MarketDotNetHttpClientTransport(
                allowed_hosts=ALLOWED_HOSTS if name == "derived_industry" else None)
        )
        modules = {}
        budget_end = time.monotonic() + (parameters.collection_deadline - now).total_seconds()
        for name in ("market_breadth", "derived_industry"):
            try:
                if _time(runtime_clock(), parameters.trade_date) >= parameters.collection_deadline:
                    raise MarketSourceContractError("AUDIT_BATCH_DEADLINE_EXCEEDED")
                modules[name] = _collect_module(name, parameters, factory(name), runtime_clock, budget_end)
            except Exception as exc:
                modules[name] = {"status": "AUDIT_MODULE_FAILED", "error_code": getattr(exc, "code", type(exc).__name__),
                                 "provider_contract_complete": False, "records": [], "responses": [],
                                 "attempts": [], "request_attempt_count": 0, "raw_response_count": 0}
        complete = all(row["provider_contract_complete"] for row in modules.values())
        result = safe({"evidence_schema_version": SCHEMA_VERSION,
                       "verifier_contract_hash": verifier_contract_hash(),
                       "status": "COMBINED_AUDIT_VALIDATED" if complete else "AUDIT_INCOMPLETE",
                       "execution_ok": True, "provider_contract_complete": complete,
                       "producer_commit_sha": head,
                       "selection_scope": "test_only" if _test_transport_factory else "production_audit_only",
                       "started_at": now.isoformat(), "completed_at": runtime_clock().isoformat(),
                       "parameters": parameters.material(), "confirmed_inputs": confirmed,
                       "requested_codes": list(FIXED_CODES), "request_plan": request_plan(),
                       "source_versions": {"market_breadth": SOURCE_IDENTITIES["market_breadth"][2],
                                           "derived_industry": INDUSTRY_VERSION},
                       "module_results": modules,
                       "request_attempt_count": sum(row["request_attempt_count"] for row in modules.values()),
                       "network_requests_made": (
                           sum(row["request_attempt_count"] for row in modules.values())
                           if complete and _test_transport_factory is None else None),
                       "upstream_network_activity": "unknown" if _test_transport_factory or not complete else "measured",
                       "retry_count": 0, "fallback_count": 0, "source_stitch_count": 0})
        result["evidence_hash"] = stable_hash(result)
        try:
            write_json_atomic(target, result)
        except OSError as exc:
            return safe({"status": "AUDIT_EVIDENCE_WRITE_FAILED", "execution_ok": False,
                         "error_code": type(exc).__name__, "provider_contract_complete": False,
                         "network_requests_made": None, "upstream_network_activity": "unknown",
                         "request_attempt_count": result["request_attempt_count"]})
        return result
    finally:
        lock.unlink(missing_ok=True)


def verify_combined_audit_file(path, *, expected_file_sha256: str) -> dict[str, Any]:
    from overnight_quant.data.eastmoney_derived_industry_breadth import (
        ReplayClock as IndustryReplayClock, ReplayTransport as IndustryReplayTransport,
    )
    from overnight_quant.scripts.run_market_source_evidence_verify import ReplayClock, ReplayTransport

    errors = []
    module_checks = {}
    payload = {}
    try:
        raw = Path(path).read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if not expected_file_sha256 or actual != expected_file_sha256:
            raise ValueError("external_file_sha256_mismatch")
        payload = json.loads(raw.decode("utf-8", errors="strict"))
        if payload.get("evidence_hash") != stable_hash({k: v for k, v in payload.items() if k != "evidence_hash"}):
            raise ValueError("evidence_hash_mismatch")
        if payload.get("evidence_schema_version") != SCHEMA_VERSION or payload.get("verifier_contract_hash") != verifier_contract_hash():
            raise ValueError("schema_or_verifier_mismatch")
        if payload.get("audit_only") is not True or payload.get("execution_ok") is not True:
            raise ValueError("audit_mode_invalid")
        for key in ("data_ready", "hard_gate_authorized", "sampling_authorized", "qualification_eligible",
                    "automatic_configuration_change", "automatic_qualification_change"):
            if payload.get(key) is not False:
                raise ValueError("safety_flag_invalid:" + key)
        if any(payload.get(key) != [] for key in ("candidates", "tickets", "orders")) or payload.get("qualification") != "0/3":
            raise ValueError("safety_output_invalid")
        if payload.get("retry_count") != 0 or payload.get("fallback_count") != 0 or payload.get("source_stitch_count") != 0:
            raise ValueError("retry_fallback_or_stitch_invalid")
        if payload.get("requested_codes") != list(FIXED_CODES) or payload.get("request_plan") != request_plan():
            raise ValueError("request_plan_invalid")
        if payload.get("source_versions") != {"market_breadth": SOURCE_IDENTITIES["market_breadth"][2], "derived_industry": INDUSTRY_VERSION}:
            raise ValueError("source_versions_invalid")
        params = payload["parameters"]
        parameters = AuditParameters.for_date(params["trade_date"], **{
            key: params[key] for key in ("start_at", "latest_start_at", "feature_cutoff", "collection_deadline")})
        if params != parameters.material():
            raise ValueError("parameters_invalid")
        precheck(parameters)
        started = _time(payload["started_at"], parameters.trade_date)
        completed = _time(payload["completed_at"], parameters.trade_date)
        if not parameters.start_at <= started <= parameters.latest_start_at or completed < started:
            raise ValueError("batch_time_invalid")
        modules = payload["module_results"]
        if set(modules) != {"market_breadth", "derived_industry"}:
            raise ValueError("modules_invalid")
        attempt_count = 0
        for name, module in modules.items():
            attempts = module["attempts"]
            attempt_count += len(attempts)
            if module["request_attempt_count"] != len(attempts) or len(attempts) > (2 if name == "market_breadth" else MAX_SCHEMA_AUDIT_REQUESTS):
                raise ValueError("request_count_invalid:" + name)
            for attempt in attempts:
                response = attempt.get("response")
                if response:
                    raw_response = base64.b64decode(response["content_base64"], validate=True)
                    if hashlib.sha256(raw_response).hexdigest() != response["raw_hash"] or len(raw_response) != response["byte_count"]:
                        raise ValueError("raw_response_hash_invalid:" + name)
            successful = module["provider_contract_complete"] is True
            if not successful:
                if module["records"] != [] or module["status"] != "AUDIT_MODULE_FAILED" or not module["error_code"]:
                    raise ValueError("failed_module_invalid:" + name)
                module_checks[name] = {"provider_validation_passed": False, "failure_reason_verified": False}
                continue
            if module["status"] != "AUDIT_MODULE_VALIDATED" or module["error_code"]:
                raise ValueError("module_status_invalid:" + name)
            responses = module["responses"]
            if len(responses) != len(attempts) or any(row["ok"] is not True for row in attempts):
                raise ValueError("successful_request_count_invalid:" + name)
            for response in responses:
                observed = _time(response["observed_at"], parameters.trade_date)
                available = _time(response["available_at"], parameters.trade_date)
                req_start = _time(response["request_started_at"], parameters.trade_date)
                req_end = _time(response["request_completed_at"], parameters.trade_date)
                if not started <= observed <= req_start <= req_end <= available <= parameters.collection_deadline:
                    raise ValueError("request_time_invalid:" + name)
                if not 0 <= response["process_elapsed_ms"] <= 2000:
                    raise ValueError("request_deadline_invalid:" + name)
            if name == "market_breadth":
                transport = ReplayTransport(name, responses)
                provider = EastmoneyMarketSourceProviders(
                    FIXED_CODES, trade_date=parameters.trade_date,
                    feature_cutoff=parameters.feature_cutoff, collection_deadline=parameters.collection_deadline,
                    transport=transport, clock=ReplayClock(responses), timeout_seconds=2.0)
                records = list(provider.collect_market_breadth_batch().records)
                detail = records[0]["payload"]
                native = [row["raw_event_time"] for row in detail["index_breadth"].values()] + [detail["raw_benchmark_event_time"]]
                if transport.responses or any(_time(value, parameters.trade_date) > parameters.feature_cutoff for value in native):
                    raise ValueError("breadth_native_event_or_requests_invalid")
            else:
                leaves = request_leaves(module["request_tree"])
                if leaves != responses:
                    raise ValueError("industry_request_tree_invalid")
                transport = IndustryReplayTransport(leaves)
                provider = EastmoneyDerivedIndustryBreadthProvider(
                    FIXED_CODES, trade_date=parameters.trade_date, cutoff=parameters.feature_cutoff,
                    decision_time=parameters.collection_deadline,
                    transport=transport, clock=IndustryReplayClock(leaves), timeout_seconds=2.0)
                records = provider.collect_records()
                if transport.leaves:
                    raise ValueError("unused_industry_requests")
            if json.loads(json.dumps(records)) != module["records"]:
                raise ValueError("records_replay_mismatch:" + name)
            module_checks[name] = {"provider_validation_passed": True, "failure_reason_verified": True}
        if payload["request_attempt_count"] != attempt_count:
            raise ValueError("aggregate_request_count_invalid")
        complete = all(row["provider_validation_passed"] for row in module_checks.values())
        if payload["provider_contract_complete"] is not complete or payload["status"] != ("COMBINED_AUDIT_VALIDATED" if complete else "AUDIT_INCOMPLETE"):
            raise ValueError("aggregate_status_invalid")
        if complete and completed > parameters.collection_deadline:
            raise ValueError("batch_completed_after_deadline")
    except (ValueError, KeyError, TypeError, IndexError, OSError, MarketSourceContractError) as exc:
        errors.append(str(exc))
    result = safe({"status": "COMBINED_AUDIT_EVIDENCE_VERIFIED" if not errors else "COMBINED_AUDIT_EVIDENCE_INVALID",
                   "evidence_integrity_verified": not errors,
                   "provider_contract_complete": not errors and payload.get("provider_contract_complete") is True,
                   "evidence_hash": payload.get("evidence_hash"), "expected_file_sha256": expected_file_sha256,
                   "module_checks": module_checks, "errors": errors})
    result["replay_hash"] = stable_hash(result)
    return result
