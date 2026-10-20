"""Separate records-first admission. Never relaxes the old strategy's readiness."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import math
import re

from overnight_quant.data.point_in_time import records_available_at, stable_hash
from overnight_quant.data.s2_integration_contract import FIXED_CODES, security_identity
from overnight_quant.data.source_capability_adapters import get_source_adapter_registry
from overnight_quant.data.source_capability_registry import validate_source_provenance_batch
from .contracts import (
    BENCHMARK, CAPABILITIES, CLASSIFICATION, INPUT_SCHEMA, MINUTE_INTERVAL_CONTRACT,
    MINUTE_INTERVAL_VERSION, PARAMETERS, RECORD_SCHEMA,
    REQUIRED_TYPES, STRATEGY_ID, formal_time_contract, requirement_hash,
)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def number(value, reason: str) -> float:
    require(type(value) in (int, float) and math.isfinite(value), reason)
    return float(value)


def timestamp(value) -> datetime:
    require(type(value) is str, "timestamp_missing")
    parsed = datetime.fromisoformat(value)
    require(parsed.utcoffset() == timedelta(hours=8), "timezone_invalid")
    return parsed


def sha(value) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def breadth(payload: dict) -> None:
    fields = ("up_count", "down_count", "flat_count", "invalid_count", "valid_count", "total_count")
    require(all(type(payload.get(k)) is int and payload[k] >= 0 for k in fields), "breadth_counts_invalid")
    valid = payload["up_count"] + payload["down_count"] + payload["flat_count"]
    require(valid > 0 and valid == payload["valid_count"]
            and valid + payload["invalid_count"] == payload["total_count"], "breadth_counts_inconsistent")
    require(abs(number(payload.get("breadth_ratio"), "breadth_ratio_missing")
                - payload["up_count"] / valid) < 1e-12, "breadth_ratio_inconsistent")


def _admission(row: dict, *, test_only: bool) -> None:
    if test_only:
        require(row.get("test_only") is True and row.get("source") == "synthetic_test_only.noflow"
                and row.get("origin_source") == "synthetic_test_only"
                and row.get("source_version") == "synthetic_noflow_v2"
                and row.get("adapter") == "test_only", "synthetic_scope_invalid")
        return
    require(row.get("test_only") is False, "test_only_not_formal")
    capability = CAPABILITIES[row["data_type"]]
    bindings = [r for r in get_source_adapter_registry() if all(
        r.get(key) == row.get(key) for key in ("capability", "origin_source", "adapter", "source_version")
    )]
    require(len(bindings) == 1 and bindings[0]["implementation_status"] == "bound"
            and bindings[0]["provider_key"] == row.get("provider_key"), "source_not_bound")
    provenance = validate_source_provenance_batch(capability, [row], require_hard_gate=True)
    require(provenance.get("hard_gate_authorized") is True, "source_not_qualified")
    # No audited bridge for the new research risk/unit/interval schema exists yet.
    # A source label or a self-signed payload cannot stand in for such a bridge.
    raise ValueError("verified_noflow_source_bridge_not_available")


def _minute_interval(row: dict, snapshot: dict, times: dict, *, test_only: bool) -> None:
    require(all(key in row for key in MINUTE_INTERVAL_CONTRACT["fields"]), "minute_interval_metadata_missing")
    require(row.get("minute_interval_contract_version") == MINUTE_INTERVAL_VERSION, "minute_interval_contract_mismatch")
    native, event, start, end, finalized, observed, available = (
        timestamp(row[key]) for key in (
            "native_label", "event_time", "interval_start", "interval_end", "finalized_at", "observed_at", "available_at",
        )
    )
    require(row["minute_label_semantics"] == snapshot["time_contract"]["minute_label_semantics"] == "minute_start"
            and native == event == start, "minute_label_interval_mismatch")
    require(end - start == timedelta(seconds=60), "minute_interval_length_invalid")
    require(start.date() == end.date() == times["feature_event_cutoff"].date(), "minute_interval_date_invalid")
    require(end <= times["feature_event_cutoff"], "minute_interval_end_after_feature_cutoff")
    probe = snapshot["time_contract"]["probe_evidence_hash"]
    require(row["interval_semantics_evidence_hash"] == probe
            and row["finalization_evidence_hash"] == probe,
            "minute_interval_evidence_mismatch")
    # Hash equality binds the synthetic fixture; it is not proof of real semantics.
    # Production still requires the independent source bridge in _admission().
    require(test_only and row["interval_semantics_validation_status"] == "VERIFIED_SYNTHETIC_TEST_ONLY",
            "minute_interval_semantics_unverified")
    require(end <= finalized <= observed <= available, "minute_finalization_before_interval_end_or_observation")


def _validate_row(row: dict, snapshot: dict, times: dict, *, test_only: bool) -> None:
    kind = row["data_type"]
    require(row.get("record_schema_version") == RECORD_SCHEMA, "record_schema_mismatch")
    require(row.get("capability") == CAPABILITIES[kind], "capability_mismatch")
    require(row.get("batch_id") == snapshot["batch_id"]
            and row.get("trade_date") == snapshot["trade_date"], "cross_batch_or_date")
    require(row.get("requirements_hash") == requirement_hash(), "requirements_mismatch")
    require(row.get("time_contract_hash") == stable_hash(snapshot["time_contract"]), "time_contract_mismatch")
    require(all(sha(row.get(k)) for k in ("request_hash", "raw_hash")), "provenance_hash_invalid")
    require(isinstance(row.get("payload"), dict), "payload_invalid")
    require(row.get("payload_hash") == stable_hash(row["payload"]), "payload_hash_invalid")
    _admission(row, test_only=test_only)
    if kind == "minute_bar":
        _minute_interval(row, snapshot, times, test_only=test_only)
    event, observed, available = (timestamp(row.get(k)) for k in ("event_time", "observed_at", "available_at"))
    require(event <= observed <= available <= times["collection_deadline"], "time_order_invalid")
    require(observed.date() == available.date() == times["feature_event_cutoff"].date(), "observation_date_invalid")
    require(event.date() == times["feature_event_cutoff"].date(), "event_date_invalid")
    require(timestamp(row.get("decision_cutoff")) == times["decision_time"], "decision_cutoff_invalid")
    accepted, rejected = records_available_at(
        [row], times["decision_time"], time_contract=snapshot["time_contract"],
    )
    require(bool(accepted) and not rejected, "point_in_time_rejected")
    if kind in {"stock_news", "global_news", "announcement"}:
        payload = row["payload"]
        require(payload.get("status") == "SUCCESS" and payload.get("risk_review_status") == "CLEAR",
                "risk_source_failed_or_unknown")
        items = payload.get("items")
        require(isinstance(items, list), "risk_items_invalid")
        for item in items:
            require(isinstance(item, dict), "risk_item_invalid")
            published = timestamp(item.get("published_at"))
            require(published <= event and published <= times["feature_event_cutoff"]
                    and published <= observed, "risk_publication_invalid")
            require(item.get("published_at_precision") == "datetime"
                    or (item.get("published_at_precision") == "date" and published.date() < event.date()),
                    "publication_time_precision_insufficient")
            require(sha(item.get("raw_hash")) and bool(item.get("origin_source")), "risk_item_provenance_missing")


def _materialize(rows: list[dict], times: dict) -> dict:
    by_type = {kind: [r for r in rows if r["data_type"] == kind] for kind in REQUIRED_TYPES}
    require(all(by_type.values()), "required_source_missing")
    for kind in ("market", "trading_calendar", "global_news"):
        require(len(by_type[kind]) == 1, f"{kind}_conflict")
    market_row = by_type["market"][0]
    market = market_row["payload"]
    require(market.get("benchmark_identity") == BENCHMARK
            and market.get("change_unit") == "percent", "market_benchmark_or_unit_invalid")
    require(market.get("stock_pool_scope") == "sse+szse+bse_index_breadth_counts"
            and sha(market.get("stock_pool_version")), "market_pool_invalid")
    number(market.get("index_change_pct"), "market_strength_missing")
    breadth(market)
    require(timestamp(market_row["event_time"]) == times["feature_event_cutoff"], "benchmark_alignment_invalid")
    calendar = by_type["trading_calendar"][0]["payload"]
    dates = calendar.get("trade_dates")
    require(isinstance(dates, list) and bool(dates) and all(type(day) is str for day in dates), "calendar_missing")
    require(dates == sorted(set(dates)) and all(
        datetime.strptime(day, "%Y-%m-%d").date() < times["feature_event_cutoff"].date() for day in dates
    ) and calendar.get("latest_completed_trade_date") == dates[-1], "completed_calendar_invalid")
    stocks = {}
    for code in FIXED_CODES:
        identity = security_identity(code)
        selected = {}
        for kind in ("quote", "industry", "stock_news", "announcement", "minute_bar"):
            matches = [r for r in by_type[kind] if security_identity(r["payload"].get("code")) == identity]
            if kind == "minute_bar":
                selected[kind] = sorted(matches, key=lambda r: timestamp(r["interval_end"]))
            else:
                require(len(matches) == 1, f"{code}:{kind}_missing_or_conflicting")
                selected[kind] = matches[0]
        quote_row, industry_row = selected["quote"], selected["industry"]
        quote, industry = quote_row["payload"], industry_row["payload"]
        require(timestamp(quote_row["event_time"]) == timestamp(industry_row["event_time"])
                == times["feature_event_cutoff"], "relative_event_alignment_invalid")
        require(quote.get("price_unit") == "CNY" and quote.get("change_unit") == "percent", "quote_unit_invalid")
        price, previous = (number(quote.get(k), "quote_price_invalid") for k in ("price", "prev_close"))
        change = number(quote.get("change_pct"), "quote_change_invalid")
        require(price > 0 and previous > 0 and abs(change - (price / previous - 1) * 100) <= 0.02,
                "quote_return_inconsistent")
        require(all(quote.get(k) is False for k in ("suspended", "is_limit_up", "is_limit_down"))
                and quote.get("tradability_status") == "VERIFIED_TRADABLE", "tradability_unknown_or_blocked")
        require(industry.get("classification_version") == CLASSIFICATION
                and industry.get("change_unit") == "percent", "industry_classification_or_unit_invalid")
        mapping = industry.get("association_contract")
        require(isinstance(mapping, dict) and mapping.get("security_identity") == identity
                and mapping.get("classification_version") == CLASSIFICATION
                and re.fullmatch(r"BK\d{4}", str(mapping.get("board_code", ""))) is not None
                and mapping["board_code"] == industry.get("board_code")
                and mapping.get("source_version") == industry_row["source_version"]
                and mapping.get("origin_source") == industry_row["origin_source"]
                and all(sha(mapping.get(k)) for k in ("request_hash", "raw_hash")), "industry_mapping_invalid")
        number(industry.get("change_pct"), "industry_strength_missing")
        breadth(industry)
        bars = selected["minute_bar"]
        require(all(timestamp(previous["interval_end"]) == timestamp(following["interval_start"])
                    for previous, following in zip(bars, bars[1:])), "minute_intervals_not_contiguous")
        expected = [times["feature_event_cutoff"] - timedelta(minutes=i) for i in range(11, -1, -1)]
        require([timestamp(r["interval_end"]) for r in bars] == expected, "minute_window_incomplete_or_conflicting")
        for row in bars:
            bar = row["payload"]
            require(bar.get("price_unit") == "CNY" and bar.get("volume_unit") == "shares" and bar.get("amount_unit") == "CNY"
                    and bar.get("aggregation_semantics") == "interval"
                    and bar.get("interval_seconds") == 60 and bar.get("is_final") is True, "minute_unit_or_semantics_invalid")
            o, h, low, c, v, a = (number(bar.get(k), "minute_value_invalid") for k in ("open", "high", "low", "close", "volume", "amount"))
            require(0 < low <= min(o, c) <= max(o, c) <= h and v >= 0 and a >= 0
                    and ((v == a == 0) or (v > 0 and low - 1e-8 <= a / v <= h + 1e-8)), "minute_ohlcva_invalid")
        amounts = [r["payload"]["amount"] for r in bars]
        require(math.isfinite(sum(amounts)) and math.isfinite(sum(r["payload"]["volume"] for r in bars)),
                "minute_aggregate_overflow")
        require(sum(amounts) >= PARAMETERS["min_window_turnover_cny"], "liquidity_insufficient")
        require(sum(r["payload"]["volume"] for r in bars[:7]) > 0
                and sum(r["payload"]["volume"] for r in bars[-10:-5]) > 0, "vwap_or_volume_baseline_zero")
        require(abs(bars[-1]["payload"]["close"] - price) <= 1e-8, "quote_minute_price_mismatch")
        stocks[code] = selected
    # No foreign identities or two different per-stock views of the same BK.
    for kind in ("quote", "industry", "stock_news", "announcement", "minute_bar"):
        require(all(security_identity(r["payload"].get("code")).split(":")[1] in FIXED_CODES
                    for r in by_type[kind]), "stock_scope_invalid")
    boards = {}
    for stock in stocks.values():
        value = stock["industry"]["payload"]
        projection = {k: v for k, v in value.items() if k not in {"code", "association_contract"}}
        key = value["board_code"]
        require(key not in boards or boards[key] == projection, "same_board_conflict")
        boards[key] = projection
    return {"market": market, "stocks": stocks}


def validate_research_input(snapshot: dict, *, test_only: bool = False) -> dict:
    """Fund-flow rows are discarded before validation, hashing or materialization."""
    try:
        require(isinstance(snapshot, dict), "input_not_object")
        require(snapshot.get("schema_version") == INPUT_SCHEMA and snapshot.get("strategy_id") == STRATEGY_ID,
                "strategy_input_schema_mismatch")
        require(type(test_only) is bool and snapshot.get("test_only") is test_only, "input_scope_mismatch")
        require(sha(snapshot.get("batch_id")), "batch_id_invalid")
        contract = snapshot.get("time_contract")
        expected = formal_time_contract(snapshot["trade_date"])
        require(isinstance(contract, dict), "time_contract_missing")
        keys = ("contract_version", "feature_event_cutoff", "collection_deadline", "decision_time", "execution_not_before",
                "minute_label_semantics", "is_final")
        require(all(type(contract.get(k)) is type(expected[k]) and contract.get(k) == expected[k] for k in keys),
                "formal_four_stage_contract_mismatch")
        require(contract.get("minute_label_validation_status") == "VERIFIED", "minute_label_unverified")
        require(sha(contract.get("probe_evidence_hash")), "minute_label_evidence_missing")
        times = {k: timestamp(contract[k]) for k in keys[1:5]}
        supplied = snapshot.get("records")
        require(isinstance(supplied, list), "records_missing")
        # Exact duplicates have no decision impact; conflicting duplicates fail later.
        rows = []
        for item in supplied:
            require(isinstance(item, dict), "record_not_object")
            kind = item.get("data_type")
            if kind in {"fund_flow", "fund_flow_audit", "daily_bar", "cost_proxy", "order_book", "transaction"}:
                continue
            require(kind in REQUIRED_TYPES, "unsupported_decision_record")
            rows.append(deepcopy(item))
        rows = sorted({stable_hash(row): row for row in rows}.values(), key=stable_hash)
        for row in rows:
            _validate_row(row, snapshot, times, test_only=test_only)
        state = _materialize(rows, times)
        return {"research_ready": True, "errors": [], "rows": rows, "state": state}
    except (ValueError, KeyError, TypeError, OverflowError, AttributeError) as exc:
        return {"research_ready": False, "errors": [str(exc)], "rows": [], "state": {}}
