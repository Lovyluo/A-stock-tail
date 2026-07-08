from pathlib import Path

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.execution.state_manager import config_for_mode, config_for_state, reset_real_state
from overnight_quant.execution.trade_recorder import write_manual_orders
from overnight_quant.scripts.run_record_order import record_order_for_state
from overnight_quant.scripts.run_scan import run_scan
from overnight_quant.scripts.run_sell_plan import run_sell_plan
from overnight_quant.scripts.run_trade_review import generate_trade_review
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def test_demo_scan_writes_examples_and_not_real(tmp_path):
    config = _tmp_config(tmp_path)

    result = run_scan(mode="demo", trade_date="2026-05-26", config=config)

    assert Path(result["signals_csv"]).parent == tmp_path / "examples" / "records"
    assert Path(result["tickets"][0]).parent == tmp_path / "examples" / "reports"
    assert not (tmp_path / "real" / "records" / "signals.csv").exists()
    assert not (tmp_path / "real" / "reports").exists()


def test_live_scan_writes_real_and_not_examples(tmp_path):
    config = _tmp_config(tmp_path)
    client = AStockClient("demo")

    result = run_scan(mode="live", trade_date="2026-05-26", config=config, client=client)

    assert Path(result["signals_csv"]).parent == tmp_path / "real" / "records"
    assert Path(result["tickets"][0]).parent == tmp_path / "real" / "reports"
    assert not (tmp_path / "examples").exists()


def test_live_sell_plan_does_not_read_examples_positions(tmp_path):
    config = _tmp_config(tmp_path)
    example_config = config_for_state(config, "example")
    write_manual_orders([_manual_buy()], example_config["paths"]["records_dir"])

    result = run_sell_plan(mode="live", trade_date="2026-05-27", config=config, client=_price_client())

    assert result["status"] == "NO_OPEN_POSITION"
    assert Path(result["path"]).parent == tmp_path / "real" / "reports"


def test_demo_sell_plan_writes_examples_and_not_real(tmp_path):
    config = _tmp_config(tmp_path)

    result = run_sell_plan(mode="demo", trade_date="2026-05-27", config=config, client=_price_client())

    assert result["status"] == "SELL_PLAN_READY"
    assert Path(result["path"]).parent == tmp_path / "examples" / "reports"
    assert not (tmp_path / "real" / "reports").exists()


def test_record_order_defaults_to_real_state(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path / "real" / "reports")

    result = record_order_for_state(
        code="300001",
        price=18.5,
        qty=200,
        side="BUY",
        trade_time="2026-05-23 14:52:00",
        config=config,
    )

    assert result["allow"] is True
    assert Path(result["orders_csv"]).parent == tmp_path / "real" / "records"
    assert not (tmp_path / "examples" / "records" / "manual_orders.csv").exists()


def test_record_order_example_state_writes_examples(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path / "examples" / "reports")

    result = record_order_for_state(
        code="300001",
        price=18.5,
        qty=200,
        side="BUY",
        trade_time="2026-05-23 14:52:00",
        state="example",
        config=config,
    )

    assert result["allow"] is True
    assert Path(result["orders_csv"]).parent == tmp_path / "examples" / "records"
    assert not (tmp_path / "real" / "records" / "manual_orders.csv").exists()


def test_trade_review_defaults_to_real_state(tmp_path):
    config = _tmp_config(tmp_path)
    real = config_for_state(config, "real")
    write_manual_orders([_manual_buy(), _manual_sell()], real["paths"]["records_dir"])

    result = generate_trade_review("300001", trade_date="2026-05-24", config=config)

    assert Path(result["path"]).parent == tmp_path / "real" / "reports"
    assert not (tmp_path / "examples" / "reports").exists()


def test_trade_review_empty_real_state_returns_clear_error(tmp_path):
    config = _tmp_config(tmp_path)

    result = generate_trade_review("300001", config=config)

    assert result == {"error": "NO_TRADE_RECORD", "code": "300001", "state": "real"}


def test_example_trade_review_ignores_sell_plans_after_trade_date(tmp_path):
    config = _tmp_config(tmp_path)
    example = config_for_state(config, "example")
    _write_ticket(Path(example["paths"]["reports_dir"]))
    write_manual_orders([_manual_buy(), _manual_sell()], example["paths"]["records_dir"])
    _write_generated(Path(example["paths"]["reports_dir"]) / "sell_plan_2026-05-23.md").write_text(
        "Action: WAIT_10_MIN\nForce Exit Before: 10:30\n", encoding="utf-8"
    )
    _write_generated(Path(example["paths"]["reports_dir"]) / "sell_plan_2026-05-26.md").write_text(
        "NO_OPEN_POSITION\n", encoding="utf-8"
    )

    result = generate_trade_review("300001", trade_date="2026-05-24", state="example", config=config)
    text = Path(result["path"]).read_text(encoding="utf-8")

    assert "planned_action: WAIT_10_MIN" in text


def test_missing_manual_orders_returns_empty_and_live_no_open_position(tmp_path):
    config = _tmp_config(tmp_path)

    result = run_sell_plan(mode="live", trade_date="2026-05-27", config=config, client=_price_client())

    assert result["status"] == "NO_OPEN_POSITION"
    assert "NO_OPEN_POSITION" in Path(result["path"]).read_text(encoding="utf-8")


def test_reset_dry_run_lists_real_files_without_deleting_examples(tmp_path):
    config = _tmp_config(tmp_path)
    real_report = _write_generated(tmp_path / "real" / "reports" / "sell_plan_2026-05-27.md")
    real_record = _write_generated(tmp_path / "real" / "records" / "signals.csv")
    example_report = _write_generated(tmp_path / "examples" / "reports" / "sell_plan_example.md")

    result = reset_real_state(config, dry_run=True)

    assert set(result["targets"]) == {str(real_report), str(real_record)}
    assert real_report.exists()
    assert real_record.exists()
    assert example_report.exists()


def test_reset_yes_deletes_only_real_generated_files(tmp_path):
    config = _tmp_config(tmp_path)
    real_report = _write_generated(tmp_path / "real" / "reports" / "trade_review_2026-05-27_300001.md")
    source_file = _write_generated(tmp_path / "real" / "reports" / "scan_reports.py")
    example_report = _write_generated(tmp_path / "examples" / "reports" / "trade_review_example.md")

    result = reset_real_state(config, confirmed=True)

    assert result["status"] == "RESET_COMPLETE"
    assert not real_report.exists()
    assert source_file.exists()
    assert example_report.exists()


def test_reset_without_confirmation_preserves_real_outputs(tmp_path):
    config = _tmp_config(tmp_path)
    real_report = _write_generated(tmp_path / "real" / "reports" / "sell_plan_2026-05-27.md")

    result = reset_real_state(config)

    assert result["status"] == "CONFIRMATION_REQUIRED"
    assert real_report.exists()


def _tmp_config(tmp_path):
    config = load_config()
    config["paths"] = {
        "records_dir": str(tmp_path / "real" / "records"),
        "reports_dir": str(tmp_path / "real" / "reports"),
        "examples_dir": str(tmp_path / "examples"),
    }
    return config


def _write_ticket(reports: Path):
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "manual_order_ticket_2026-05-23_300001.md").write_text(
        "\n".join(
            [
                "Strategy: yang_yongxing_overnight_v1",
                "Date: 2026-05-23",
                "Generated At: 2026-05-23 14:45:00",
                "Code: 300001",
                "Name: Demo Robotics",
                "Suggested Price: 18.5",
                "Max Acceptable Price: 18.78",
                "Suggested Quantity: 200",
                "Stop Loss: 17.95",
            ]
        ),
        encoding="utf-8",
    )


def _manual_buy():
    return {
        "order_id": "BUY1",
        "ticket_id": "ticket",
        "strategy_name": "yang_yongxing_overnight_v1",
        "trade_date": "2026-05-23",
        "trade_time": "2026-05-23 14:52:00",
        "code": "300001",
        "name": "Demo Robotics",
        "side": "BUY",
        "price": 18.5,
        "qty": 200,
        "amount": 3700,
        "stop_loss_price": 17.95,
    }


def _manual_sell():
    return {
        **_manual_buy(),
        "order_id": "SELL1",
        "trade_date": "2026-05-24",
        "trade_time": "2026-05-24 09:45:00",
        "side": "SELL",
        "price": 19.02,
        "amount": 3804,
    }


def _write_generated(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("sample\n", encoding="utf-8")
    return path


class _price_client:
    def get_current_price(self, code):
        return {"code": code, "name": "Demo Robotics", "price": 19.02, "open_change_pct": 2}
