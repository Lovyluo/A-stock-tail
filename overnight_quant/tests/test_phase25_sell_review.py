import os
from datetime import datetime
from pathlib import Path

from overnight_quant.execution.order_recorder import record_manual_order
from overnight_quant.execution.position_tracker import get_open_positions, get_position_summaries
from overnight_quant.execution.trade_recorder import write_manual_orders
from overnight_quant.reports.lifecycle_report import write_trade_lifecycle_report
from overnight_quant.reports.trade_review import write_trade_review
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def test_valid_sell_closes_open_position(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")

    result = record_manual_order(config, "300001", 19.02, 200, "SELL", "2026-05-24 09:45:00")
    summaries = get_position_summaries(config["paths"]["records_dir"])

    assert result["allow"] is True
    assert summaries[0]["status"] == "CLOSED"
    assert summaries[0]["open_qty"] == 0


def test_partial_sell_leaves_remaining_open_qty(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")

    record_manual_order(config, "300001", 19.02, 100, "SELL", "2026-05-24 09:45:00")
    summaries = get_position_summaries(config["paths"]["records_dir"])

    assert summaries[0]["status"] == "PARTIALLY_CLOSED"
    assert summaries[0]["open_qty"] == 100
    assert get_open_positions(config["paths"]["records_dir"])[0]["open_qty"] == 100


def test_sell_more_than_open_qty_is_rejected(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")

    result = record_manual_order(config, "300001", 19.02, 300, "SELL", "2026-05-24 09:45:00")

    assert result["allow"] is False
    assert "sell_qty_exceeds_open_position" in result["reasons"]


def test_sell_before_buy_time_is_rejected(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")

    result = record_manual_order(config, "300001", 19.02, 200, "SELL", "2026-05-23 14:00:00")

    assert result["allow"] is False
    assert "sell_time_not_after_buy_time" in result["reasons"]


def test_sell_without_open_buy_is_rejected_phase25(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)

    result = record_manual_order(config, "300001", 19.02, 200, "SELL", "2026-05-24 09:45:00")

    assert result["allow"] is False
    assert "sell_without_open_buy" in result["reasons"]


def test_sell_non_positive_price_is_rejected(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")

    result = record_manual_order(config, "300001", 0, 200, "SELL", "2026-05-24 09:45:00")

    assert result["allow"] is False
    assert "price_not_positive" in result["reasons"]


def test_historical_buy_uses_ticket_matching_trade_date(tmp_path):
    config = _tmp_config(tmp_path)
    historical = _write_ticket(tmp_path, "2026-05-23")
    _write_ticket(tmp_path, "2026-05-26")

    result = record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")

    assert result["allow"] is True
    assert result["row"]["source_ticket_path"] == str(historical)


def test_closed_trade_calculates_realized_pnl_correctly(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")
    record_manual_order(config, "300001", 19.02, 200, "SELL", "2026-05-24 09:45:00")

    summary = get_position_summaries(config["paths"]["records_dir"])[0]

    assert summary["realized_pnl"] == 104.0
    assert summary["avg_buy_price"] == 18.5


def test_multiple_buys_and_sells_calculate_average_and_unrealized_pnl(tmp_path):
    config = _tmp_config(tmp_path)
    write_manual_orders(
        [
            _order_row("B1", "BUY", 18.0, 100, "2026-05-23 14:50:00"),
            _order_row("B2", "BUY", 20.0, 100, "2026-05-23 14:52:00"),
            _order_row("S1", "SELL", 21.0, 100, "2026-05-24 09:40:00"),
        ],
        config["paths"]["records_dir"],
    )

    summary = get_position_summaries(config["paths"]["records_dir"], {"300001": 20.0})[0]

    assert summary["status"] == "PARTIALLY_CLOSED"
    assert summary["avg_buy_price"] == 19.0
    assert summary["realized_pnl"] == 200.0
    assert summary["unrealized_pnl"] == 100.0


def test_fees_and_stamp_tax_are_applied(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")
    record_manual_order(config, "300001", 19.02, 200, "SELL", "2026-05-24 09:45:00")

    result = write_trade_review(config, "300001", "2026-05-24")

    assert result["gross_pnl"] == 104.0
    assert result["fee_estimate"] == 11.9
    assert result["net_pnl"] == 92.1
    assert result["return_pct"] == 2.49


def test_partial_sell_review_is_incomplete_and_uses_realized_cost(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")
    record_manual_order(config, "300001", 19.02, 100, "SELL", "2026-05-24 09:45:00")

    result = write_trade_review(config, "300001", "2026-05-24")

    assert result["conclusion"] == "INCOMPLETE_TRADE"
    assert result["gross_pnl"] == 52.0


def test_trade_review_missing_ticket_does_not_crash(tmp_path):
    config = _tmp_config(tmp_path)
    write_manual_orders(
        [
            _order_row("B1", "BUY", 18.5, 200, "2026-05-23 14:52:00", source_ticket_path=""),
            _order_row("S1", "SELL", 19.02, 200, "2026-05-24 09:45:00", source_ticket_path=""),
        ],
        config["paths"]["records_dir"],
    )

    result = write_trade_review(config, "300001", "2026-05-24")

    assert result["conclusion"] == "INCOMPLETE_TRADE"
    assert Path(result["path"]).exists()


def test_trade_review_report_is_generated(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    _write_sell_plan(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")
    record_manual_order(config, "300001", 19.02, 200, "SELL", "2026-05-24 09:45:00")
    write_trade_lifecycle_report(config, "2026-05-24")

    result = write_trade_review(config, "300001", "2026-05-24")
    text = Path(result["path"]).read_text(encoding="utf-8")

    assert Path(result["path"]).exists()
    assert "EXECUTION_OK" in text
    assert "net_pnl: 92.1" in text
    assert "lifecycle_status: CLOSED" in text


def test_lifecycle_becomes_closed_after_full_sell(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")
    record_manual_order(config, "300001", 19.02, 200, "SELL", "2026-05-24 09:45:00")

    report = write_trade_lifecycle_report(config, "2026-05-24")
    text = Path(report).read_text(encoding="utf-8")

    assert "status: CLOSED" in text
    assert "manual SELL: YES" in text
    assert "realized pnl: 104.0" in text
    assert "return pct: 2.81" in text


def test_lifecycle_preserves_sell_plan_path_after_sell(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    _write_sell_plan(tmp_path)
    record_manual_order(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00")

    result = record_manual_order(config, "300001", 19.02, 200, "SELL", "2026-05-24 09:45:00")
    text = Path(result["lifecycle_report_path"]).read_text(encoding="utf-8")

    assert f"sell plan path: {tmp_path / 'reports' / 'sell_plan_2026-05-24.md'}" in text


def test_no_automatic_trading_code_exists_phase25():
    root = Path(__file__).resolve().parents[1]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*.py")
        if "tests" not in path.parts
    )
    forbidden = ["pyautogui", "selenium", "broker api", "auto" + "_order", "place" + "_order"]
    assert not any(token in text.lower() for token in forbidden)


def _tmp_config(tmp_path):
    config = load_config()
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    config["cost"] = {
        "commission_rate": 0.0003,
        "min_commission": 5,
        "stamp_tax_rate": 0.0005,
        "slippage_pct": 0.0,
    }
    return config


def _write_ticket(tmp_path, trade_date="2026-05-23"):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    ticket = reports / f"manual_order_ticket_{trade_date}_300001.md"
    ticket.write_text(
        "\n".join(
            [
                "======== Manual Order Ticket ========",
                "Strategy: yang_yongxing_overnight_v1",
                f"Date: {trade_date}",
                "Direction: BUY",
                "Code: 300001",
                "Name: Demo Robotics",
                "Suggested Price: 18.5",
                "Max Acceptable Price: 18.78",
                "Suggested Amount: 3700.0",
                "Suggested Quantity: 200",
                "Stop Loss: 17.95",
                "=====================================",
            ]
        ),
        encoding="utf-8",
    )
    generated_time = datetime.strptime(f"{trade_date} 14:45:00", "%Y-%m-%d %H:%M:%S").timestamp()
    os.utime(ticket, (generated_time, generated_time))
    return ticket


def _write_sell_plan(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "sell_plan_2026-05-24.md").write_text(
        "\n".join(
            [
                "# Next-Day Sell Plan",
                "",
                "Action: WAIT_10_MIN",
                "Force Exit Before: 10:30",
                "Stop Loss: 17.95",
            ]
        ),
        encoding="utf-8",
    )


def _order_row(order_id, side, price, qty, trade_time, source_ticket_path="ticket.md"):
    return {
        "order_id": order_id,
        "ticket_id": "2026-05-23_300001",
        "strategy_name": "yang_yongxing_overnight_v1",
        "trade_date": trade_time[:10],
        "trade_time": trade_time,
        "code": "300001",
        "name": "Demo Robotics",
        "side": side,
        "price": price,
        "qty": qty,
        "amount": price * qty,
        "max_acceptable_price": 18.78,
        "stop_loss_price": 17.95,
        "source_ticket_path": source_ticket_path,
        "status": "FILLED",
    }
