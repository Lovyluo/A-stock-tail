from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from overnight_quant.backtest.event_engine import (
    ExecutionConfig,
    holding_decision,
    simulate_entry_fill,
    simulate_exit_fill,
)
from overnight_quant.backtest.point_in_time_provider import PointInTimeDataError, PointInTimeProvider
from overnight_quant.backtest.research_metrics import (
    calculate_research_metrics,
    evaluate_historical_acceptance,
    evaluate_shadow_acceptance,
)
from overnight_quant.backtest.walk_forward import SealedHoldout, build_walk_forward_windows
from overnight_quant.data.point_in_time import build_point_in_time_record, enforce_formal_no_demo
from overnight_quant.data.snapshot_store import (
    CloseWindowCollector,
    ImmutableSnapshotError,
    ImmutableSnapshotStore,
)
from overnight_quant.execution.paper_broker import PaperBroker
from overnight_quant.scripts.run_scan import run_scan
from overnight_quant.strategy.close_confirmation_v1.features import SCORE_WEIGHTS
from overnight_quant.strategy.close_confirmation_v1.strategy import CloseConfirmationStrategy
from overnight_quant.strategy.legacy_frozen_baseline import (
    FROZEN_LEGACY_SHA256,
    LegacyFrozenBaseline,
    legacy_baseline_fingerprint,
    legacy_baseline_is_intact,
)
from overnight_quant.strategy.registry import ensure_default_strategies_registered, get_strategy_registration


def test_1451_data_cannot_change_1450_decision():
    snapshot = _snapshot()
    base = _evaluate(snapshot, "shadow")
    changed = deepcopy(snapshot)
    changed["stocks"][0]["intraday_bars"].append(_bar("14:51", 10.8, 99999))

    later = _evaluate(changed, "shadow")

    assert base["scored"][0]["decision_hash"] == later["scored"][0]["decision_hash"]


def test_daily_closing_field_cannot_change_1450_result():
    snapshot = _snapshot()
    base = _evaluate(snapshot, "shadow")
    snapshot["stocks"][0]["close"] = 999.0
    snapshot["stocks"][0]["closing_price"] = 999.0

    changed = _evaluate(snapshot, "shadow")

    assert base["scored"][0]["decision_hash"] == changed["scored"][0]["decision_hash"]


def test_live_and_replay_scoring_are_identical_for_same_frozen_snapshot():
    snapshot = _snapshot()
    live = _evaluate(snapshot, "live")
    replay = _evaluate(snapshot, "replay")

    assert live["demo_field_count"] == replay["demo_field_count"] == 0
    assert live["scored"] == replay["scored"]


def test_formal_modes_reject_any_demo_field_and_clear_candidate_outputs():
    guarded = enforce_formal_no_demo(
        {
            "status": "DATA_FALLBACK_DEMO",
            "shadow_candidates": [{"code": "000001"}],
            "selected": [{"code": "000001"}],
            "paper_intents": [{"code": "000001"}],
            "tickets": ["x"],
        },
        "shadow",
    )

    assert guarded["status"] == "FORMAL_DATA_REJECTED"
    assert guarded["demo_field_count"] > 0
    assert guarded["shadow_candidates"] == []
    assert guarded["selected"] == []
    assert guarded["paper_intents"] == []
    assert guarded["tickets"] == []


def test_missing_minute_data_never_falls_back_to_daily_data():
    snapshot = _snapshot()
    snapshot["stocks"][0]["intraday_bars"] = []
    provider = PointInTimeProvider([snapshot])

    with pytest.raises(PointInTimeDataError, match="MINUTE_DATA_REQUIRED"):
        provider.snapshot_at("2026-07-30", "14:50")


def test_news_without_publication_time_is_not_scored():
    snapshot = _snapshot()
    snapshot["stocks"][0]["news"].append(
        {
            "data_type": "news",
            "event_time": "2026-07-30T14:20:00+08:00",
            "published_at": "",
            "observed_at": "2026-07-30T14:21:00+08:00",
            "available_at": "2026-07-30T14:21:00+08:00",
            "decision_cutoff": "2026-07-30T14:50:00+08:00",
            "source": "source",
            "source_version": "1",
            "request_hash": "r",
            "raw_hash": "h",
            "title": "positive",
        }
    )

    result = _evaluate(snapshot, "shadow")

    assert result["scored"][0]["features"]["eligible_news_count"] == 1


def test_temporal_contract_rejects_1451_event_even_if_available_time_is_earlier():
    record = build_point_in_time_record(
        {"price": 10.0},
        event_time="2026-07-30 14:51",
        observed_at="2026-07-30 14:49",
        available_at="2026-07-30 14:49",
        decision_cutoff="2026-07-30 14:50",
        source="test",
        source_version="1",
    )
    from overnight_quant.data.point_in_time import records_available_at

    accepted, rejected = records_available_at([record], "2026-07-30 14:50")

    assert accepted == []
    assert rejected[0]["pit_reject_reason"] == "event_after_decision"


def test_immutable_snapshot_cannot_be_overwritten(tmp_path):
    store = ImmutableSnapshotStore(tmp_path)
    store.write_once("frozen_1450", "2026-07-30", {"value": 1})

    with pytest.raises(ImmutableSnapshotError, match="immutable_snapshot_conflict"):
        store.write_once("frozen_1450", "2026-07-30", {"value": 2})


def test_1440_to_1450_collector_persists_input_and_freeze_excludes_late_records(tmp_path):
    on_time = build_point_in_time_record(
        {"code": "000001", "price": 10.0},
        event_time="2026-07-30 14:45",
        observed_at="2026-07-30 14:45",
        available_at="2026-07-30 14:45",
        decision_cutoff="2026-07-30 14:50",
        source="test",
        source_version="1",
    ).as_dict()
    late = build_point_in_time_record(
        {"code": "000001", "price": 10.1},
        event_time="2026-07-30 14:51",
        observed_at="2026-07-30 14:51",
        available_at="2026-07-30 14:51",
        decision_cutoff="2026-07-30 15:00",
        source="test",
        source_version="1",
    ).as_dict()
    collector = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path),
        {"test": lambda observed_at: [on_time, late]},
    )

    collected = collector.collect(datetime.fromisoformat("2026-07-30T14:45:00+08:00"))
    frozen = collector.freeze("2026-07-30", collected["records"])

    assert len(collected["records"]) == 2
    assert frozen["record_count"] == 1
    assert frozen["rejected_count"] == 1
    assert frozen["rejected_records"][0]["pit_reject_reason"] == "event_after_decision"


def test_scoring_weights_match_initial_contract():
    assert SCORE_WEIGHTS == {
        "market_confirmation": 15,
        "industry_confirmation": 20,
        "stock_relative_strength": 15,
        "price_volume_confirmation": 25,
        "catalyst_quality": 10,
        "chip_structure_proxy": 15,
    }
    assert sum(SCORE_WEIGHTS.values()) == 100


def test_event_entry_starts_at_1451_partially_fills_and_cancels_at_1456():
    signal = {"code": "000001", "trade_date": "2026-07-30", "quantity": 1000, "prev_close": 10.0}
    bars = [
        _execution_bar("14:50", 10.00, 5000),
        _execution_bar("14:51", 10.10, 2000),
        _execution_bar("14:55", 10.20, 3000),
        _execution_bar("14:56", 1.00, 999999),
    ]

    result = simulate_entry_fill(
        signal,
        bars,
        ExecutionConfig(participation_rate=0.10, slippage_bps=0, impact_bps_at_full_participation=0),
    )

    assert result["status"] == "PARTIAL"
    assert result["filled_quantity"] == 500
    assert [row["event_time"][11:16] for row in result["fills"]] == ["14:51", "14:55"]
    assert all(row["price"] != 1.0 for row in result["fills"])


def test_exit_obeys_t_plus_one_and_records_limit_down_block():
    position = {"code": "000001", "entry_date": "2026-07-30", "quantity": 500, "prev_close": 10.0}
    same_day = simulate_exit_fill(position, [], trade_date="2026-07-30")
    blocked = simulate_exit_fill(
        position,
        [{**_execution_bar("09:31", 9.0, 10000, day="2026-07-31"), "bid_volume": 0}],
        trade_date="2026-07-31",
    )

    assert same_day["status"] == "T_PLUS_ONE_BLOCKED"
    assert blocked["status"] == "LIMIT_DOWN_BLOCKED"
    assert blocked["blocked_days"] == 1


def test_holding_rules_cover_d1_risk_invalidation_and_d5_time_exit():
    assert holding_decision({}, {"risk_exit": True}, holding_day=1)["reason"] == "d1_risk_exit"
    assert holding_decision({}, {"industry_valid": False}, holding_day=3)["action"] == "EXIT"
    assert holding_decision({}, {}, holding_day=5)["reason"] == "d5_time_exit"
    assert holding_decision({}, {}, holding_day=2)["action"] == "HOLD"


def test_paper_account_enforces_position_limits_and_t_plus_one():
    account = PaperBroker()
    intent = account.entry_intent("000001", 10.0, 9.5)
    assert intent["status"] == "READY"
    assert intent["quantity"] <= 1000
    account.apply_entry_fill("000001", intent["quantity"], 10.0, trade_date="2026-07-30", stop_price=9.5)

    assert account.exit_intent("000001", trade_date="2026-07-30", reason="risk")["status"] == "T_PLUS_ONE_BLOCKED"
    assert account.snapshot({"000001": 10.0})["exposure"] <= 10000.0


def test_walk_forward_uses_required_windows_and_sealed_holdout_opens_once():
    start = date(2018, 1, 1)
    dates = [start + timedelta(days=index) for index in range(365 * 8)]
    result = build_walk_forward_windows(dates)
    assert result["windows"]
    first = result["windows"][0]
    assert len(first.purge_dates) == 5
    assert len(first.embargo_dates) == 5
    sealed = SealedHoldout(result["sealed_dates"])
    assert sealed.open_once()
    with pytest.raises(RuntimeError, match="ALREADY_OPENED"):
        sealed.open_once()


def test_research_metrics_include_acceptance_and_shadow_thresholds():
    trades = [
        {
            "net_pnl": 100,
            "return_pct": 1,
            "filled_quantity": 100,
            "requested_quantity": 100,
            "entry_value": 1000,
            "exit_value": 1100,
            "slippage_bps": 5,
        },
        {
            "net_pnl": -50,
            "return_pct": -0.5,
            "filled_quantity": 100,
            "requested_quantity": 100,
            "entry_value": 1000,
            "exit_value": 950,
            "blocked_days": 1,
        },
    ]
    metrics = calculate_research_metrics(trades, [100000, 100100, 100050])
    acceptance = evaluate_historical_acceptance(metrics, history_years=1, oos_windows=1)

    assert "deflated_sharpe_probability" in metrics
    assert "probability_of_backtest_overfit" in metrics
    assert "factor_ablation" in metrics
    assert acceptance["passed"] is False
    assert evaluate_shadow_acceptance(trading_days=60, filled_trades=49)["required_trading_days"] == 90
    assert evaluate_shadow_acceptance(trading_days=60, filled_trades=50)["passed"] is True


def test_legacy_baseline_is_registered_frozen_and_reproducible():
    ensure_default_strategies_registered()
    registration = get_strategy_registration("legacy_frozen_baseline")

    assert registration.lifecycle == "frozen"
    assert registration.formal_signal_enabled is False
    assert registration.ticket_enabled is False
    assert legacy_baseline_fingerprint() == FROZEN_LEGACY_SHA256
    assert legacy_baseline_is_intact() is True


def test_old_live_entry_is_disabled_without_outputs():
    result = run_scan(mode="live", trade_date="2026-07-30", config={})

    assert result["status"] == "LEGACY_FORMAL_ENTRY_DISABLED"
    assert result["selected"] == []
    assert result["tickets"] == []
    assert result["demo_field_count"] == 0


def test_frozen_wrapper_also_blocks_direct_live_use():
    client = type("LiveClient", (), {"mode": "live"})()

    result = LegacyFrozenBaseline(client, {}).scan("2026-07-30")

    assert result["status"] == "LEGACY_FORMAL_ENTRY_DISABLED"
    assert result["selected"] == []
    assert result["tickets"] == []


def test_new_python_sources_do_not_contain_execution_integration_tokens():
    root = Path(__file__).resolve().parents[1]
    source_files = [
        root / "data" / "point_in_time.py",
        root / "data" / "snapshot_store.py",
        root / "backtest" / "point_in_time_provider.py",
        root / "backtest" / "event_engine.py",
        root / "execution" / "paper_broker.py",
        root / "strategy" / "close_confirmation_v1" / "strategy.py",
    ]
    tokens = ["pyautogui", "selenium", "broker" + " api", "auto_" + "order", "place_" + "order"]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in source_files)
    assert not any(token in combined for token in tokens)


def _evaluate(snapshot: dict, mode: str) -> dict:
    provider = PointInTimeProvider([snapshot])
    frozen = provider.snapshot_at("2026-07-30", "14:50")
    return CloseConfirmationStrategy({"min_shadow_score": 0}).evaluate_snapshot(frozen, mode=mode)


def _snapshot() -> dict:
    bars = [_bar(f"14:{minute:02d}", 10.0 + (minute - 30) * 0.02, 1000 + minute * 10) for minute in range(30, 51)]
    daily = [
        {"date": f"2026-06-{index + 1:02d}", "close": 9.0 + index * 0.02, "volume": 100000 + index * 1000}
        for index in range(20)
    ]
    news = [
        {
            "data_type": "news",
            "event_time": "2026-07-30T14:20:00+08:00",
            "published_at": "2026-07-30T14:20:00+08:00",
            "observed_at": "2026-07-30T14:21:00+08:00",
            "available_at": "2026-07-30T14:21:00+08:00",
            "decision_cutoff": "2026-07-30T14:50:00+08:00",
            "source": "source",
            "source_version": "1",
            "request_hash": "r",
            "raw_hash": "h",
            "title": "业务增长",
            "kind": "news",
        }
    ]
    return {
        "status": "FROZEN_1450",
        "trade_date": "2026-07-30",
        "decision_time": "2026-07-30T14:50:00+08:00",
        "stocks": [
            {
                "code": "000001",
                "name": "样本",
                "prev_close": 9.8,
                "change_pct": 4.0,
                "amount_wan": 30000,
                "turnover_pct": 8.0,
                "market": {"index_change_pct": 0.8, "breadth_ratio": 0.65},
                "industry": {"change_pct": 1.5, "relative_strength_pct": 1.0, "breadth_ratio": 0.7},
                "intraday_bars": bars,
                "daily_bars": daily,
                "fund_flow": [{"main_net": 1000}],
                "news": news,
            }
        ],
        "market_records": [],
        "industry_records": [],
        "news": [],
    }


def _bar(clock: str, price: float, volume: float) -> dict:
    return {
        "event_time": f"2026-07-30T{clock}:00+08:00",
        "observed_at": f"2026-07-30T{clock}:00+08:00",
        "available_at": f"2026-07-30T{clock}:00+08:00",
        "price": price,
        "open": price,
        "high": price + 0.02,
        "low": price - 0.02,
        "volume": volume,
        "amount": price * volume,
        "bid_vol1": 500,
        "ask_vol1": 300,
    }


def _execution_bar(clock: str, price: float, volume: float, *, day: str = "2026-07-30") -> dict:
    return {
        "event_time": f"{day}T{clock}:00+08:00",
        "open": price,
        "volume": volume,
        "amount": price * volume,
    }
