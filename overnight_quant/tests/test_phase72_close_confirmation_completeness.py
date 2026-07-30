from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta

from overnight_quant.backtest.point_in_time_provider import PointInTimeProvider
from overnight_quant.data.close_confirmation_readiness import (
    validate_close_confirmation_readiness,
)
from overnight_quant.data.point_in_time import build_point_in_time_record
from overnight_quant.data.snapshot_store import CloseWindowCollector, ImmutableSnapshotStore
from overnight_quant.scripts.run_close_confirmation import run_close_confirmation
from overnight_quant.scripts.run_preflight import run_preflight
from overnight_quant.strategy.close_confirmation_v1.features import (
    build_close_confirmation_features,
)
from overnight_quant.strategy.close_confirmation_v1.gates import evaluate_hard_gates
from overnight_quant.strategy.close_confirmation_v1.scoring import (
    score_close_confirmation,
)
from overnight_quant.strategy.close_confirmation_v1.strategy import (
    CloseConfirmationStrategy,
)
from overnight_quant.ui.dashboard import localize_table_value, status_badge


TRADE_DATE = "2026-07-30"
DECISION_TIME = f"{TRADE_DATE}T14:50:00+08:00"


def test_market_only_frozen_snapshot_is_not_data_ready(tmp_path):
    collector = CloseWindowCollector(ImmutableSnapshotStore(tmp_path), {})
    frozen = collector.freeze(
        TRADE_DATE,
        [
            _record(
                "market",
                {"index_change_pct": 0.5, "breadth_ratio": 0.6},
                "14:49",
            )
        ],
    )

    assert frozen["status"] == "FROZEN_1450"
    assert frozen["data_ready"] is False
    assert frozen["coverage_by_type"]["market"] == 1
    assert "no_scoreable_stock" in frozen["readiness_errors"]

    result = run_close_confirmation(
        mode="shadow",
        snapshot_path=frozen["path"],
        trade_date=TRADE_DATE,
        config={"paths": {"reports_dir": str(tmp_path / "reports")}},
    )
    assert result["status"] == "POINT_IN_TIME_DATA_INCOMPLETE"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    _assert_no_outputs(result)


def test_missing_market_data_cannot_pass_market_or_quality_gate():
    stock = _complete_stock()
    stock["market"] = {}

    features = build_close_confirmation_features(
        {**stock, "_news_source_ready": True},
        decision_time=DECISION_TIME,
    )
    gates = evaluate_hard_gates(
        stock,
        features,
        decision_time=DECISION_TIME,
        mode="shadow",
    )

    assert features["market_strength"] is None
    assert gates["gates"]["market"]["pass"] is False
    assert gates["gates"]["market"]["reason"] == "market_data_missing"
    assert gates["gates"]["data_quality"]["pass"] is False


def test_missing_industry_data_cannot_pass_industry_or_quality_gate():
    stock = _complete_stock()
    stock["industry"] = {}

    features = build_close_confirmation_features(
        {**stock, "_news_source_ready": True},
        decision_time=DECISION_TIME,
    )
    gates = evaluate_hard_gates(
        stock,
        features,
        decision_time=DECISION_TIME,
        mode="shadow",
    )

    assert features["industry_relative_strength"] is None
    assert gates["gates"]["industry"]["pass"] is False
    assert gates["gates"]["industry"]["reason"] == "industry_data_missing"
    assert gates["gates"]["data_quality"]["pass"] is False


def test_missing_chip_inputs_receive_no_default_chip_score():
    stock = _complete_stock()
    stock["daily_bars"] = stock["daily_bars"][:10]
    stock["fund_flow"] = []

    features = build_close_confirmation_features(
        {**stock, "_news_source_ready": True},
        decision_time=DECISION_TIME,
    )
    score = score_close_confirmation(features)

    assert features["feature_availability"]["chip"] is False
    assert features["component_inputs"]["chip_structure_proxy"] is None
    assert score["components"]["chip_structure_proxy"]["available"] is False
    assert score["components"]["chip_structure_proxy"]["points"] == 0


def test_failed_news_source_is_distinct_from_successful_zero_news():
    zero_news = _complete_snapshot()
    zero_news["source_status"] = [_source_status()]
    zero_result = CloseConfirmationStrategy({"min_shadow_score": 0}).evaluate_snapshot(
        zero_news,
        mode="shadow",
    )

    assert zero_result["data_ready"] is True
    assert zero_result["critical_source_status"]["news"]["status"] == "AVAILABLE_EMPTY"
    assert zero_result["scored"][0]["features"]["catalyst_score"] == 0

    failed_news = deepcopy(zero_news)
    failed_news["source_status"][0]["status"] = "FAILED"
    failed_result = CloseConfirmationStrategy({"min_shadow_score": 0}).evaluate_snapshot(
        failed_news,
        mode="shadow",
    )

    assert failed_result["status"] == "POINT_IN_TIME_DATA_INCOMPLETE"
    assert failed_result["data_ready"] is False
    assert "news_source_failed" in failed_result["readiness_errors"]
    _assert_no_outputs(failed_result)


def test_demo_field_forces_formal_rejection_and_data_not_ready():
    snapshot = _complete_snapshot()
    snapshot["demo_market_proxy"] = {"enabled": True}

    result = CloseConfirmationStrategy({"min_shadow_score": 0}).evaluate_snapshot(
        snapshot,
        mode="shadow",
    )

    assert result["status"] == "FORMAL_DATA_REJECTED"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    assert result["demo_field_count"] > 0
    assert result["scored"] == []
    _assert_no_outputs(result)


def test_complete_records_only_snapshot_is_scored():
    snapshot = {
        "status": "FROZEN_1450",
        "trade_date": TRADE_DATE,
        "decision_time": DECISION_TIME,
        "records": _complete_records(),
        "source_status": [_source_status()],
    }

    frozen = PointInTimeProvider([snapshot]).snapshot_at(TRADE_DATE, "14:50")
    result = CloseConfirmationStrategy({"min_shadow_score": 0}).evaluate_snapshot(
        frozen,
        mode="shadow",
    )

    assert result["data_ready"] is True
    assert result["readiness_errors"] == []
    assert result["coverage_by_type"]["market"] == 1
    assert result["coverage_by_type"]["industry"] == 1
    assert result["coverage_by_type"]["quote"] == 1
    assert result["coverage_by_type"]["minute_bar"] == 12
    assert result["coverage_by_type"]["daily_bar"] == 60
    assert result["coverage_by_type"]["fund_flow"] == 1
    assert len(result["scored"]) == 1
    assert result["status"] in {"SHADOW_SIMULATION_READY", "NO_SHADOW_CONFIRMATION"}


def test_records_after_1450_do_not_change_complete_records_only_score():
    base_snapshot = {
        "status": "FROZEN_1450",
        "trade_date": TRADE_DATE,
        "decision_time": DECISION_TIME,
        "records": _complete_records(),
        "source_status": [_source_status()],
    }
    changed_snapshot = deepcopy(base_snapshot)
    changed_snapshot["records"].append(
        _record(
            "minute_bar",
            {
                "code": "000001",
                "price": 99.0,
                "volume": 999999,
                "amount": 98999901,
            },
            "14:51",
            cutoff="15:00",
        )
    )

    base = _evaluate_records_snapshot(base_snapshot)
    changed = _evaluate_records_snapshot(changed_snapshot)

    assert base["scored"][0]["decision_hash"] == changed["scored"][0]["decision_hash"]


def test_incomplete_snapshot_never_generates_candidates_tickets_or_orders():
    snapshot = _complete_snapshot()
    snapshot["stocks"][0]["intraday_bars"] = snapshot["stocks"][0]["intraday_bars"][:-1]

    result = CloseConfirmationStrategy({"min_shadow_score": 0}).evaluate_snapshot(
        snapshot,
        mode="shadow",
    )

    assert result["status"] == "POINT_IN_TIME_DATA_INCOMPLETE"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    _assert_no_outputs(result)


def test_incomplete_status_is_yellow_in_dashboard_and_degraded_in_preflight(
    tmp_path,
    monkeypatch,
):
    assert status_badge("POINT_IN_TIME_DATA_INCOMPLETE")["tone"] == "yellow"
    assert (
        localize_table_value("status", "POINT_IN_TIME_DATA_INCOMPLETE", "zh")
        == "数据未就绪"
    )

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / f"close_confirmation_shadow_{TRADE_DATE}.md").write_text(
        "status: POINT_IN_TIME_DATA_INCOMPLETE\n"
        "execution_ok: true\n"
        "data_ready: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "overnight_quant.scripts.run_preflight._is_writable_dir",
        lambda path: True,
    )
    monkeypatch.setattr(
        "overnight_quant.scripts.run_preflight._config_readable",
        lambda: True,
    )
    result = run_preflight(
        config={
            "paths": {
                "records_dir": str(tmp_path / "records"),
                "reports_dir": str(reports),
            },
            "backtest": {"output_dir": str(tmp_path / "backtest")},
        },
        client=object(),
        now=datetime.fromisoformat(f"{TRADE_DATE}T14:55:00+08:00"),
        trade_date=TRADE_DATE,
    )

    assert result["status"] == "PROJECT_HEALTH_DEGRADED"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False


def test_readiness_validator_reports_coverage_errors_and_source_states():
    snapshot = _complete_snapshot()
    snapshot["stocks"][0]["fund_flow"] = []
    snapshot["source_status"] = [_source_status()]

    readiness = validate_close_confirmation_readiness(snapshot)

    assert set(readiness["coverage_by_type"]) == {
        "market",
        "industry",
        "quote",
        "minute_bar",
        "daily_bar",
        "fund_flow",
        "news",
    }
    assert readiness["critical_source_status"]["news"]["status"] == "AVAILABLE_EMPTY"
    assert "000001:fund_flow_missing" in readiness["readiness_errors"]


def _complete_snapshot() -> dict:
    return {
        "status": "FROZEN_1450",
        "trade_date": TRADE_DATE,
        "decision_time": DECISION_TIME,
        "stocks": [_complete_stock()],
        "source_status": [_source_status()],
    }


def _pit_meta(
    *,
    source: str,
    raw_hash: str,
    clock: str,
    event_time: str | None = None,
    cutoff: str = "14:50",
) -> dict:
    instant = f"{TRADE_DATE}T{clock}:00+08:00"
    return {
        "event_time": event_time or instant,
        "observed_at": instant,
        "available_at": instant,
        "decision_cutoff": f"{TRADE_DATE}T{cutoff}:00+08:00",
        "source": source,
        "source_version": "1",
        "raw_hash": raw_hash,
    }


def _source_status(
    *,
    status: str = "SUCCESS",
    clock: str = "14:40",
    data_type: str = "news",
) -> dict:
    return {
        "data_type": data_type,
        "status": status,
        "record_count": 0,
        **_pit_meta(
            source=f"unit.{data_type}",
            raw_hash=f"{data_type}-{status}-{clock}",
            clock=clock,
        ),
    }


def _complete_stock() -> dict:
    bars = []
    for minute in range(39, 51):
        price = 10.0 + (minute - 39) * 0.01
        bars.append(
            {
                "event_time": f"{TRADE_DATE}T14:{minute:02d}:00+08:00",
                "observed_at": f"{TRADE_DATE}T14:{minute:02d}:00+08:00",
                "available_at": f"{TRADE_DATE}T14:{minute:02d}:00+08:00",
                "decision_cutoff": DECISION_TIME,
                "source": "unit.minute_bar",
                "source_version": "1",
                "raw_hash": f"minute-{minute}",
                "price": price,
                "open": price,
                "high": price + 0.01,
                "low": price - 0.01,
                "volume": 1000 + minute,
                "amount": price * (1000 + minute),
                "bid_vol1": 500,
                "ask_vol1": 300,
            }
        )
    start = date(2026, 4, 1)
    daily_bars = [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "close": 9.0 + index * 0.02,
            "high": 9.05 + index * 0.02,
            "low": 8.95 + index * 0.02,
            "volume": 100000 + index * 1000,
            "adjustment": "qfq",
            **_pit_meta(
                source="unit.daily_bar",
                raw_hash=f"daily-{index}",
                clock="14:30",
                event_time=f"{(start + timedelta(days=index)).isoformat()}T15:00:00+08:00",
            ),
        }
        for index in range(60)
    ]
    return {
        "code": "000001",
        "name": "平安银行",
        "price": bars[-1]["price"],
        "prev_close": 9.8,
        "change_pct": 3.0,
        "amount_wan": 30000,
        "turnover_pct": 8.0,
        "suspended": False,
        "is_limit_up": False,
        "is_limit_down": False,
        "industry_name": "银行",
        **_pit_meta(source="unit.quote", raw_hash="quote", clock="14:49"),
        "market": {
            "index_change_pct": 0.5,
            "breadth_ratio": 0.62,
            **_pit_meta(source="unit.market", raw_hash="market", clock="14:49"),
        },
        "industry": {
            "name": "银行",
            "change_pct": 1.0,
            "relative_strength_pct": 0.8,
            "breadth_ratio": 0.68,
            **_pit_meta(source="unit.industry", raw_hash="industry", clock="14:49"),
        },
        "intraday_bars": bars,
        "daily_bars": daily_bars,
        "fund_flow": [
            {
                "main_net": 1000000,
                "large_net": 600000,
                **_pit_meta(
                    source="unit.fund_flow",
                    raw_hash="fund-flow",
                    clock="14:40",
                ),
            }
        ],
        "news": [],
    }


def _complete_records() -> list[dict]:
    stock = _complete_stock()
    records = [
        _record(
            "market",
            stock["market"],
            "14:49",
        ),
        _record(
            "industry",
            stock["industry"],
            "14:49",
        ),
        _record(
            "quote",
            {
                key: stock[key]
                for key in (
                    "code",
                    "name",
                    "price",
                    "prev_close",
                    "change_pct",
                    "amount_wan",
                    "turnover_pct",
                    "suspended",
                    "is_limit_up",
                    "is_limit_down",
                    "industry_name",
                )
            },
            "14:49",
        ),
    ]
    for bar in stock["intraday_bars"]:
        records.append(
            _record(
                "minute_bar",
                {
                    key: bar[key]
                    for key in (
                        "price",
                        "open",
                        "high",
                        "low",
                        "volume",
                        "amount",
                        "bid_vol1",
                        "ask_vol1",
                    )
                }
                | {"code": stock["code"]},
                bar["event_time"][11:16],
            )
        )
    for index, bar in enumerate(stock["daily_bars"]):
        records.append(
            _record(
                "daily_bar",
                {**bar, "code": stock["code"]},
                "14:30",
                event_time=f"{bar['date']} 15:00",
                observed_at=f"{TRADE_DATE} 14:30",
            )
        )
    records.append(
        _record(
            "fund_flow",
            {"code": stock["code"], "main_net": 1000000, "large_net": 600000},
            "14:40",
        )
    )
    return records


def _record(
    data_type: str,
    payload: dict,
    clock: str,
    *,
    cutoff: str = "14:50",
    event_time: str | None = None,
    observed_at: str | None = None,
) -> dict:
    event = event_time or f"{TRADE_DATE} {clock}"
    observed = observed_at or event
    return build_point_in_time_record(
        payload,
        event_time=event,
        observed_at=observed,
        available_at=f"{TRADE_DATE} {clock}",
        decision_cutoff=f"{TRADE_DATE} {cutoff}",
        source=f"unit.{data_type}",
        source_version="1",
        request={"data_type": data_type},
        raw=payload,
        data_type=data_type,
    ).as_dict()


def _evaluate_records_snapshot(snapshot: dict) -> dict:
    frozen = PointInTimeProvider([snapshot]).snapshot_at(TRADE_DATE, "14:50")
    return CloseConfirmationStrategy({"min_shadow_score": 0}).evaluate_snapshot(
        frozen,
        mode="shadow",
    )


def _assert_no_outputs(result: dict) -> None:
    assert result.get("shadow_candidates") == []
    assert result.get("selected") == []
    assert result.get("tickets") == []
    assert result.get("orders") == []
