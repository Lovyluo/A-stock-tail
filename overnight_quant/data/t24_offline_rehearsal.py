"""Synthetic Provider -> Adapter -> PIT -> readiness -> features rehearsal only."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from overnight_quant.data.close_confirmation_readiness import validate_close_confirmation_readiness
from overnight_quant.data.close_time_contract import CloseTimeContract
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.s2_integration_contract import (
    ASSOCIATION_SCHEMA, CLASSIFICATION, CLASSIFICATION_VERSION, FIXED_CODES,
    FUND_BLOCKER, INDUSTRY_BLOCKER, RECORD_SCHEMA, phase_budget,
    security_identity, validate_fund_window, validate_record,
)
from overnight_quant.data.snapshot_store import close_snapshot_hash
from overnight_quant.data.source_capability_adapters import (
    SourceAdapterBinding, SourceProviderEnvelope,
    _execute_source_adapter_with_bindings_for_test,
    get_source_adapter_registry,
)
from overnight_quant.data.source_capability_registry import QUALIFIED_MOOTDX_RECORD_SHA256
from overnight_quant.strategy.close_confirmation_v1.features import build_close_confirmation_features


FIXTURE_DATE = "2026-09-18"
FIXTURE_TIMES = {
    "feature_cutoff": f"{FIXTURE_DATE}T14:50:00+08:00",
    "collection_deadline": f"{FIXTURE_DATE}T14:51:05+08:00",
    "decision_time": f"{FIXTURE_DATE}T14:51:10+08:00",
    "execution_start": f"{FIXTURE_DATE}T14:52:00+08:00",
}
FIXTURE_BOARDS = {
    "000001": ("BK0475", "bank"), "000333": ("BK1239", "white_goods"),
    "600000": ("BK0475", "bank"), "600519": ("BK1277", "liquor"),
    "601318": ("BK0474", "insurance"),
}


def _binding(capability: str) -> dict:
    origin = "eastmoney" if capability in {"market_breadth", "industry_snapshot", "fund_flow"} else "tongdaxin" if capability == "minute_bar" else "tencent"
    return next(row for row in get_source_adapter_registry() if row["capability"] == capability and row["origin_source"] == origin and row["adapter"] != "akshare")


def synthetic_record(capability: str, data_type: str, payload: dict, *, event_time: str | None = None) -> dict:
    identity = _binding(capability)
    return {
        "record_schema_version": RECORD_SCHEMA, "test_only": True,
        "capability": capability, "data_type": data_type,
        "origin_source": identity["origin_source"], "adapter": identity["adapter"],
        "source": f"synthetic_test_only.{identity['origin_source']}",
        "source_version": identity["source_version"], "published_at": None,
        "event_time": event_time or FIXTURE_TIMES["feature_cutoff"],
        "observed_at": f"{FIXTURE_DATE}T14:50:00+08:00",
        "available_at": f"{FIXTURE_DATE}T14:50:01+08:00",
        "decision_cutoff": FIXTURE_TIMES["decision_time"],
        "temporal_contract": deepcopy(FIXTURE_TIMES),
        "request_hash": stable_hash({"synthetic_request": capability, "code": payload.get("code")}),
        "raw_hash": stable_hash({"synthetic_response": payload}), "payload": deepcopy(payload),
    }


def synthetic_fixture() -> list[dict]:
    """Dates/values are explicitly fabricated test inputs, never calendar evidence."""
    current = date.fromisoformat(FIXTURE_DATE) - timedelta(days=1)
    days = []
    while len(days) < 60:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current -= timedelta(days=1)
    days.sort()
    rows = [
        synthetic_record("trading_calendar", "trading_calendar", {
            "calendar_kind": "benchmark_index_trade_dates", "calendar_name": "synthetic_test_only",
            "trade_dates": days, "latest_completed_trade_date": days[-1],
        }),
        synthetic_record("market_breadth", "market", {"index_change_pct": 0.5, "breadth_ratio": 0.6}),
    ]
    for code in FIXED_CODES:
        board, name = FIXTURE_BOARDS[code]
        industry = synthetic_record("industry_snapshot", "industry", {
            "code": code, "name": name, "board_code": board,
            "classification_version": CLASSIFICATION_VERSION,
            "change_pct": 1.2, "field_units": {"change_pct": "percent"},
            "breadth_ratio": 0.6, "invalid_count": 2 if board == "BK1239" else 0,
        })
        industry["payload"]["association_contract"] = {
            "schema_version": ASSOCIATION_SCHEMA, "security_identity": security_identity(code),
            "board_code": board, "board_name": name, "classification_system": CLASSIFICATION,
            "classification_version": CLASSIFICATION_VERSION, "origin_source": "eastmoney",
            "source_version": industry["source_version"],
            "request_hash": stable_hash({"synthetic_mapping_request": code}),
            "raw_hash": stable_hash({"synthetic_mapping_response": code, "board": board}),
        }
        rows.append(industry)
        rows.append(synthetic_record("quote", "quote", {
            "code": code, "name": "synthetic", "price": 10.11, "prev_close": 9.8,
            "change_pct": 3.0, "amount_wan": 30000, "turnover_pct": 8.0,
            "suspended": False, "is_limit_up": False, "is_limit_down": False,
        }))
        for minute in range(39, 51):
            rows.append(synthetic_record("minute_bar", "minute_bar", {
                "code": code, "price": 10.0, "open": 10.0, "close": 10.0,
                "high": 10.1, "low": 9.9, "volume": 1000, "amount": 10000,
                "bid_vol1": 500, "ask_vol1": 300, "is_final": True,
                "endpoint_id": "mootdx_locked@59.36.5.11:7709",
                "endpoint": {"host": "59.36.5.11", "port": 7709},
                "qualification_record_sha256": QUALIFIED_MOOTDX_RECORD_SHA256,
            }, event_time=f"{FIXTURE_DATE}T14:{minute:02d}:00+08:00"))
        for day in days:
            rows.append(synthetic_record("daily_bar_qfq", "daily_bar", {
                "code": code, "date": day, "open": 9.5, "close": 10.0,
                "high": 10.1, "low": 9.0, "volume": 100000, "adjustment": "qfq",
            }, event_time=f"{day}T15:00:00+08:00"))
        for minute in range(46, 51):
            rows.append(synthetic_record("fund_flow", "fund_flow", {
                "code": code, "main_net": 100, "large_net": 60, "super_net": 40,
                "amount_unit": "CNY", "aggregation_semantics": "incremental",
                "semantics_evidence_scope": "synthetic_test_only", "is_proxy": False,
                "eligible_for_hard_gate": True,
            }, event_time=f"{FIXTURE_DATE}T14:{minute:02d}:00+08:00"))
    return rows


def run_offline_rehearsal(records: list[dict]) -> dict:
    rows = deepcopy(records)
    output = {
        "test_only": True, "network_requests_made": 0,
        "data_ready": False, "hard_gate_authorized": False,
        "qualification": "0/3", "candidates": [], "tickets": [], "orders": [],
        "strategy_integration": False, "automatic_configuration_change": False,
        "live_candidate_blockers": [INDUSTRY_BLOCKER, FUND_BLOCKER],
    }
    errors = []
    for row in rows:
        if row.get("test_only") is not True:
            errors.append("synthetic_scope_required")
        errors.extend(validate_record(row, expected_times=FIXTURE_TIMES))
    for code in FIXED_CODES:
        window = [row for row in rows if row.get("data_type") == "fund_flow" and (row.get("payload") or {}).get("code") == code]
        errors.extend(validate_fund_window(window, FIXTURE_TIMES["feature_cutoff"]))
    adapters = {}
    if not errors:
        for capability in sorted({row["capability"] for row in rows}):
            batch = [row for row in rows if row["capability"] == capability]
            identity = _binding(capability)
            provider_key = f"tests.t24.synthetic.{capability}"
            binding = SourceAdapterBinding(
                **{key: identity[key] for key in ("capability", "origin_source", "adapter", "source_version")},
                provider_key=provider_key, candidate_provider_key="", legacy_provider_key="", implementation_status="bound",
            )
            envelope = SourceProviderEnvelope(provider_key=provider_key, provider_callable=lambda batch=batch: deepcopy(batch))
            result = _execute_source_adapter_with_bindings_for_test(
                **{key: identity[key] for key in ("capability", "origin_source", "adapter", "source_version")},
                provider_envelope=envelope, entries=[binding], environ={},
            )
            adapters[capability] = {key: result.get(key) for key in (
                "status", "provider_called", "selection_registry_scope", "selection_adapter_registry_hash", "production_adapter_registry_hash",
            )}
            if result["status"] != "SOURCE_ADAPTER_BOUND":
                errors.append(f"adapter_rejected:{capability}:{result['status']}")
    if errors:
        return {**output, "status": "OFFLINE_INTEGRATION_REJECTED", "errors": sorted(set(errors)), "adapter_results": adapters}
    time_contract = CloseTimeContract(
        feature_event_cutoff=FIXTURE_TIMES["feature_cutoff"], collection_deadline=FIXTURE_TIMES["collection_deadline"],
        decision_time=FIXTURE_TIMES["decision_time"], execution_not_before=FIXTURE_TIMES["execution_start"],
        minute_label_semantics="minute_end", minute_label_validation_status="VERIFIED", probe_evidence_hash="a" * 64,
    )
    news_status = {
        "source": "synthetic_test_only.news", "source_version": "test_only_v1", "data_type": "news",
        "status": "SUCCESS", "record_count": 0, "event_time": FIXTURE_TIMES["feature_cutoff"],
        "observed_at": FIXTURE_TIMES["feature_cutoff"], "available_at": f"{FIXTURE_DATE}T14:50:01+08:00",
        "decision_cutoff": FIXTURE_TIMES["decision_time"], "raw_hash": "b" * 64,
    }
    snapshot = {"trade_date": FIXTURE_DATE, "decision_time": FIXTURE_TIMES["decision_time"], "time_contract": time_contract.as_dict(), "records": rows, "source_status": [news_status]}
    readiness = validate_close_confirmation_readiness(snapshot)
    normalized = readiness["normalized_snapshot"]
    errors = list(readiness["readiness_errors"])
    for code, status in readiness["stock_readiness"].items():
        errors.extend(f"{code}:{reason}" for reason in status["errors"])
    if readiness["rejected_record_count"]:
        errors.append("integration_records_rejected")
    if set(readiness["eligible_stock_codes"]) != set(FIXED_CODES):
        errors.append("fixed_five_stock_readiness_incomplete")
    news_ready = readiness["critical_source_status"]["news"]["status"] in {"AVAILABLE", "AVAILABLE_EMPTY"}
    features = {} if errors else {
        stock["code"]: build_close_confirmation_features(
            {**stock, "_time_contract": normalized["time_contract"], "_news_source_ready": news_ready},
            decision_time=FIXTURE_TIMES["decision_time"],
        )
        for stock in normalized["stocks"]
    }
    snapshot_hash = close_snapshot_hash(decision_time=FIXTURE_TIMES["decision_time"], records=rows, source_status=[news_status], time_contract=time_contract)
    return {
        **output, "status": "OFFLINE_CHAIN_EXECUTABLE" if not errors else "OFFLINE_INTEGRATION_REJECTED",
        "errors": sorted(set(errors)), "synthetic_readiness_passed": not errors,
        "adapter_results": adapters, "snapshot_hash": snapshot_hash, "features": features,
        "feature_hash": stable_hash(features), "stock_count": len(normalized["stocks"]),
        "stock_associations": {stock["code"]: stock.get("industry_association") for stock in normalized["stocks"]},
        "virtual_budget_serial": phase_budget(started_at="2026-09-18T14:39:30+08:00", cutoff="2026-09-18T14:40:00+08:00", deadline="2026-09-18T14:40:10+08:00"),
        "virtual_budget_existing_four_workers": phase_budget(started_at="2026-09-18T14:39:30+08:00", cutoff="2026-09-18T14:40:00+08:00", deadline="2026-09-18T14:40:10+08:00", workers=4),
    }
