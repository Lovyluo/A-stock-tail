"""Offline projections of explicit captured Provider batches; never a transport."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

from overnight_quant.data import complete_industry_candidate as industry
from overnight_quant.data import market_source_providers as market
from overnight_quant.data import static_source_providers as static
from overnight_quant.data import exchange_announcement_providers as exchange
from overnight_quant.data.point_in_time import records_available_at, stable_hash
from overnight_quant.data.s2_integration_contract import FIXED_CODES, association_errors, security_identity
from overnight_quant.data.source_capability_adapters import (
    SourceProviderEnvelope, execute_source_adapter, get_source_adapter_registry,
    _execute_source_adapter_with_bindings_for_test,
)
from overnight_quant.data.source_capability_registry import validate_source_provenance_batch
from .contracts import (
    BENCHMARK, CAPABILITIES, CLASSIFICATION, INPUT_SCHEMA, MINUTE_INTERVAL_VERSION,
    RECORD_SCHEMA, REQUIRED_TYPES, STRATEGY_ID, formal_time_contract, requirement_hash, safety,
)
from .readiness import _SourceBridgeAdmission, number, require, sha, timestamp
from .strategy import NoFundFlowResearchStrategy

CAPTURE_SCHEMA = "noflow_captured_provider_batches_v1"
BRIDGE_VERSION = "noflow_real_record_bridge_v1"
EXCLUDED = {"fund_flow", "fund_flow_audit", "transaction", "order_book", "daily_bar", "cost_proxy"}
RISK_TYPES = {"stock_news", "global_news", "announcement"}
FILTER_CUTOFF_IDENTITIES = (frozenset(static.SOURCE_IDENTITIES.values())
                           | frozenset(market.SOURCE_IDENTITIES.values())
                           | frozenset(exchange.SOURCE_IDENTITIES.values()))


def bridge_contract_hash() -> str:
    needed = set(CAPABILITIES.values())
    return stable_hash({
        "version": BRIDGE_VERSION, "capture": CAPTURE_SCHEMA, "target": requirement_hash(),
        "identity": "exact_registry_identity_provider_key_security_and_BK_no_name_join",
        "industry_projection": "decision_counts_strength_and_association;per_security_mapping_capture_times_audit_only",
        "calendar": "retain_completed_historical_event_never_insert_target",
        "source_cutoff": "known_filter_cutoff_versions_only_preserve_native_cutoff_in_trace",
        "minute": "no_approved_real_unit_interval_finality_profile;synthetic_minute_start_only",
        "risk": "no_inferred_CLEAR_or_tradability;zero_items_requires_captured_200_response",
        "quote": "CNY_price_and_native_CNY_limits_only;limit_flags_derived;suspension_unproven",
        "publication_precision": "date_only_strictly_before_target_trade_date_keep_native_publication_event",
        "source_versions": {"static": {k: v for k, v in static.SOURCE_IDENTITIES.items() if k in needed},
                            "market": {k: v for k, v in market.SOURCE_IDENTITIES.items() if k in needed},
                            "exchange": exchange.SOURCE_IDENTITIES, "industry": industry.SOURCE_VERSION},
        "industry_strength_contract_hash": industry.strength.candidate_contract_hash(),
        "source_bindings": [row for row in get_source_adapter_registry() if row["capability"] in needed],
        "approved_real_minute_profiles": [],
        "authority": "production_adapter_and_hard_gate_provenance;runtime_row_digest_not_JSON_flag",
        "output": "audit_projection_only;formal_records_and_trading_outputs_empty",
    })


def _identity(batch: dict) -> tuple:
    return tuple(batch.get(k) for k in ("origin_source", "adapter", "source_version"))


def _check_source(batch: dict, kind: str) -> tuple[list, dict]:
    rows = batch["records"]
    require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), "provider_records_invalid")
    capability = CAPABILITIES[kind]
    require(batch.get("capability") == capability, "provider_capability_mismatch")
    bindings = [b for b in get_source_adapter_registry() if b["capability"] == capability
                and tuple(b[k] for k in ("origin_source", "adapter", "source_version")) == _identity(batch)]
    composite = kind == "industry" and _identity(batch) == ("eastmoney", "direct_http", industry.SOURCE_VERSION)
    require(len(bindings) == 1 or composite, "provider_source_or_version_unknown")
    expected = industry.PROVIDER_KEY if composite else (bindings[0]["provider_key"] or bindings[0]["candidate_provider_key"])
    require(bool(expected) and batch.get("provider_key") == expected, "provider_key_mismatch")
    for row in rows:
        require(row.get("capability") == capability and _identity(row) == _identity(batch), "record_source_identity_mismatch")
        require(row.get("test_only", False) is batch["test_only"], "provider_scope_mismatch")
        if batch["test_only"]:
            require(str(row.get("source", "")).startswith("synthetic_test_only."), "synthetic_provider_scope_missing")
        require(all(sha(row.get(k)) for k in ("request_hash", "raw_hash")), "provider_hash_invalid")
    envelope = SourceProviderEnvelope(expected, lambda: deepcopy(rows))
    args = dict(origin_source=batch["origin_source"], adapter=batch["adapter"],
                source_version=batch["source_version"], provider_envelope=envelope, environ={})
    if batch["test_only"]:
        adapter = _execute_source_adapter_with_bindings_for_test(
            capability, entries=get_source_adapter_registry(), **args)
    else:
        adapter = execute_source_adapter(capability, **args)
    return rows, adapter


def _metadata(row: dict, batch: dict, capture: dict, kind: str, payload: dict) -> dict:
    contract = capture["time_contract"]
    cutoff, deadline = (timestamp(contract[k]) for k in ("feature_event_cutoff", "collection_deadline"))
    event, observed, available = (timestamp(row.get(k)) for k in ("event_time", "observed_at", "available_at"))
    require(event <= observed <= available <= deadline and event <= cutoff, "provider_time_order_invalid")
    require(observed.date().isoformat() == available.date().isoformat() == capture["trade_date"], "provider_collection_date_mismatch")
    if kind not in RISK_TYPES | {"trading_calendar"}:
        require(event.date() == cutoff.date(), "provider_event_date_mismatch")
    require(row.get("batch_id", capture["batch_id"]) == capture["batch_id"]
            and row.get("trade_date", capture["trade_date"]) == capture["trade_date"], "provider_batch_or_date_mismatch")
    source_cutoff = row.get("decision_cutoff")
    filter_cutoff = _identity(batch) in FILTER_CUTOFF_IDENTITIES
    if source_cutoff is not None:
        expected_cutoff = contract["feature_event_cutoff"] if filter_cutoff else contract["decision_time"]
        require(timestamp(source_cutoff) == timestamp(expected_cutoff), "native_cutoff_contract_mismatch")
    if "temporal_contract" in row:
        require(row["temporal_contract"] == {"feature_cutoff": contract["feature_event_cutoff"],
            "collection_deadline": contract["collection_deadline"], "decision_time": contract["decision_time"],
            "execution_start": contract["execution_not_before"]}, "provider_temporal_contract_mismatch")
    if kind == "market":
        native_times = [p["raw_event_time"] for p in row["payload"]["index_breadth"].values()]
        native_times.append(row["payload"]["raw_benchmark_event_time"])
        require(all(timestamp(t).date() == cutoff.date() and timestamp(t) <= cutoff for t in native_times),
                "market_native_event_date_or_cutoff_invalid")
    if kind == "industry":
        children = row.get("component_provenance", [])
        require(capture["test_only"] or len(children) >= 3, "industry_child_provenance_missing")
        for child in children:
            native = child["original_capture"]
            require(native["batch_id"] == capture["batch_id"] and native["trade_date"] == capture["trade_date"],
                    "industry_child_batch_or_date_mismatch")
            require(native["time_contract_hash"] == stable_hash(row["temporal_contract"]),
                    "industry_child_time_contract_mismatch")
            completed, child_available = timestamp(native["request_completed_at"]), timestamp(native["available_at"])
            require(completed <= child_available <= available <= deadline, "industry_child_completion_invalid")
            raw = base64.b64decode(native["content_base64"], validate=True)
            require(hashlib.sha256(raw).hexdigest() == native["raw_hash"] == child["raw_hash"], "industry_child_raw_hash_invalid")
            if child["source_version"] == industry.strength.SOURCE_VERSION:
                parsed = industry.strength.parse_captured_board_strength(native,
                    request=industry.strength.board_strength_request(payload["board_code"]),
                    trade_date=capture["trade_date"], feature_cutoff=contract["feature_event_cutoff"],
                    collection_deadline=contract["collection_deadline"], expected_raw_sha256=native["raw_hash"],
                    test_only=capture["test_only"])
                require(parsed["execution_ok"] and parsed["audit_records"][0]["payload"]["raw_fields"]
                        == payload["strength_raw_fields"], "industry_strength_raw_replay_rejected")
            for native_time in child["native_event_times"]:
                instant = timestamp(native_time)
                require(instant <= completed and instant.date() == cutoff.date() and instant <= cutoff,
                        "industry_child_native_event_invalid")
    synthetic = capture["test_only"]
    result = {
        **deepcopy(row), "record_schema_version": RECORD_SCHEMA, "data_type": kind,
        "capability": CAPABILITIES[kind], "test_only": synthetic,
        "batch_id": capture["batch_id"], "trade_date": capture["trade_date"],
        "requirements_hash": requirement_hash(), "time_contract_hash": stable_hash(contract),
        "source": row.get("source") or f"{batch['origin_source']}.{batch['adapter']}",
        "provider_key": batch["provider_key"], "decision_cutoff": contract["decision_time"],
        "published_at": row.get("published_at"), "payload": payload, "payload_hash": stable_hash(payload),
        "bridge_trace": {"version": BRIDGE_VERSION, "contract_hash": capture["_bridge_contract_hash"],
            "provider_record_hash": stable_hash(row), "native_identity": list(_identity(row)),
            "native_source": row.get("source"), "native_decision_cutoff": source_cutoff,
            "native_cutoff_semantics": "feature_filter" if filter_cutoff else "decision_authorization_or_absent",
            "derived_decision_cutoff_origin": "target_four_stage_contract_not_native_event",
            "provider_payload": deepcopy(row.get("payload")),
            "native_times": {k: row.get(k) for k in ("event_time", "observed_at", "available_at", "published_at")}},
    }
    if synthetic:
        result.update(source="synthetic_test_only.noflow", origin_source="synthetic_test_only",
                      source_version="synthetic_noflow_v2", adapter="test_only")
        mapping = payload.get("association_contract")
        if kind == "industry" and isinstance(mapping, dict):
            mapping.update(source_version=result["source_version"], origin_source=result["origin_source"])
            result["payload_hash"] = stable_hash(payload)
    accepted, rejected = records_available_at([result], contract["decision_time"], time_contract=contract)
    require(bool(accepted) and not rejected, "bridge_point_in_time_rejected")
    return result


def project_payload(kind: str, row: dict, *, test_only: bool) -> dict:
    """Field compatibility projection, never a source/temporal authorization."""
    payload = deepcopy(row.get("payload"))
    require(isinstance(payload, dict), "provider_payload_missing")
    if kind in {"quote", "industry", "minute_bar"}:
        payload["code"] = security_identity(payload.get("code")).split(":")[1]
    units = payload.get("field_units") or {}
    if kind == "quote":
        require(units.get("price") == units.get("prev_close") == "CNY_per_share"
                and units.get("change_pct") == "percent", "quote_unit_unproven")
        require(units.get("limit_up") == units.get("limit_down") == "CNY_per_share", "quote_limit_unit_unproven")
        price, upper, lower = (number(payload.get(k), "quote_limit_or_price_invalid") for k in ("price", "limit_up", "limit_down"))
        require(0 < lower < upper and price > 0, "quote_limit_or_price_invalid")
        payload.update(price_unit="CNY", change_unit="percent", is_limit_up=price >= upper, is_limit_down=price <= lower)
        # Native Tencent quotes do not establish suspension/tradability review.
        if not test_only:
            payload.update(suspended=None, tradability_status="UNPROVEN")
    elif kind == "market":
        require(market._valid_market_record(row), "market_native_contract_invalid")
        require(payload.get("benchmark_secid") == "1.000001" and units.get("index_change_pct") == "percent",
                "market_benchmark_or_unit_unproven")
        native_pool = payload["stock_pool_version"]
        payload.update(benchmark_identity=BENCHMARK, change_unit="percent", source_stock_pool_version=native_pool,
                       stock_pool_version=stable_hash({"origin_source": row["origin_source"],
                           "scope": payload["stock_pool_scope"], "source_stock_pool_version": native_pool}))
    elif kind == "industry":
        require(not association_errors(row), "industry_mapping_invalid")
        require(units.get("change_pct") == "percent" and payload.get("classification_version") == CLASSIFICATION,
                "industry_unit_or_classification_unproven")
        number(payload.get("change_pct"), "industry_strength_missing")
        raw = payload.get("strength_raw_fields") or {}
        require(raw.get("f57") == payload["board_code"] and raw.get("f58") == industry.strength.BOARD_NAMES[payload["board_code"]],
                "industry_strength_native_identity_invalid")
        precision = raw.get("f152")
        require(type(precision) is int and 0 <= precision <= 8, "industry_strength_precision_unproven")
        native_change = number(raw.get("f170"), "industry_strength_raw_value_invalid")
        require(float(Decimal(str(native_change)).scaleb(-precision)) == payload["change_pct"],
                "industry_strength_raw_projection_mismatch")
        require(type(raw.get("f86")) is int and raw["f86"] > 0, "industry_strength_native_time_missing")
        native_event = datetime.fromtimestamp(raw["f86"], timestamp(row["event_time"]).tzinfo)
        require(timestamp(payload["strength_event_time"]) == native_event <= timestamp(row["event_time"])
                and payload["strength_source_version"] == industry.strength.SOURCE_VERSION,
                "industry_strength_native_time_or_version_invalid")
        payload["change_unit"] = "percent"
        require(payload["association_contract"]["security_identity"] == security_identity(payload["code"]),
                "industry_security_mismatch")
        # Per-security mapping timestamps are not board-wide decision fields.
        # All native fields remain in bridge_trace.provider_payload.
        fields = ("code", "board_code", "classification_version", "association_contract", "change_pct", "change_unit",
                  "up_count", "down_count", "flat_count", "invalid_count", "valid_count", "total_count", "breadth_ratio",
                  "strength_raw_fields", "strength_event_time", "strength_source_version")
        payload = {key: deepcopy(payload[key]) for key in fields}
    elif kind == "trading_calendar":
        require(payload.get("calendar_kind") == "benchmark_index_trade_dates"
                and payload.get("exchange_scope") == ["SSE", "SZSE"], "completed_calendar_contract_invalid")
    elif kind == "minute_bar":
        # No real source profile yet proves shares/CNY interval values + finality
        # for this strategy. Existing minute_end evidence is not relabelled.
        require(test_only, "MINUTE_SOURCE_INTERVAL_SEMANTICS_UNPROVEN")
        require(units == {"price": "CNY", "volume": "shares", "amount": "CNY"}
                and payload.get("aggregation_semantics") == "interval", "minute_units_unproven")
        payload.update(price_unit="CNY", volume_unit="shares", amount_unit="CNY", interval_seconds=60)
    return payload


def _empty_risk_response(body: dict, kind: str, origin: str, cutoff) -> bool:
    """Replay the existing Provider's empty/after-cutoff filtering, not risk review."""
    if kind == "global_news":
        items = (body.get("data") or {}).get("fastNewsList")
    elif kind == "stock_news":
        items = (body.get("result") or {}).get("cmsArticleWebOld")
        if isinstance(items, dict):
            items = items.get("list")
    elif origin == "sse":
        groups = body.get("result")
        if not isinstance(groups, list):
            return False
        items = [item for group in groups for item in (group if isinstance(group, list) else [group])]
    elif origin == "szse":
        items = body.get("data")
    else:
        items = body.get("announcements")
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        if origin in {"sse", "szse"}:
            published, precision = exchange._publication_time(item.get("SSEDATE" if origin == "sse" else "publishTime"))
            excluded = published >= cutoff or (precision == "date" and published.date() == cutoff.date())
        else:
            published = static._published_at(item)
            excluded = published is not None and published > cutoff
        if not excluded:
            return False
    return True


def _risk_rows(batch: dict, capture: dict, kind: str) -> list:
    rows = batch["records"]
    responses = batch.get("responses")
    require(isinstance(responses, list) and bool(responses), "risk_source_response_missing")
    groups = {}
    endpoints = {("eastmoney", "stock_news"): static.EASTMONEY_STOCK_NEWS_URL,
                 ("eastmoney", "global_news"): static.EASTMONEY_GLOBAL_NEWS_URL,
                 ("cninfo", "announcement"): static.CNINFO_ANNOUNCEMENT_URL,
                 ("sse", "announcement"): exchange.SSE_ANNOUNCEMENT_URL,
                 ("szse", "announcement"): exchange.SZSE_ANNOUNCEMENT_URL,
                 ("bse", "announcement"): exchange.BSE_ANNOUNCEMENT_URL}
    for response in responses:
        require(type(response.get("http_status_code")) is int and response["http_status_code"] == 200,
                "risk_source_request_failed")
        raw = base64.b64decode(response["content_base64"], validate=True)
        require(bool(raw) and hashlib.sha256(raw).hexdigest() == response.get("raw_hash")
                and sha(response.get("request_hash")), "risk_response_hash_invalid")
        url = urlparse(response.get("response_url", ""))
        approved = urlparse(endpoints.get((batch["origin_source"], kind), ""))
        require(url.scheme == "https" and url.netloc == approved.netloc and url.path == approved.path and not url.fragment,
                "risk_response_source_invalid")
        expected_method = "GET" if kind in {"stock_news", "global_news"} or batch["origin_source"] == "sse" else "POST"
        require(response.get("url") == approved.geturl() and response.get("method") == expected_method
                and response.get("redirect_chain", []) == [], "risk_request_endpoint_invalid")
        text = raw.decode("utf-8-sig").strip()
        callback = "jQuery_static_source"
        if kind == "stock_news" and text.startswith(callback + "("):
            close = text.rfind(")")
            require(close > len(callback) and text[close + 1:] in {"", ";"}, "news_jsonp_wrapper_invalid")
            text = text[len(callback) + 1:close]
        body = json.loads(text)
        require(isinstance(body, dict), "risk_response_not_json_object")
        response = {**response, "_bridge_empty_body": body}
        start, end = timestamp(response["observed_at"]), timestamp(response["available_at"])
        require(start <= end <= timestamp(capture["time_contract"]["collection_deadline"])
                and start.date().isoformat() == end.date().isoformat() == capture["trade_date"], "risk_response_time_invalid")
        code = response.get("scope_code", "")
        if batch["origin_source"] in {"sse", "szse", "bse"}:
            material = exchange._request_material(response["method"], response["url"],
                params=response.get("params"), data=response.get("data"), json_body=response.get("json_body"))
            require(exchange._request_code(batch["origin_source"], response) == code, "risk_request_scope_mismatch")
        else:
            material = {"method": response["method"], "url": response["url"],
                        "params": response.get("params") or [], "data": response.get("data") or []}
            if kind == "stock_news":
                require(json.loads(dict(material["params"])["param"])["keyword"] == code, "risk_request_scope_mismatch")
            if kind == "announcement":
                require(dict(material["data"])["stock"] == f"{code},{static._cninfo_org_id(code)}", "risk_request_scope_mismatch")
        require(stable_hash(material) == response["request_hash"], "risk_request_hash_invalid")
        if kind != "global_news":
            identity = security_identity(code)
            require(identity.split(":")[1] == code and code in FIXED_CODES, "risk_security_invalid")
            exchange_identity = {"sse": "SH", "szse": "SZ", "bse": "BJ"}.get(batch["origin_source"])
            require(exchange_identity is None or identity.startswith(exchange_identity + ":"), "announcement_exchange_route_mismatch")
        else:
            require(code == "", "global_news_scope_invalid")
        groups.setdefault(code, []).append(response)
    result = []
    for code, receipts in sorted(groups.items()):
        selected = [r for r in rows if (r.get("payload") or {}).get("code", "") == code]
        if not selected:
            for receipt in receipts:
                empty = _empty_risk_response(receipt["_bridge_empty_body"], kind, batch["origin_source"],
                                             timestamp(capture["time_contract"]["feature_event_cutoff"]))
                require(empty, "risk_empty_source_not_replayed")
        items = []
        for row in selected:
            _metadata(row, batch, capture, kind, deepcopy(row["payload"]))
            require(any(row["request_hash"] == r["request_hash"] and row["raw_hash"] == r["raw_hash"] for r in receipts),
                    "risk_record_response_mismatch")
            published = timestamp(row.get("published_at"))
            precision = row.get("published_at_precision") or row["payload"].get("published_at_precision")
            require(precision in {"date", "datetime"}, "publication_precision_unproven")
            require(precision == "datetime" or published.date().isoformat() < capture["trade_date"],
                    "publication_time_precision_insufficient")
            require(published <= timestamp(capture["time_contract"]["feature_event_cutoff"]), "risk_future_publication")
            if kind == "announcement" and batch["origin_source"] in {"sse", "szse", "bse"}:
                require(published < timestamp(capture["time_contract"]["feature_event_cutoff"]), "announcement_source_cutoff_rejected")
            items.append({**deepcopy(row["payload"]), "published_at": row["published_at"],
                          "published_at_precision": precision, "origin_source": row["origin_source"], "raw_hash": row["raw_hash"]})
        observed = max((r["observed_at"] for r in receipts), key=timestamp)
        available = max((r["available_at"] for r in receipts), key=timestamp)
        native = {"capability": batch["capability"], **{k: batch[k] for k in ("origin_source", "adapter", "source_version")},
            "test_only": capture["test_only"], "event_time": max((r["event_time"] for r in selected), key=timestamp, default=observed),
            "observed_at": observed, "available_at": available,
            "request_hash": stable_hash(sorted(r["request_hash"] for r in receipts)),
            "raw_hash": stable_hash(sorted(r["raw_hash"] for r in receipts)), "payload": {}}
        # A successful request is not an independent CLEAR risk review.
        review = batch.get("risk_review_status") if capture["test_only"] else "UNPROVEN"
        payload = {"code": code, "status": "SUCCESS", "risk_review_status": review,
                   "items": sorted(items, key=stable_hash), "status_event_semantics": "publication_or_empty_query_observation"}
        result.append(_metadata(native, batch, capture, kind, payload))
    require(all(any((r.get("payload") or {}).get("code", "") == code for code in groups) for r in rows),
            "risk_record_outside_request_scope")
    return result


def bridge_captured_records(capture: dict, *, test_only: bool = False) -> dict:
    result = {**safety(), "bridge_version": BRIDGE_VERSION, "bridge_contract_hash": bridge_contract_hash(),
              "test_only": test_only, "status": "POINT_IN_TIME_DATA_INCOMPLETE", "execution_ok": True,
              "module_results": [], "audit_records": [], "missing_reasons": [], "research_ranking": []}
    result["ingest_hash"] = None
    try:
        require(isinstance(capture, dict) and capture.get("schema_version") == CAPTURE_SCHEMA, "capture_schema_invalid")
        result["ingest_hash"] = stable_hash(capture)
        capture = deepcopy(capture)
        capture["_bridge_contract_hash"] = result["bridge_contract_hash"]
        require(type(test_only) is bool and capture.get("test_only") is test_only, "capture_scope_invalid")
        if test_only:
            require(capture.get("input_scope") == "synthetic_provider_capture_test_only", "synthetic_capture_scope_missing")
        require(capture.get("strategy_id") == STRATEGY_ID and sha(capture.get("batch_id")), "capture_strategy_or_batch_invalid")
        expected = formal_time_contract(capture["trade_date"])
        contract = capture["time_contract"]
        require(all(contract.get(k) == expected[k] for k in ("contract_version", "feature_event_cutoff", "collection_deadline",
                "decision_time", "execution_not_before", "minute_label_semantics", "is_final")), "formal_time_contract_mismatch")
        require(isinstance(capture.get("batches"), list), "provider_batches_missing")
        snapshot = {"schema_version": INPUT_SCHEMA, "strategy_id": STRATEGY_ID, "test_only": test_only,
                    "batch_id": capture["batch_id"], "trade_date": capture["trade_date"],
                    "time_contract": deepcopy(contract), "records": []}
        eligible = set()
        batches = [b for b in capture["batches"] if isinstance(b, dict) and b.get("data_type") not in EXCLUDED]
        require(all(isinstance(b, dict) for b in capture["batches"]), "provider_batch_not_object")
        for batch in sorted({stable_hash(b): b for b in batches}.values(), key=stable_hash):
            kind = batch.get("data_type")
            entry = {"data_type": kind, "identity": list(_identity(batch)), "conversion_ok": False, "errors": []}
            try:
                require(kind in REQUIRED_TYPES and batch.get("test_only") is test_only, "provider_batch_scope_invalid")
                require(batch.get("batch_id") == capture["batch_id"] and batch.get("trade_date") == capture["trade_date"],
                        "provider_batch_or_date_mismatch")
                rows, adapter = _check_source(batch, kind)
                entry.update(adapter_result=adapter, source_admitted=False, provider_mode="captured_records_only_no_transport")
                converted = []
                if kind in RISK_TYPES:
                    converted = _risk_rows(batch, capture, kind)
                else:
                    require(bool(rows), "provider_records_empty")
                    for row in sorted({stable_hash(r): r for r in rows}.values(), key=stable_hash):
                        payload = project_payload(kind, row, test_only=test_only)
                        if kind == "trading_calendar":
                            try:
                                static._validate_calendar_payload(payload, timestamp(contract["feature_event_cutoff"]).date())
                            except static.StaticSourceContractError as exc:
                                raise ValueError(str(exc)) from exc
                            require(row["event_time"] == payload["latest_completed_trade_date"] + "T15:00:00+08:00",
                                    "calendar_native_event_mismatch")
                        normalized = _metadata(row, batch, capture, kind, payload)
                        if kind == "minute_bar":
                            require(row.get("minute_label_semantics") == "minute_start"
                                    and row.get("native_label", row["event_time"]) == row["event_time"]
                                    and row.get("interval_semantics_validation_status") == "VERIFIED_SYNTHETIC_TEST_ONLY"
                                    and row.get("interval_semantics_evidence_hash") == contract["probe_evidence_hash"],
                                    "minute_label_or_semantics_unproven")
                            native = timestamp(row["event_time"])
                            normalized.update(native_label=row["event_time"], interval_start=row["event_time"],
                                interval_end=(native + timedelta(seconds=60)).isoformat(),
                                minute_interval_contract_version=MINUTE_INTERVAL_VERSION)
                            # Do not repair contradictory supplied boundaries.
                            require(row.get("interval_start", normalized["interval_start"]) == normalized["interval_start"]
                                    and row.get("interval_end", normalized["interval_end"]) == normalized["interval_end"],
                                    "minute_label_interval_mismatch")
                        converted.append(normalized)
                if not test_only and adapter["status"] == "SOURCE_ADAPTER_BOUND":
                    provenance = validate_source_provenance_batch(batch["capability"], rows, require_hard_gate=True)
                    entry["source_admitted"] = provenance.get("hard_gate_authorized") is True
                    if entry["source_admitted"]:
                        eligible.update(stable_hash(r) for r in converted)
                if not test_only:
                    entry["admission_blockers"] = [] if entry["source_admitted"] else [
                        "source_not_qualified_or_bound:" + adapter["status"] + ":" + str(adapter.get("route_status"))]
                    if kind == "quote":
                        entry["admission_blockers"].append("quote_tradability_evidence_unproven")
                    if kind in RISK_TYPES:
                        entry["admission_blockers"].append("risk_review_contract_unproven")
                snapshot["records"].extend(converted)
                entry.update(conversion_ok=True, converted_count=len(converted))
            except (KeyError, ValueError, TypeError, OverflowError, AttributeError, OSError,
                    exchange.ExchangeAnnouncementContractError, static.StaticSourceContractError) as exc:
                entry["errors"] = [str(exc)]
            result["module_results"].append(entry)
        result["audit_records"] = sorted(snapshot["records"], key=stable_hash)
        module_errors = [error for m in result["module_results"]
                         for error in [*m["errors"], *m.get("admission_blockers", [])]]
        # A rejected extra batch cannot leave a nested successful ranking behind.
        admitted_snapshot = snapshot if not module_errors else {**snapshot, "records": []}
        research = NoFundFlowResearchStrategy().evaluate_snapshot(
            admitted_snapshot, test_only=test_only, _bridge=_SourceBridgeAdmission(frozenset(eligible)))
        errors = sorted(set(module_errors + research["missing_reasons"]))
        result.update(research_result=research, missing_reasons=errors,
                      status=research["status"] if not errors else "POINT_IN_TIME_DATA_INCOMPLETE",
                      research_ranking=research["research_ranking"] if not errors else [],
                      research_ready=research["research_ready"] and not errors,
                      decision_hash=stable_hash({"bridge": bridge_contract_hash(), "research": research["decision_hash"], "errors": errors}))
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError, OSError,
            exchange.ExchangeAnnouncementContractError, static.StaticSourceContractError) as exc:
        result.update(missing_reasons=[str(exc)], research_ready=False)
        result["decision_hash"] = stable_hash({"bridge": bridge_contract_hash(), "errors": result["missing_reasons"]})
    result["snapshot_hash"] = stable_hash({"namespace": BRIDGE_VERSION, "decision_hash": result["decision_hash"]})
    return result
