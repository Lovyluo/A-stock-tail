from overnight_quant.data.demo_data import demo_market_snapshot, demo_quotes, demo_daily_kline
from overnight_quant.risk.risk_manager import RiskManager
from overnight_quant.strategy.filters import evaluate_market_gate
from overnight_quant.strategy.scoring import score_stock
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def by_code(code):
    return next(stock for stock in demo_quotes() if stock["code"] == code)


def scored(code):
    config = load_config()
    market_gate = evaluate_market_gate(demo_market_snapshot())
    return score_stock(by_code(code), demo_daily_kline(code), market_gate["score"], config)


def test_risk_allows_qualified_stock():
    config = load_config()
    risk = RiskManager(config)
    market_gate = evaluate_market_gate(demo_market_snapshot())

    result = risk.evaluate_buy(scored("300001"), market_gate, planned_amount=4000)

    assert result["allow"] is True
    assert result["risk_level"] in {"LOW", "MEDIUM"}


def test_risk_rejects_market_fail():
    config = load_config()
    risk = RiskManager(config)
    market_gate = {"pass": False, "score": 20, "reasons": [], "reject_reasons": ["market_gate_fail"]}

    result = risk.evaluate_buy(scored("300001"), market_gate, planned_amount=4000)

    assert result["allow"] is False
    assert "market_gate_fail" in result["reasons"]


def test_risk_rejects_limit_up_low_score_and_max_order():
    config = load_config()
    risk = RiskManager(config)
    market_gate = evaluate_market_gate(demo_market_snapshot())

    limit_up = risk.evaluate_buy(scored("002002"), market_gate, planned_amount=4000)
    low_score = risk.evaluate_buy(scored("300004"), market_gate, planned_amount=4000)
    too_large = risk.evaluate_buy(scored("300001"), market_gate, planned_amount=6000)

    assert limit_up["allow"] is False
    assert "limit_up_unavailable" in limit_up["reasons"]
    assert low_score["allow"] is False
    assert "score_below_threshold" in low_score["reasons"]
    assert too_large["allow"] is False
    assert "order_value_exceeds_limit" in too_large["reasons"]
