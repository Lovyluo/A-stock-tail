from datetime import datetime, timedelta, timezone
from pathlib import Path

from overnight_quant.execution.order_recorder import bind_stock_name, record_manual_order, record_position_update, void_manual_order
from overnight_quant.execution.position_tracker import get_open_positions, get_position_summaries, read_order_rows
from overnight_quant.scripts.run_sell_plan import _realtime_trigger_cn, _sell_trigger_cn, generate_sell_plan
from overnight_quant.strategy.yang_yongxing_overnight import load_config


CN = timezone(timedelta(hours=8))


def test_valid_manual_buy_order_is_recorded(tmp_path):
    config = _tmp_config(tmp_path)
    ticket = _write_ticket(tmp_path)

    result = record_manual_order(config, code="300001", price=18.5, qty=200, side="BUY", trade_time="2026-05-23 14:52:00")

    assert result["allow"] is True
    assert Path(result["orders_csv"]).exists()
    row = Path(result["orders_csv"]).read_text(encoding="utf-8")
    assert "300001" in row
    assert str(ticket) in row


def test_buy_price_above_max_acceptable_is_rejected(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)

    result = record_manual_order(config, code="300001", price=19.0, qty=200, side="BUY", trade_time="2026-05-23 14:52:00")

    assert result["allow"] is False
    assert "price_above_max_acceptable" in result["reasons"]


def test_qty_not_multiple_of_100_is_rejected(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)

    result = record_manual_order(config, code="300001", price=18.5, qty=150, side="BUY", trade_time="2026-05-23 14:52:00")

    assert result["allow"] is False
    assert "qty_not_board_lot" in result["reasons"]


def test_duplicate_buy_for_same_ticket_is_rejected(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    first = record_manual_order(config, code="300001", price=18.5, qty=200, side="BUY", trade_time="2026-05-23 14:52:00")

    second = record_manual_order(config, code="300001", price=18.5, qty=200, side="BUY", trade_time="2026-05-23 14:53:00")

    assert first["allow"] is True
    assert second["allow"] is False
    assert "duplicate_buy_for_ticket" in second["reasons"]


def test_record_buy_replaces_legacy_demo_placeholder(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    records = tmp_path / "records"
    records.mkdir(parents=True, exist_ok=True)
    (records / "manual_orders.csv").write_text(
        "strategy,trade_date,code,name,buy_price,quantity,stop_loss\n"
        "yang_yongxing_overnight_v1,2026-05-23,300001,Demo Robotics,18.5,200,17.95\n",
        encoding="utf-8",
    )

    result = record_manual_order(config, code="300001", price=18.5, qty=200, side="BUY", trade_time="2026-05-23 14:52:00")
    positions = get_open_positions(config["paths"]["records_dir"])

    assert result["allow"] is True
    assert len(positions) == 1
    assert positions[0]["open_qty"] == 200


def test_position_tracker_detects_open_position(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, code="300001", price=18.5, qty=200, side="BUY", trade_time="2026-05-23 14:52:00")

    positions = get_open_positions(config["paths"]["records_dir"])

    assert len(positions) == 1
    assert positions[0]["code"] == "300001"
    assert positions[0]["open_qty"] == 200


def test_position_update_buy_without_ticket_is_recorded(tmp_path):
    config = _tmp_config(tmp_path)

    result = record_position_update(
        config,
        code="300001",
        name="Demo Robotics",
        price=18.5,
        qty=200,
        side="BUY",
        trade_time="2026-05-23 14:52:00",
        stop_loss_price=17.95,
    )
    positions = get_open_positions(config["paths"]["records_dir"])

    assert result["allow"] is True
    assert result["row"]["strategy_name"] == "manual_position_update"
    assert result["row"]["source_ticket_path"] == ""
    assert len(positions) == 1
    assert positions[0]["code"] == "300001"
    assert positions[0]["open_qty"] == 200
    assert positions[0]["stop_loss_price"] == 17.95


def test_position_update_preserves_multiple_same_code_buys(tmp_path):
    config = _tmp_config(tmp_path)

    first = record_position_update(
        config,
        code="300001",
        name="Demo Robotics",
        price=18.0,
        qty=100,
        side="BUY",
        trade_time="2026-05-23 10:01:00",
        stop_loss_price=17.5,
    )
    second = record_position_update(
        config,
        code="300001",
        name="Demo Robotics",
        price=20.0,
        qty=200,
        side="BUY",
        trade_time="2026-05-23 10:15:00",
        stop_loss_price=18.8,
    )
    rows = read_order_rows(config["paths"]["records_dir"])
    summary = get_position_summaries(config["paths"]["records_dir"])[0]

    assert first["allow"] is True
    assert second["allow"] is True
    assert len(rows) == 2
    assert [float(row["price"]) for row in rows] == [18.0, 20.0]
    assert summary["open_qty"] == 300
    assert summary["avg_buy_price"] == 19.3333
    assert summary["stop_loss_price"] == 18.8


def test_position_update_uses_bound_name_when_later_input_name_is_wrong(tmp_path):
    config = _tmp_config(tmp_path)

    record_position_update(
        config,
        code="300001",
        name="Correct Name",
        price=18.0,
        qty=100,
        side="BUY",
        trade_time="2026-05-23 10:01:00",
    )
    result = record_position_update(
        config,
        code="300001",
        name="Wrong Name",
        price=20.0,
        qty=100,
        side="BUY",
        trade_time="2026-05-23 10:15:00",
    )
    rows = read_order_rows(config["paths"]["records_dir"])
    summary = get_position_summaries(config["paths"]["records_dir"])[0]

    assert result["allow"] is True
    assert rows[1]["name"] == "Correct Name"
    assert summary["name"] == "Correct Name"


def test_manual_name_binding_can_correct_existing_display_name(tmp_path):
    config = _tmp_config(tmp_path)
    record_position_update(
        config,
        code="300001",
        name="Wrong Name",
        price=18.0,
        qty=100,
        side="BUY",
        trade_time="2026-05-23 10:01:00",
    )

    result = bind_stock_name(config, "300001", "Correct Name", notes="typo fix")
    summary = get_position_summaries(config["paths"]["records_dir"])[0]

    assert result["allow"] is True
    assert summary["name"] == "Correct Name"


def test_void_manual_order_removes_bad_fill_from_position_math(tmp_path):
    config = _tmp_config(tmp_path)
    first = record_position_update(
        config,
        code="300001",
        name="Demo Robotics",
        price=18.0,
        qty=100,
        side="BUY",
        trade_time="2026-05-23 10:01:00",
    )
    record_position_update(
        config,
        code="300001",
        name="Demo Robotics",
        price=30.0,
        qty=100,
        side="BUY",
        trade_time="2026-05-23 10:15:00",
    )

    result = void_manual_order(config, first["row"]["order_id"], notes="wrong lot")
    rows = read_order_rows(config["paths"]["records_dir"])
    summary = get_position_summaries(config["paths"]["records_dir"])[0]

    assert result["allow"] is True
    assert rows[0]["status"] == "VOID"
    assert summary["open_qty"] == 100
    assert summary["avg_buy_price"] == 30.0


def test_reopened_position_starts_new_cost_cycle_after_full_close(tmp_path):
    config = _tmp_config(tmp_path)
    record_position_update(config, "300001", 10.0, 100, "BUY", "2026-05-23 10:01:00", name="Demo Robotics")
    record_position_update(config, "300001", 11.0, 100, "SELL", "2026-05-23 10:30:00", name="Demo Robotics")
    record_position_update(config, "300001", 12.0, 200, "BUY", "2026-05-23 13:30:00", name="Demo Robotics")

    summaries = get_position_summaries(config["paths"]["records_dir"])
    open_positions = get_open_positions(config["paths"]["records_dir"])

    assert [row["status"] for row in summaries] == ["CLOSED", "OPEN"]
    assert summaries[0]["avg_buy_price"] == 10.0
    assert open_positions[0]["open_qty"] == 200
    assert open_positions[0]["avg_buy_price"] == 12.0


def test_position_update_rejects_sell_more_than_open_qty(tmp_path):
    config = _tmp_config(tmp_path)
    record_position_update(config, "300001", 18.5, 200, "BUY", "2026-05-23 14:52:00", name="Demo Robotics")

    result = record_position_update(config, "300001", 19.02, 300, "SELL", "2026-05-24 09:45:00")

    assert result["allow"] is False
    assert "sell_qty_exceeds_open_position" in result["reasons"]


def test_sell_plan_uses_real_manual_order_instead_of_demo_placeholder(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record_manual_order(config, code="300001", price=18.5, qty=200, side="BUY", trade_time="2026-05-23 14:52:00")
    client = _price_client(price=19.02)

    result = generate_sell_plan(config=config, client=client, mode="demo", trade_date="2026-05-24")

    assert result["status"] == "SELL_PLAN_READY"
    assert result["rows"][0]["code"] == "300001"
    assert result["rows"][0]["buy_price"] == 18.5
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "Demo Robotics" in text
    assert "持仓卖出计划明细" in text
    assert "执行步骤" in text
    assert "策略逻辑" in text
    assert "VWAP" in text


def test_sell_plan_includes_market_theme_fund_and_volume_context(tmp_path):
    config = _tmp_config(tmp_path)
    record_position_update(
        config,
        code="300001",
        name="Demo Robotics",
        price=18.5,
        qty=200,
        side="BUY",
        trade_time="2026-05-23 14:52:00",
        stop_loss_price=17.95,
    )

    result = generate_sell_plan(config=config, client=_enriched_price_client(), mode="live", trade_date="2026-05-24")
    row = result["rows"][0]
    text = Path(result["path"]).read_text(encoding="utf-8")

    assert row["context_score_total"] <= -4
    assert "大盘偏弱" in row["market_context_cn"]
    assert "题材偏弱" in row["theme_context_cn"]
    assert "多日主力" in row["fund_context_cn"]
    assert "当日主力" in row["today_main_fund_cn"]
    assert "放量下跌" in row["volume_context_cn"]
    assert "综合执行建议" in text
    assert "大盘来源" in text
    assert "多日资金来源" in text


def test_sell_trigger_uses_time_aware_force_exit_wording():
    text = _sell_trigger_cn(
        "WAIT_10_MIN",
        {
            "effective_stop_loss_price": 17.95,
            "vwap": 18.1,
            "force_exit_before": "10:30",
            "past_force_exit_time": True,
            "context_score_total": -2,
        },
    )

    assert "已过 10:30" in text
    assert "10:30 前仍未走强" not in text
    assert "资金继续流出" in text


def test_realtime_trigger_does_not_claim_stop_loss_when_price_is_above_stop():
    text = _realtime_trigger_cn(
        "WAIT_10_MIN",
        {
            "current_price": 71.25,
            "effective_stop_loss_price": 68.657,
            "vwap": 68.621,
            "minute_fund_score": -2,
            "past_force_exit_time": True,
            "force_exit_before": "10:30",
        },
    )

    assert "已跌破止损价" not in text
    assert "止损价 68.657，跌破才触发硬止损" in text
    assert "当前触发" in text
    assert "分钟主力资金继续流出" in text


def test_sell_plan_falls_back_to_eastmoney_core_theme_and_sina_funds(tmp_path):
    config = _tmp_config(tmp_path)
    record_position_update(
        config,
        code="300001",
        name="Demo Robotics",
        price=18.5,
        qty=200,
        side="BUY",
        trade_time="2026-05-23 14:52:00",
    )

    result = generate_sell_plan(config=config, client=_fallback_data_client(), mode="live", trade_date="2026-05-24")
    row = result["rows"][0]

    assert row["theme_source"] == "eastmoney_core_conception"
    assert "人工智能" in row["theme_context_cn"]
    assert row["fund_source"] == "sina_money_flow_history"
    assert "多日资金" in row["fund_context_cn"]
    assert row["today_fund_source"] == "sina_money_flow_current"
    assert "当日主力流入" in row["today_main_fund_cn"]


def test_no_open_position_returns_no_open_position(tmp_path):
    config = _tmp_config(tmp_path)

    result = generate_sell_plan(config=config, client=_price_client(), mode="live", trade_date="2026-05-24")

    assert result["status"] == "NO_OPEN_POSITION"
    assert "NO_OPEN_POSITION" in Path(result["path"]).read_text(encoding="utf-8")


def test_sell_without_open_buy_is_rejected(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)

    result = record_manual_order(config, code="300001", price=19.0, qty=200, side="SELL", trade_time="2026-05-24 09:40:00")

    assert result["allow"] is False
    assert "sell_without_open_buy" in result["reasons"]


def test_trade_lifecycle_report_is_generated(tmp_path):
    config = _tmp_config(tmp_path)
    _write_ticket(tmp_path)
    record = record_manual_order(config, code="300001", price=18.5, qty=200, side="BUY", trade_time="2026-05-23 14:52:00")

    text = Path(record["lifecycle_report_path"]).read_text(encoding="utf-8")

    assert "BOUGHT_OPEN" in text
    assert "manual BUY: YES" in text


def test_no_automatic_trading_code_exists_phase24():
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
    return config


def _write_ticket(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    ticket = reports / "manual_order_ticket_2026-05-23_300001.md"
    ticket.write_text(
        "\n".join(
            [
                "======== Manual Order Ticket ========",
                "Strategy: yang_yongxing_overnight_v1",
                "Date: 2026-05-23",
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
    mtime = datetime(2026, 5, 23, 14, 45, tzinfo=CN).timestamp()
    import os

    os.utime(ticket, (mtime, mtime))
    return ticket


class _price_client:
    def __init__(self, price=19.02):
        self.price = price

    def get_current_price(self, code):
        return {
            "code": code,
            "name": "Demo Robotics",
            "price": self.price,
            "open_price": self.price,
            "open_change_pct": 2.0,
            "is_limit_up": False,
            "is_limit_down": False,
        }


class _enriched_price_client(_price_client):
    def __init__(self):
        super().__init__(price=17.72)

    def get_current_price(self, code):
        data = super().get_current_price(code)
        data.update(
            {
                "price": 17.72,
                "open_price": 18.0,
                "open_change_pct": -1.1,
                "current_change_pct": -2.4,
                "high": 18.2,
                "low": 17.6,
                "amount_wan": 32000,
                "turnover_pct": 8.2,
                "vol_ratio": 1.9,
                "vwap": 17.95,
            }
        )
        return data

    def get_intraday_bars(self, code):
        return [
            {"time": "09:35", "high": 18.1, "low": 17.8, "vwap": 18.0},
            {"time": "10:05", "high": 18.0, "low": 17.6, "vwap": 17.95},
        ]

    def _tencent_quotes(self, codes):
        return {
            "000001": {"name": "上证指数", "change_pct": -1.25},
            "000300": {"name": "沪深300", "change_pct": -1.4},
            "399006": {"name": "创业板指", "change_pct": -2.1},
        }

    def _safe_baidu_concept_blocks(self, code):
        return {
            "industry": [{"name": "软件开发", "change_pct": "-2.10%"}],
            "concept": [{"name": "人工智能", "change_pct": "-1.80%"}],
            "concept_tags": ["人工智能"],
        }

    def _eastmoney_fund_flow_daily(self, code, limit=10):
        return [
            {"time": f"2026-05-{day:02d}", "main_net": -20_000_000}
            for day in range(14, 14 + int(limit))
        ]

    def _eastmoney_quote_fund_flow(self, codes):
        return {
            str(codes[0]).zfill(6): {
                "main_net": -120_000_000,
                "large_net": -80_000_000,
                "super_net": -40_000_000,
            }
        }

    def _eastmoney_daily_kline(self, code, lookback):
        rows = []
        for idx in range(lookback):
            close = 20 - idx * 0.04
            rows.append(
                {
                    "date": f"2026-05-{idx + 1:02d}",
                    "open": close + 0.1,
                    "close": close,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "volume": 100_000,
                    "amount": 2_000_000,
                }
            )
        rows[-1]["close"] = 17.72
        rows[-1]["volume"] = 180_000
        return rows


class _fallback_data_client(_price_client):
    def __init__(self):
        super().__init__(price=18.8)

    def _tencent_quotes(self, codes):
        return {
            "000001": {"name": "上证指数", "change_pct": 0.2},
            "000300": {"name": "沪深300", "change_pct": 0.1},
            "399006": {"name": "创业板指", "change_pct": 0.4},
        }

    def get_intraday_bars(self, code):
        return []

    def _safe_baidu_concept_blocks(self, code):
        return {}

    def _eastmoney_core_conception_blocks(self, code):
        return {
            "industry": [{"name": "软件开发", "change_pct": 0.8, "board_code": "BK0737"}],
            "concept": [{"name": "人工智能", "change_pct": 1.6, "board_code": "BK0800"}],
            "concept_tags": ["软件开发", "人工智能"],
        }

    def _eastmoney_fund_flow_kline_daily(self, code, limit=10):
        raise RuntimeError("eastmoney unavailable")

    def _eastmoney_fund_flow_daily(self, code, limit=10):
        raise RuntimeError("eastmoney unavailable")

    def _sina_money_flow_history(self, code):
        return [
            {"time": f"2026-07-{day:02d}", "main_net": 10_000_000}
            for day in range(1, 11)
        ]

    def _safe_quote_fund_flow(self, codes):
        return {}

    def _safe_fund_flow(self, code):
        return [], "missing", "empty"

    def _sina_money_flow_current(self, codes):
        return {str(codes[0]).zfill(6): {"main_net": 45_000_000, "large_net": 30_000_000, "super_net": 15_000_000}}

    def _eastmoney_daily_kline(self, code, lookback):
        rows = []
        for idx in range(lookback):
            close = 18 + idx * 0.01
            rows.append(
                {
                    "date": f"2026-05-{idx + 1:02d}",
                    "open": close,
                    "close": close,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "volume": 100_000,
                    "amount": 2_000_000,
                }
            )
        return rows
