from overnight_quant.data.demo_data import demo_market_snapshot, demo_quotes, demo_daily_kline
from overnight_quant.strategy.filters import evaluate_market_gate
from overnight_quant.strategy.scoring import rank_scored, score_stock
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def by_code(code):
    return next(stock for stock in demo_quotes() if stock["code"] == code)


def score(code):
    config = load_config()
    market_score = evaluate_market_gate(demo_market_snapshot())["score"]
    return score_stock(by_code(code), demo_daily_kline(code), market_score, config)


def test_qualified_stock_scores_above_buy_threshold():
    result = score("300001")

    assert result["total_score"] >= 75
    assert result["decision"] == "BUY_CANDIDATE"
    assert result["score_reasons"]


def test_theme_and_capital_weakness_lower_scores():
    qualified = score("300001")
    no_theme = score("300004")
    outflow = score("002005")
    tail_dive = score("600003")

    ranked = rank_scored([outflow, tail_dive, no_theme, qualified])

    assert ranked[0]["code"] == "300001"
    assert qualified["total_score"] > no_theme["total_score"]
    assert qualified["total_score"] > outflow["total_score"]
    assert qualified["total_score"] > tail_dive["total_score"]

