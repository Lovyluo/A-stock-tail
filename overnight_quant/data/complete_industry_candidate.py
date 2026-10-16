"""Captured-input composition only; no transport, activation or formal records."""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import gzip
import hashlib
import json
import math
import re
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlparse

from overnight_quant.data import eastmoney_industry_strength_candidate as strength
from overnight_quant.data.eastmoney_derived_industry_breadth import (
    CORE_CONCEPTION_URL, EXPECTED_INDUSTRIES, MAX_SCHEMA_AUDIT_REQUESTS, PAGE_SIZE,
    SOURCE_VERSION as BREADTH_VERSION, EastmoneyDerivedIndustryBreadthProvider,
    ReplayClock, ReplayTransport,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_CLIST_URL, FIXED_CODES, MARKET_DOTNET_TRANSPORT_VERSION, MAX_RESPONSE_BYTES,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import (
    ASSOCIATION_SCHEMA, CLASSIFICATION, CLASSIFICATION_VERSION, RECORD_SCHEMA,
    TIME_FIELDS, security_identity,
)
from overnight_quant.data.t25_bounded_executor import (
    AuditRequest, AuditTimeConfiguration, schedule_budget,
)


INPUT_SCHEMA = "complete_industry_captured_batch_v1"
CONTRACT_VERSION = "complete_industry_candidate_contract_v1"
SOURCE_VERSION = "eastmoney_mapping+constituent_breadth+fltt1_strength_candidate_v1"
PROVIDER_KEY = "complete_industry_candidate.CompleteIndustryCandidateProvider.collect_batch"
BOARDS = tuple(sorted(strength.BOARD_NAMES))
TOTAL_PAGE_LIMIT = MAX_SCHEMA_AUDIT_REQUESTS - len(FIXED_CODES)
PER_BOARD_PAGE_LIMIT = TOTAL_PAGE_LIMIT - len(BOARDS) + 1


def _safe(**value: Any) -> dict:
    return {
        **value, "implementation_status": "candidate_not_activated",
        "qualification_status": "unqualified", "qualification": "0/3",
        "data_ready": False, "hard_gate_authorized": False,
        "provider_activated": False, "provider_contract_complete": False,
        "strategy_integration": False, "automatic_configuration_change": False,
        "automatic_qualification_change": False, "sampling_authorized": False,
        "records": [], "candidates": [], "tickets": [], "orders": [],
        "network_requests_made": 0,
    }


def contract_hash() -> str:
    return stable_hash({
        "version": CONTRACT_VERSION, "input_schema": INPUT_SCHEMA,
        "source_version": SOURCE_VERSION, "provider_key": PROVIDER_KEY,
        "breadth_version": BREADTH_VERSION, "strength_contract": strength.candidate_contract_hash(),
        "codes": FIXED_CODES, "boards": EXPECTED_INDUSTRIES,
        "page_size": PAGE_SIZE, "total_page_limit": TOTAL_PAGE_LIMIT,
        "join": "canonical_security_then_exact_BK_not_name",
        "event": "maximum_all_validated_native_components_no_truncation",
        "batch": "same_id_date_scope_time_contract_all_leaves",
        "duration": "wall_and_monotonic_process_ms<=2000_all_child_roles",
        "admission": "audit_records_only_no_production_binding",
    })


@dataclass(frozen=True)
class CompleteIndustryCandidateEnvelope:
    provider_key: str
    batch: Mapping[str, Any]


def _require(valid: bool, reason: str) -> None:
    if not valid:
        raise ValueError(reason)


def _time(value: Any) -> datetime:
    _require(type(value) is str, "candidate_time_invalid")
    parsed = datetime.fromisoformat(value)
    _require(parsed.utcoffset() == timedelta(hours=8), "candidate_timezone_invalid")
    return parsed


def _sha(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _context(row: Mapping, batch: Mapping) -> None:
    _require(isinstance(row, Mapping), "candidate_component_invalid")
    _require(type(row.get("test_only")) is bool, "candidate_scope_invalid")
    _require(all(row.get(key) == batch[key] for key in ("batch_id", "trade_date", "test_only")),
             "candidate_batch_or_scope_mismatch")
    _require(row.get("time_contract_hash") == stable_hash(batch["temporal_contract"]),
             "candidate_time_contract_mismatch")


def _json(raw: bytes) -> dict:
    decoded = gzip.decompress(raw) if raw.startswith(b"\x1f\x8b") else raw
    _require(0 < len(decoded) <= MAX_RESPONSE_BYTES, "candidate_decoded_size_invalid")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "candidate_duplicate_json_field")
            result[key] = value
        return result
    value = json.loads(decoded.decode("utf-8", errors="strict"), object_pairs_hook=unique)
    _require(isinstance(value, dict), "candidate_response_invalid")
    return value


def _validate_leaf(leaf: Mapping, batch: Mapping, *, version: str,
                   url: str, params: Mapping, native_events: list[datetime],
                   clocks: Mapping[str, datetime], strength_leaf: bool = False) -> dict:
    _context(leaf, batch)
    _require(leaf.get("origin_source") == "eastmoney" and leaf.get("adapter") == "direct_http"
             and leaf.get("source_version") == version
             and leaf.get("transport_version") == MARKET_DOTNET_TRANSPORT_VERSION,
             "candidate_source_identity_mismatch")
    expected_hash = stable_hash({"method": "GET", "url": url,
                                 "params": sorted((str(k), str(v)) for k, v in params.items())})
    _require(leaf.get("request_hash") == expected_hash, "candidate_request_hash_mismatch")
    response = urlparse(str(leaf.get("response_url", "")))
    approved = urlparse(url)
    _require(response.scheme == "https" and response.netloc == approved.netloc
             and response.path == approved.path and not response.fragment
             and sorted(parse_qsl(response.query, keep_blank_values=True))
             == sorted((str(k), str(v)) for k, v in params.items()), "candidate_response_url_invalid")
    _require(type(leaf.get("http_status_code")) is int and leaf["http_status_code"] == 200,
             "candidate_http_failed")
    _require(leaf.get("redirect_chain") == [] and _number(leaf.get("client_call_started_monotonic_seconds"))
             and leaf["client_call_started_monotonic_seconds"] >= 0
             and leaf.get("http_start_coordination_version") == "http_client_call_spacing_v1"
             and leaf.get("request_started_at_semantics") == "client_SendAsync_call_boundary_not_packet_transmission"
             and leaf.get("request_completed_at_semantics") == "response_body_fully_read",
             "candidate_client_call_provenance_invalid")
    raw = base64.b64decode(leaf["content_base64"], validate=True)
    _require(0 < len(raw) <= MAX_RESPONSE_BYTES and _sha(leaf.get("raw_hash"))
             and hashlib.sha256(raw).hexdigest() == leaf["raw_hash"], "candidate_raw_hash_mismatch")
    if not strength_leaf:
        _require(leaf.get("byte_count") == len(raw), "candidate_byte_count_mismatch")
    times = {key: _time(leaf[key]) for key in (
        "process_started_at", "request_started_at", "request_completed_at",
        "process_completed_at", "observed_at", "available_at",
    )}
    _require(all(t.date().isoformat() == batch["trade_date"] for t in times.values()),
             "candidate_child_date_mismatch")
    ps, start, end, pe = (times[key] for key in (
        "process_started_at", "request_started_at", "request_completed_at", "process_completed_at"))
    observed, available = times["observed_at"], times["available_at"]
    _require(_time(batch["started_at"]) <= ps <= start <= end <= pe <= available
             <= _time(batch["completed_at"]) <= clocks["collection_deadline"],
             "candidate_child_time_order_invalid")
    _require((pe <= observed <= available) if strength_leaf else (_time(batch["started_at"]) <= observed <= ps),
             "candidate_child_observation_invalid")
    _require((pe - ps).total_seconds() * 1000 <= 2000, "candidate_child_deadline_exceeded")
    _require(_number(leaf.get("process_elapsed_ms")) and 0 <= leaf["process_elapsed_ms"] <= 2000
             and leaf.get("worker_terminated") is False, "candidate_child_deadline_exceeded")
    for event in native_events:
        _require(event.date().isoformat() == batch["trade_date"] and event <= end
                 and event <= clocks["feature_cutoff"], "candidate_native_time_invalid")
    return {"source_version": version, "request_hash": expected_hash,
            "raw_hash": leaf["raw_hash"], "original_capture": deepcopy(dict(leaf)),
            "native_event_times": [t.isoformat() for t in native_events],
            "time_semantics": "native_feature_event" if native_events else "static_classification_audit_only"}


class CompleteIndustryCandidateProvider:
    """Only consumes explicit captured inputs. No callable or transport constructor."""

    def __init__(self, envelope: CompleteIndustryCandidateEnvelope):
        self.envelope = envelope

    def collect_batch(self) -> dict:
        empty = _safe(status="COMPLETE_INDUSTRY_CANDIDATE_REJECTED", execution_ok=False,
                      candidate_batch_complete=False, audit_records=[], errors=[],
                      contract_version=CONTRACT_VERSION, contract_hash=contract_hash())
        try:
            _require(type(self.envelope) is CompleteIndustryCandidateEnvelope
                     and self.envelope.provider_key == PROVIDER_KEY
                     and isinstance(self.envelope.batch, Mapping), "candidate_envelope_invalid")
            batch = deepcopy(dict(self.envelope.batch))
            _require(batch.get("schema_version") == INPUT_SCHEMA and _sha(batch.get("batch_id")),
                     "candidate_input_schema_invalid")
            _require(type(batch.get("test_only")) is bool, "candidate_scope_invalid")
            codes = tuple(sorted({security_identity(code).split(":")[1] for code in batch["codes"]}))
            _require(codes == FIXED_CODES, "candidate_fixed_codes_required")
            declared = batch["temporal_contract"]
            _require(isinstance(declared, dict) and set(declared) == set(TIME_FIELDS),
                     "candidate_temporal_contract_invalid")
            clocks = {key: _time(declared[key]) for key in TIME_FIELDS}
            ordered = list(clocks.values())
            _require(ordered[0] < ordered[1] <= ordered[2] <= ordered[3]
                     and all(t.date().isoformat() == batch["trade_date"] for t in ordered),
                     "candidate_temporal_contract_invalid")
            started, completed = _time(batch["started_at"]), _time(batch["completed_at"])
            _require(started.date().isoformat() == batch["trade_date"]
                     and started <= completed <= clocks["collection_deadline"], "candidate_batch_time_invalid")
            breadth, native = batch["breadth"], batch["strength"]
            for component, version in ((breadth, BREADTH_VERSION), (native, strength.SOURCE_VERSION)):
                _context(component, batch)
                _require(component.get("source_version") == version, "candidate_component_version_invalid")
            tree = deepcopy(breadth["request_tree"])
            _require(set(tree) == {"mappings", "boards"} and set(tree["boards"]) == set(BOARDS),
                     "candidate_board_coverage_invalid")
            mappings = sorted(tree["mappings"], key=lambda row: security_identity(row["identity"]).split(":")[1])
            _require([row["identity"] for row in mappings] == list(FIXED_CODES),
                     "candidate_mapping_coverage_invalid")
            mapping_trace, page_trace = {}, {}
            leaves = list(mappings)
            for leaf in mappings:
                code = leaf["identity"]
                params = {"code": ("SH" if code.startswith("6") else "SZ") + code}
                _require(leaf.get("url") == CORE_CONCEPTION_URL and dict(leaf["params"]) == params
                         and leaf.get("method") == "GET", "candidate_mapping_request_invalid")
                mapping_trace[code] = _validate_leaf(leaf, batch, version=BREADTH_VERSION,
                    url=CORE_CONCEPTION_URL, params=params, native_events=[], clocks=clocks)
            for board in BOARDS:
                pages = sorted(tree["boards"][board], key=lambda row: int(dict(row["params"])["pn"]))
                _require(1 <= len(pages) <= PER_BOARD_PAGE_LIMIT, "candidate_page_limit_invalid")
                page_trace[board] = []
                for index, leaf in enumerate(pages, 1):
                    params = {"pn": str(index), "pz": str(PAGE_SIZE), "po": "1", "np": "1",
                              "fltt": "2", "invt": "2", "fid": "f3", "fs": f"b:{board}",
                              "fields": "f2,f3,f12,f14,f124"}
                    _require(leaf.get("identity") == f"{board}:{index}" and leaf.get("url") == EASTMONEY_CLIST_URL
                             and dict(leaf["params"]) == params and leaf.get("method") == "GET",
                             "candidate_page_sequence_invalid")
                    data = _json(base64.b64decode(leaf["content_base64"], validate=True))["data"]
                    events = []
                    for row in data["diff"]:
                        _require(type(row.get("f124")) is int, "candidate_native_time_missing")
                        _require(type(row.get("f12")) is str and re.fullmatch(r"\d{6}", row["f12"]) is not None,
                                 "candidate_constituent_identity_invalid")
                        events.append(datetime.fromtimestamp(row["f124"], CN_TZ))
                    _require(bool(events), "candidate_page_empty")
                    page_trace[board].append(_validate_leaf(leaf, batch, version=BREADTH_VERSION,
                        url=EASTMONEY_CLIST_URL, params=params, native_events=events, clocks=clocks))
                tree["boards"][board] = pages
                leaves.extend(pages)
            _require(len(leaves) <= MAX_SCHEMA_AUDIT_REQUESTS, "candidate_total_page_limit_exceeded")
            tree["mappings"] = mappings
            transport = ReplayTransport(leaves)
            provider = EastmoneyDerivedIndustryBreadthProvider(codes, trade_date=batch["trade_date"],
                cutoff=declared["feature_cutoff"], decision_time=declared["collection_deadline"],
                transport=transport, clock=ReplayClock(leaves), schema_audit_only=False)
            widths = provider.collect_records()
            _require(not transport.leaves, "candidate_unused_page")
            captures = sorted(native["captures"], key=lambda row: row["board_code"])
            _require([row["board_code"] for row in captures] == list(BOARDS),
                     "candidate_strength_coverage_or_duplicate")
            strengths, strength_trace = {}, {}
            for item in captures:
                _context(item, batch)
                board, capture = item["board_code"], item["capture"]
                _context(capture, batch)
                request = strength.board_strength_request(board)
                parsed = strength.parse_captured_board_strength(capture, request=request,
                    trade_date=batch["trade_date"], feature_cutoff=declared["feature_cutoff"],
                    collection_deadline=declared["collection_deadline"],
                    expected_raw_sha256=hashlib.sha256(base64.b64decode(capture["content_base64"], validate=True)).hexdigest(),
                    test_only=batch["test_only"])
                _require(parsed["execution_ok"] is True, "candidate_strength_rejected:" + parsed["error_code"])
                record = parsed["audit_records"][0]
                strengths[board] = record
                strength_trace[board] = _validate_leaf(capture, batch, version=strength.SOURCE_VERSION,
                    url=request["url"], params=request["params"], native_events=[_time(record["event_time"])],
                    clocks=clocks, strength_leaf=True)
            all_children = [*mapping_trace.values(), *[p for pages in page_trace.values() for p in pages],
                            *strength_trace.values()]
            for host in {urlparse(c["original_capture"]["response_url"]).hostname for c in all_children}:
                calls = sorted(c["original_capture"]["client_call_started_monotonic_seconds"]
                               for c in all_children if urlparse(c["original_capture"]["response_url"]).hostname == host)
                _require(all((b - a) * 1000 >= 500 for a, b in zip(calls, calls[1:])),
                         "candidate_client_call_spacing_invalid")
            boundaries = sorted(((_time(c["original_capture"]["process_started_at"]), 1)
                                 for c in all_children)) + sorted(((_time(c["original_capture"]["process_completed_at"]), -1)
                                                                  for c in all_children))
            active = 0
            for _, delta in sorted(boundaries):
                active += delta
                _require(0 <= active <= 2, "candidate_worker_limit_invalid")
            records = []
            for width in widths:
                code = width["payload"]["code"]
                board = width["payload"]["board_code"]
                native_record = strengths[board]
                children = [mapping_trace[code], *page_trace[board], strength_trace[board]]
                event = max(_time(t) for child in children for t in child["native_event_times"])
                observed = max(max(_time(c["original_capture"]["observed_at"]),
                                   _time(c["original_capture"]["request_completed_at"])) for c in children)
                available = max(_time(c["original_capture"]["available_at"]) for c in children)
                payload = deepcopy(width["payload"])
                payload.update(change_pct=native_record["payload"]["change_pct"],
                    event_time_granularity="native_components_no_truncation",
                    strength_event_time=native_record["event_time"],
                    strength_source_version=strength.SOURCE_VERSION,
                    strength_raw_fields=deepcopy(native_record["payload"]["raw_fields"]))
                payload["association_contract"] = {
                    "schema_version": ASSOCIATION_SCHEMA, "security_identity": security_identity(code),
                    "classification_system": CLASSIFICATION, "classification_version": CLASSIFICATION_VERSION,
                    "board_code": board, "board_name": payload["industry"], "origin_source": "eastmoney",
                    "source_version": SOURCE_VERSION, "mapping_source_version": BREADTH_VERSION,
                    "request_hash": mapping_trace[code]["request_hash"], "raw_hash": mapping_trace[code]["raw_hash"],
                }
                records.append({**deepcopy(width), "record_schema_version": RECORD_SCHEMA,
                    "source_version": SOURCE_VERSION, "composite_contract_version": CONTRACT_VERSION,
                    "source": "synthetic_test_only.eastmoney" if batch["test_only"] else "eastmoney_candidate_audit",
                    "test_only": batch["test_only"], "batch_id": batch["batch_id"], "published_at": None,
                    "temporal_contract": deepcopy(declared), "decision_cutoff": declared["decision_time"],
                    "event_time": event.isoformat(), "observed_at": observed.isoformat(),
                    "available_at": available.isoformat(), "payload": payload,
                    "request_hash": stable_hash([c["request_hash"] for c in children]),
                    "raw_hash": stable_hash([c["raw_hash"] for c in children]),
                    "component_provenance": deepcopy(children), "usage_scope": "candidate_audit_only",
                })
            result = _safe(status="COMPLETE_INDUSTRY_CANDIDATE_VALIDATED", execution_ok=True,
                candidate_batch_complete=True, audit_records=records, errors=[], batch_id=batch["batch_id"],
                test_only=batch["test_only"], captured_request_count=len(leaves) + len(captures),
                contract_version=CONTRACT_VERSION, contract_hash=contract_hash(),
                source_version=SOURCE_VERSION, strength_request_count=len(captures),
                breadth_request_count=len(leaves), candidate_hash=stable_hash(records))
            return result
        except (ValueError, KeyError, TypeError, OverflowError, OSError) as exc:
            return {**empty, "errors": [str(exc) or type(exc).__name__]}
        except Exception as exc:
            return {**empty, "errors": ["candidate_unexpected_failure:" + type(exc).__name__]}


def complete_industry_audit_plan(trade_date: str, *, page_counts: Mapping[str, int] | None = None) -> dict:
    """Pure planning. Existing executor limits are not altered or activated."""
    config = AuditTimeConfiguration(trade_date)
    pages = dict(page_counts) if page_counts is not None else dict.fromkeys(BOARDS, 1)
    _require(set(pages) == set(BOARDS) and all(type(v) is int and 1 <= v <= PER_BOARD_PAGE_LIMIT for v in pages.values())
             and sum(pages.values()) <= TOTAL_PAGE_LIMIT, "candidate_plan_page_limit_invalid")
    mapping_ids = tuple(f"10-mapping-{code}" for code in FIXED_CODES)
    jobs = [AuditRequest(key, CORE_CONCEPTION_URL,
                        {"code": ("SH" if code.startswith("6") else "SZ") + code})
            for code, key in zip(FIXED_CODES, mapping_ids)]
    for board in BOARDS:
        for page in range(1, pages[board] + 1):
            params = {"pn": str(page), "pz": str(PAGE_SIZE), "po": "1", "np": "1", "fltt": "2",
                      "invt": "2", "fid": "f3", "fs": f"b:{board}", "fields": "f2,f3,f12,f14,f124"}
            deps = mapping_ids if page == 1 else (f"20-page-{board}-{page-1}",)
            jobs.append(AuditRequest(f"20-page-{board}-{page}", EASTMONEY_CLIST_URL, params, deps))
        request = strength.board_strength_request(board)
        jobs.append(AuditRequest(f"30-strength-{board}", request["url"], request["params"],
                                 (f"20-page-{board}-{pages[board]}",)))
    material = config.material()
    nominal = schedule_budget(jobs, started_at=config.at("14:37:00"), deadline=config.at("14:40:00"))
    # Conservative bound: completion-to-next-start gaps cover cold-start variation.
    mapping_ms = 5 * 2000 + 4 * 500
    push_requests = sum(pages.values()) + len(BOARDS)
    push_ms = push_requests * 2000 + (push_requests - 1) * 500
    worst_ms = mapping_ms + push_ms + 1500
    safe_latest = config.at("14:40:00") - timedelta(milliseconds=worst_ms)
    return _safe(status="COMPLETE_INDUSTRY_AUDIT_PLAN_ONLY", execution_ok=True,
        source_version=SOURCE_VERSION, contract_hash=contract_hash(), time_configuration=material,
        requests=[j.material() for j in sorted(jobs, key=lambda j: j.request_id)],
        request_count=len(jobs), mapping_requests=5, constituent_pages=sum(pages.values()),
        strength_requests=4, page_counts=dict(sorted(pages.items())), page_size=PAGE_SIZE,
        total_page_limit=TOTAL_PAGE_LIMIT, per_board_page_limit=PER_BOARD_PAGE_LIMIT,
        maximum_request_count=5 + TOTAL_PAGE_LIMIT + 4,
        pagination_rule="first_page_total_determines_pages; unused_reserved_pages_not_dispatched",
        fixed_plan_changes_require_review=True, max_workers=2, same_host_in_flight_limit=2,
        actual_client_start_interval_ms=500, per_request_deadline_ms=2000,
        deadline_includes="process_start+rate_wait+request+body_read; queue_in_batch_budget",
        executor_dag_simulation=nominal, conservative_mapping_ms=mapping_ms,
        conservative_push2_ms=push_ms, cleanup_and_freeze_ms=1500,
        conservative_total_ms=worst_ms, conservative_safe_latest_start=safe_latest.isoformat(),
        planned_1437_feasible=(config.at("14:37:00") + timedelta(milliseconds=worst_ms) <= config.at("14:40:00")),
        approved_latest_start_safe_under_bound=(config.at("14:39:30") <= safe_latest),
        scheduling_authorized=False,
        stop_conditions=["calendar_or_session_or_clock_rejected", "window_or_budget_exhausted",
                         "page_count_not_preapproved", "missing_page_duplicate_or_total_drift",
                         "mapping_membership_identity_or_native_time_invalid", "any_child_failure",
                         "429_or_access_restriction", "pending_or_corrupt_coordination_journal"],
        plan_hash=stable_hash({"requests": [j.material() for j in jobs],
                               "time_configuration": material, "contract_hash": contract_hash()}))
