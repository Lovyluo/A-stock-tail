"""Versioned research assumptions, not calibrated trading parameters."""
from __future__ import annotations

from datetime import date
from types import MappingProxyType

from overnight_quant.data.close_time_contract import build_close_time_contract
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import FIXED_CODES

STRATEGY_ID = "close_confirmation_noflow_v1"
REQUIREMENTS_VERSION = "noflow_requirements_v2"
INPUT_SCHEMA = "noflow_research_input_v2"
RECORD_SCHEMA = "noflow_research_record_v2"
REPORT_SCHEMA = "noflow_research_report_v2"
FEATURE_VERSION = "noflow_features_v2"
MINUTE_INTERVAL_VERSION = "noflow_minute_interval_v1"
MINUTE_INTERVAL_CONTRACT = MappingProxyType({
    "version": MINUTE_INTERVAL_VERSION,
    "fields": ("native_label", "minute_label_semantics", "interval_start", "interval_end",
               "interval_semantics_validation_status", "interval_semantics_evidence_hash",
               "finalized_at", "finalization_evidence_hash"),
    "label": "event_time=native_label=interval_start;minute_start_only",
    "admission": "interval_end<=feature_cutoff;duration=60_seconds;contiguous_nonoverlap",
    "window": "12_complete_intervals_[14:38,14:39)_through_[14:49,14:50)",
    "report_window_fields": "window_start_and_window_end_are_native_labels_not_interval_boundaries",
    "finality": "interval_end<=finalized_at<=observed_at<=available_at;is_final_not_sufficient",
    "trust": "synthetic_scope_only_or_independently_verified_source_bridge;no_self_qualification",
})
CLASSIFICATION = "eastmoney_coreconception+board_constituent_breadth_v1"
BENCHMARK = "SH:000001"
GROUPS = ("market_state", "industry_resonance", "relative_strength", "price_volume")
REQUIRED_TYPES = (
    "trading_calendar", "market", "industry", "quote", "minute_bar",
    "stock_news", "global_news", "announcement",
)
CAPABILITIES = MappingProxyType({
    "trading_calendar": "trading_calendar", "market": "market_breadth",
    "industry": "industry_snapshot", "quote": "quote", "minute_bar": "minute_bar",
    "stock_news": "stock_news", "global_news": "global_news", "announcement": "announcement",
})
PARAMETERS = MappingProxyType({
    "group_weight": 0.25, "window_minutes": 12, "tail_minutes": 5,
    "index_scale_pp": 4.0, "industry_scale_pp": 6.0, "relative_scale_pp": 10.0,
    "vwap_position_scale_pp": 3.0, "vwap_slope_scale_pp": 1.5,
    "volume_change_scale": 2.0, "tail_return_scale_pp": 4.0,
    "min_window_turnover_cny": 1000000.0,
})
FORMULAS = MappingProxyType({
    "market_state": "mean(clip(.5+index_change_pct/4),breadth_ratio)",
    "industry_resonance": "mean(clip(.5+board_change_pct/6),board_breadth_ratio)",
    "relative_strength": "mean(clip(.5+(stock-index)/10),clip(.5+(stock-board)/10))",
    "price_volume": "mean(clip(.5+position_pct/3),clip(.5+slope_pct/1.5),clip(.5+volume_ratio_change/2),clip(.5+tail_return_pct/4))",
    "score": "100*.25*sum(four_groups);no_selection_threshold",
})


def parameter_hash() -> str:
    return stable_hash(dict(PARAMETERS))


def feature_hash() -> str:
    return stable_hash({"version": FEATURE_VERSION, "formulas": dict(FORMULAS), "parameters": parameter_hash(),
                        "minute_intervals": dict(MINUTE_INTERVAL_CONTRACT)})


def requirement_hash() -> str:
    return stable_hash({
        "strategy": STRATEGY_ID, "version": REQUIREMENTS_VERSION,
        "input": INPUT_SCHEMA, "record": RECORD_SCHEMA, "report": REPORT_SCHEMA,
        "required": REQUIRED_TYPES, "capabilities": dict(CAPABILITIES), "groups": GROUPS,
        "fixed_codes": FIXED_CODES, "benchmark": BENCHMARK, "classification": CLASSIFICATION,
        "time": "minute_start_verified_1450_145105_145110_145200",
        "minute_intervals": dict(MINUTE_INTERVAL_CONTRACT),
        "units": "percent_points_CNY_shares_interval_minute_endpoints",
        "risk": "reviewed_CLEAR_per_stock_news_global_news_announcement_no_unknown",
        "output": "ranking_only_never_formal", "parameters": parameter_hash(), "features": feature_hash(),
        "excluded": "fund_flow_order_book_chip_proxy_daily_bars_not_decision_inputs",
    })


def formal_time_contract(trade_date: str) -> dict:
    date.fromisoformat(trade_date)
    # Shape/timeline only: this helper does not manufacture label verification.
    return build_close_time_contract(trade_date, minute_label_semantics="minute_start").as_dict()


def safety() -> dict:
    return {
        "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_ID,
        "lifecycle": "research_only", "report_schema": REPORT_SCHEMA,
        "requirements_version": REQUIREMENTS_VERSION, "requirements_hash": requirement_hash(),
        "feature_contract_version": FEATURE_VERSION, "feature_contract_hash": feature_hash(),
        "parameter_hash": parameter_hash(), "formal_signal_enabled": False,
        "strategy_integration": False, "sampling_authorized": False,
        "qualification": "0/3", "data_ready": False, "hard_gate_authorized": False,
        "network_requests_made": 0, "records": [], "candidates": [], "tickets": [], "orders": [],
    }
