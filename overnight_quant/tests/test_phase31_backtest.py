from pathlib import Path

from overnight_quant.backtest.backtest_engine import BacktestEngine, calculate_trade_costs
from overnight_quant.backtest.backtest_metrics import calculate_max_drawdown
from overnight_quant.backtest.historical_data import SampleHistoricalDataProvider
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


SAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "historical"


def test_selection_never_reads_next_day_fields(tmp_path):
    provider = SampleHistoricalDataProvider(SAMPLE_DIR)
    candidate = provider.candidates_asof("2026-04-01")[0]

    result = _run_engine(tmp_path)
    trade = _trade(result, "300101")

    assert "next_day_high" not in candidate
    assert trade["sell_price"] == 10.3
    assert trade["selection_as_of"] == "14:50"
    assert trade["data_fidelity"] == "sample_exact"


def test_limit_up_candidate_is_not_bought(tmp_path):
    result = _run_engine(tmp_path)

    assert not any(row["code"] == "002102" for row in result["trades"])
    assert any(row["code"] == "002102" and "limit_up_unavailable" in row["reasons"] for row in result["rejections"])


def test_stop_loss_exit(tmp_path):
    trade = _trade(_run_engine(tmp_path), "600102")

    assert trade["exit_reason"] == "stop_loss_intraday"
    assert trade["sell_price"] == 19.4
    assert trade["net_pnl"] < 0


def test_take_profit_exit(tmp_path):
    trade = _trade(_run_engine(tmp_path), "300101")

    assert trade["exit_reason"] == "take_profit_intraday"
    assert trade["sell_price"] == 10.3
    assert trade["net_pnl"] > 0


def test_both_hit_uses_conservative_default(tmp_path):
    trade = _trade(_run_engine(tmp_path), "300103")

    assert trade["exit_reason"] == "both_hit_conservative_stop_loss"
    assert trade["sell_price"] == 29.1


def test_limit_down_exit_risk_carries_position(tmp_path):
    trade = _trade(_run_engine(tmp_path), "600104")

    assert "limit_down_exit_risk" in trade["exit_reason"]
    assert trade["sell_date"] == "2026-04-13"
    assert trade["holding_days"] == 2


def test_fee_and_slippage_calculation():
    config = load_config()
    config["cost"]["slippage_pct"] = 0.1

    costs = calculate_trade_costs(10.0, 10.3, 400, config)

    assert costs["gross_pnl"] == 120.0
    assert costs["buy_commission"] == 5.0
    assert costs["sell_commission"] == 5.0
    assert costs["stamp_tax"] == 2.06
    assert costs["slippage_cost"] == 8.12
    assert costs["net_pnl"] == 99.82


def test_max_drawdown_calculation():
    assert calculate_max_drawdown([100.0, 120.0, 90.0, 110.0]) == 25.0


def test_market_fail_creates_empty_day(tmp_path):
    result = _run_engine(tmp_path)

    assert any(row["trade_date"] == "2026-04-02" and row["reason"] == "market_gate_fail" for row in result["skipped_days"])


def test_unavailable_theme_and_capital_are_disclosed(tmp_path):
    result = _run_engine(tmp_path)

    assert "theme_tags" in result["data_quality"]["unavailable_fields"]
    assert "main_net" in result["data_quality"]["unavailable_fields"]


def test_sample_backtest_is_deterministic(tmp_path):
    config = _config(tmp_path)

    first = run_backtest(config=config, run_id="first")
    second = run_backtest(config=config, run_id="second")

    assert first["trades"] == second["trades"]
    assert Path(first["output_dir"], "trades.csv").read_text(encoding="utf-8") == Path(second["output_dir"], "trades.csv").read_text(encoding="utf-8")


def test_backtest_writes_only_backtest_outputs(tmp_path):
    config = _config(tmp_path)

    result = run_backtest(config=config, run_id="isolation")

    output_dir = Path(result["output_dir"])
    assert output_dir.parent == tmp_path / "backtest_outputs"
    assert {
        "trades.csv",
        "equity_curve.csv",
        "monthly_returns.csv",
        "yearly_returns.csv",
        "skipped_days.csv",
        "data_quality.md",
        "backtest_summary.md",
    } <= {path.name for path in output_dir.iterdir()}
    quality_text = (output_dir / "data_quality.md").read_text(encoding="utf-8")
    assert "cannot prove strategy profitability" in quality_text
    assert "selection_as_of: 14:50" in quality_text
    assert "benchmark: CSI 300" in quality_text
    assert "historical_true_fields: trade_date, code, open" in quality_text
    assert "simulated_or_fixture_fields: tail_pullback_pct, range_position" in quality_text
    assert not (tmp_path / "records").exists()
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "examples").exists()


def test_unimplemented_phase31_modes_are_rejected(tmp_path):
    config = _config(tmp_path)

    for dataset, fidelity in (("local", "sample_exact"), ("sample", "daily_proxy"), ("sample", "strict_historical")):
        result = run_backtest(dataset=dataset, fidelity=fidelity, config=config)
        assert result["error"] == "NOT_IMPLEMENTED_IN_PHASE_3_1"


def test_no_automatic_trading_code_exists_phase31():
    root = Path(__file__).resolve().parents[1]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*.py")
        if "tests" not in path.parts
    )
    forbidden = ["pyautogui", "selenium", "broker api", "auto" + "_order", "place" + "_order"]
    assert not any(token in text.lower() for token in forbidden)


def _run_engine(tmp_path):
    return BacktestEngine(SampleHistoricalDataProvider(SAMPLE_DIR), _config(tmp_path)).run()


def _config(tmp_path):
    config = load_config()
    config.setdefault("backtest", {})
    config["backtest"]["initial_capital"] = 100000
    config["backtest"]["output_dir"] = str(tmp_path / "backtest_outputs")
    config["backtest"]["sample_data_dir"] = str(SAMPLE_DIR)
    config["backtest"]["intraday_assumption"] = "conservative"
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    config["paths"]["examples_dir"] = str(tmp_path / "examples")
    return config


def _trade(result, code):
    return next(row for row in result["trades"] if row["code"] == code)
