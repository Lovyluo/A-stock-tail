from overnight_quant.data.demo_data import demo_market_snapshot, demo_quotes
from overnight_quant.strategy.filters import evaluate_market_gate, evaluate_tail_stability, initial_filter
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def by_code(code):
    return next(stock for stock in demo_quotes() if stock["code"] == code)


def test_market_gate_passes_demo_market():
    gate = evaluate_market_gate(demo_market_snapshot())

    assert gate["pass"] is True
    assert gate["score"] >= 70
    assert gate["reasons"]


def test_initial_filter_keeps_qualified_demo_stock():
    result = initial_filter(by_code("300001"), load_config())

    assert result["pass"] is True
    assert result["reject_reasons"] == []


def test_initial_filter_rejects_non_00_60_when_prefix_scope_enabled():
    config = load_config()
    config.setdefault("filters", {})["enforce_allowed_code_prefixes"] = True

    assert "code_prefix_not_allowed" in initial_filter(by_code("300001"), config)["reject_reasons"]
    assert "code_prefix_not_allowed" in initial_filter(by_code("830009"), config)["reject_reasons"]
    assert "code_prefix_not_allowed" not in initial_filter(by_code("002005"), config)["reject_reasons"]
    assert "code_prefix_not_allowed" not in initial_filter(by_code("600003"), config)["reject_reasons"]


def test_initial_filter_rejects_untradable_and_disqualified_samples():
    config = load_config()
    expected = {
        "002002": "limit_up_unavailable",
        "600006": "st_stock",
        "300007": "suspended",
        "301008": "new_stock",
    }

    for code, reason in expected.items():
        result = initial_filter(by_code(code), config)
        assert result["pass"] is False
        assert reason in result["reject_reasons"]


def test_initial_filter_does_not_treat_word_stock_as_st():
    config = load_config()

    new_stock = initial_filter(by_code("301008"), config)
    bj_stock = initial_filter(by_code("830009"), config)

    assert "new_stock" in new_stock["reject_reasons"]
    assert "st_stock" not in new_stock["reject_reasons"]
    assert "bj_stock" in bj_stock["reject_reasons"]
    assert "st_stock" not in bj_stock["reject_reasons"]


def test_tail_stability_rejects_tail_dive_sample():
    stock = dict(by_code("600003"))
    stock["tail_pullback_pct"] = 3.6
    result = evaluate_tail_stability(stock, load_config())

    assert result["pass"] is False
    assert "tail_pullback_too_large" in result["reject_reasons"]


def test_tail_stability_allows_moderate_daily_high_pullback():
    stock = dict(by_code("600003"))
    stock["tail_pullback_pct"] = 3.0
    result = evaluate_tail_stability(stock, load_config())

    assert result["pass"] is True
    assert "tail_stable" in result["reasons"]
