from datetime import datetime, timedelta, timezone
from pathlib import Path

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.demo_data import demo_market_snapshot, demo_quotes
from overnight_quant.data.market_calendar import TAIL_SESSION
from overnight_quant.risk.risk_manager import RiskManager
from overnight_quant.strategy.yang_yongxing_overnight import YangYongxingOvernightStrategy, load_config


CN = timezone(timedelta(hours=8))


def test_weekend_live_scan_must_not_generate_ticket(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 23, 14, 30, tzinfo=CN), tmp_path / "cache")

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-23")

    assert result["selected"] == []
    assert result["tickets"] == []
    assert "non_trading_day" in result["market_gate"]["reject_reasons"]
    assert "non_trading_day" in Path(result["quality_report_path"]).read_text(encoding="utf-8")


def test_weekday_outside_tail_session_must_not_generate_ticket(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 22, 10, 0, tzinfo=CN), tmp_path / "cache")

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")

    assert result["selected"] == []
    assert "outside_tail_session" in result["market_gate"]["reject_reasons"]


def test_allow_outside_session_does_not_bypass_non_trading_day(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 23, 14, 30, tzinfo=CN), tmp_path / "cache")
    client.allow_outside_session = True

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-23")

    assert result["selected"] == []
    assert "non_trading_day" in result["market_gate"]["reject_reasons"]


def test_allow_outside_session_does_not_bypass_stale_quote(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 22, 10, 0, tzinfo=CN), tmp_path / "cache")
    client.allow_outside_session = True
    stale = dict(demo_quotes()[0])
    stale["code"] = "002001"
    stale["_sources"] = {key: "unit.live" for key in stale}
    stale["_freshness"] = {
        "tencent_quote": {
            "source": "tencent_quote",
            "data_date": "2026-05-21",
            "data_time": "15:00:00",
            "is_stale": True,
            "stale_reason": "quote_stale",
        }
    }
    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [stale])
    monkeypatch.setattr(client, "_tencent_quotes", lambda codes: {stale["code"]: stale})

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")

    assert "outside_tail_session" not in result["market_gate"]["reject_reasons"]
    assert result["selected"] == []
    assert any("quote_stale" in stock.get("risk_gate", {}).get("reasons", []) for stock in result["rejected"])


def test_tail_session_can_proceed_if_other_gates_pass(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 22, 14, 30, tzinfo=CN), tmp_path / "cache")

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")

    assert result["market_gate"]["session_state"] == TAIL_SESSION
    assert result["selected"]
    assert result["tickets"]


def test_stale_quote_rejects_ticket():
    config = load_config()
    stock = {
        "code": "300001",
        "name": "Stale Quote",
        "total_score": 99,
        "risk_flags": [],
        "_freshness_reasons": ["quote_stale"],
    }

    result = RiskManager(config).evaluate_buy(stock, {"pass": True}, planned_amount=4000)

    assert result["allow"] is False
    assert "quote_stale" in result["reasons"]


def test_stale_baidu_kline_rejects_ticket_and_is_reported(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 22, 14, 30, tzinfo=CN), tmp_path / "cache")
    monkeypatch.setattr(client, "_baidu_daily_kline", lambda code, lookback: [_daily_bar("2026-05-21")])

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")
    text = Path(result["quality_report_path"]).read_text(encoding="utf-8")

    assert result["selected"] == []
    assert any("quote_stale" in stock.get("risk_gate", {}).get("reasons", []) for stock in result["rejected"])
    assert "baidu_daily_kline: data_date=2026-05-21" in text
    assert "reason=quote_stale" in text


def test_baidu_kline_without_date_rejects_ticket_as_timestamp_missing(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 22, 14, 30, tzinfo=CN), tmp_path / "cache")
    monkeypatch.setattr(client, "_baidu_daily_kline", lambda code, lookback: [_daily_bar("")])

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")

    assert result["selected"] == []
    assert any("timestamp_missing" in stock.get("risk_gate", {}).get("reasons", []) for stock in result["rejected"])


def test_unavailable_kline_fallback_chain_rejects_ticket_as_freshness_unknown(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 22, 14, 30, tzinfo=CN), tmp_path / "cache")
    monkeypatch.setattr(client, "_baidu_daily_kline", lambda code, lookback: [])
    monkeypatch.setattr(client, "_eastmoney_daily_kline", lambda code, lookback: [])
    monkeypatch.setattr(client, "_mootdx_daily_kline", lambda code, lookback: [])

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-22")
    text = Path(result["quality_report_path"]).read_text(encoding="utf-8")

    assert result["selected"] == []
    assert any("freshness_unknown" in stock.get("risk_gate", {}).get("reasons", []) for stock in result["rejected"])
    assert "daily_kline_fallback_chain: data_date=" in text
    assert "reason=freshness_unknown" in text


def test_demo_mode_is_not_blocked_by_trading_calendar(tmp_path):
    config = _tmp_config(tmp_path)
    client = AStockClient("demo", now=datetime(2026, 5, 23, 14, 30, tzinfo=CN))

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-23")

    assert result["selected"]
    assert result["tickets"]


def test_quality_report_includes_session_state_and_freshness(tmp_path, monkeypatch):
    config = _tmp_config(tmp_path)
    client = _patched_live_client(monkeypatch, datetime(2026, 5, 23, 14, 30, tzinfo=CN), tmp_path / "cache")

    result = YangYongxingOvernightStrategy(client, config).scan("2026-05-23")
    text = Path(result["quality_report_path"]).read_text(encoding="utf-8")

    assert "session_state: NON_TRADING_DAY" in text
    assert "is_trade_day: NO" in text
    assert "freshness_summary" in text
    assert "## Field Coverage Improvement" in text
    assert "non_trading_day" in text


def _tmp_config(tmp_path):
    config = load_config()
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    return config


def _patched_live_client(monkeypatch, now, cache_dir):
    client = AStockClient("live", now=now, cache_dir=cache_dir)
    good = dict(demo_quotes()[0])
    good["code"] = "002001"
    good["_sources"] = {key: "unit.live" for key in good}
    good["_freshness"] = {
        "tencent_quote": {
            "source": "tencent_quote",
            "data_date": now.date().isoformat(),
            "data_time": now.strftime("%H:%M:%S"),
            "is_stale": False,
            "stale_reason": "",
        }
    }
    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [good])
    monkeypatch.setattr(client, "_tencent_quotes", lambda codes: {good["code"]: good})
    monkeypatch.setattr(client, "_eastmoney_quote_meta", lambda codes: {})
    monkeypatch.setattr(client, "_eastmoney_stock_info", lambda code: {"f189": "20200101", "f127": "Test"})
    monkeypatch.setattr(client, "_eastmoney_fund_flow_minute", lambda code: [{"main_net": 1000, "large_net": 500}])
    monkeypatch.setattr(client, "_baidu_daily_kline", lambda code, lookback: [_daily_bar(now.date().isoformat())])
    monkeypatch.setattr(client, "_hsgt_realtime", lambda: [{"time": "14:30", "hgt_yi": 5, "sgt_yi": 4}])
    return client


def _daily_bar(data_date):
    return {
        "date": data_date,
        "open": 18.0,
        "close": 18.5,
        "high": 18.7,
        "low": 17.9,
        "volume": 1000,
        "amount": 10000,
        "ma5": 18.2,
        "ma10": 18.0,
        "ma20": 17.8,
    }
