from datetime import datetime, timedelta, timezone

from overnight_quant.data.astock_client import AStockClient
from overnight_quant.data.demo_data import demo_quotes
from overnight_quant.risk.risk_manager import RiskManager
from overnight_quant.strategy.filters import initial_filter
from overnight_quant.strategy.scoring import score_stock
from overnight_quant.strategy.yang_yongxing_overnight import load_config


CN = timezone(timedelta(hours=8))


def test_list_date_cache_hit_does_not_call_remote(tmp_path, monkeypatch):
    client = AStockClient("live", now=datetime(2026, 5, 22, 14, 30, tzinfo=CN), cache_dir=tmp_path)
    client.update_list_date_cache("300001", "2020-01-02", "unit")
    seed = _seed_without_list_date()
    quote = _quote_for(seed)

    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [seed])
    monkeypatch.setattr(client, "_tencent_quotes", lambda codes: {"300001": quote})
    monkeypatch.setattr(client, "_eastmoney_quote_meta", lambda codes: {})
    monkeypatch.setattr(
        client,
        "_eastmoney_stock_info",
        lambda code: (_ for _ in ()).throw(AssertionError("remote stock info should not be called")),
    )
    monkeypatch.setattr(client, "_eastmoney_fund_flow_minute", lambda code: [{"main_net": 1000, "large_net": 500}])

    rows = client.get_candidate_quotes()

    assert rows[0]["list_date"] == "2020-01-02"
    assert rows[0]["is_new_stock"] is False
    assert rows[0]["_sources"]["list_date"] == "list_date_cache.unit"


def test_list_date_missing_uses_specific_reject_reason(monkeypatch):
    client = AStockClient("live", now=datetime(2026, 5, 22, 14, 30, tzinfo=CN))
    seed = _seed_without_list_date()
    quote = _quote_for(seed)

    monkeypatch.setattr(client, "_fetch_live_candidate_seeds", lambda: [seed])
    monkeypatch.setattr(client, "_tencent_quotes", lambda codes: {"300001": quote})
    monkeypatch.setattr(client, "_eastmoney_quote_meta", lambda codes: {})
    monkeypatch.setattr(client, "_eastmoney_stock_info", lambda code: {})
    monkeypatch.setattr(client, "_eastmoney_fund_flow_minute", lambda code: [{"main_net": 1000, "large_net": 500}])

    rows = client.get_candidate_quotes()
    risk = RiskManager(load_config()).evaluate_buy({**rows[0], "total_score": 99, "risk_flags": []}, {"pass": True}, 4000)

    assert "list_date_missing_not_found" in rows[0]["_risk_unknown_reasons"]
    assert risk["allow"] is False
    assert "list_date_missing_not_found" in risk["reasons"]


def test_list_date_present_allows_risk_gate_to_continue():
    stock = {
        **demo_quotes()[0],
        "total_score": 99,
        "risk_flags": [],
        "list_date": "2020-01-02",
        "is_new_stock": False,
        "_missing_fields": [],
        "_risk_unknown_reasons": [],
    }

    result = RiskManager(load_config()).evaluate_buy(stock, {"pass": True}, 4000)

    assert result["allow"] is True
    assert not any(reason.startswith("list_date_missing") for reason in result["reasons"])


def test_fund_flow_missing_does_not_reject_but_lowers_capital_score():
    config = load_config()
    base = {
        **demo_quotes()[0],
        "main_net": None,
        "big_order_net": None,
        "_missing_fields": ["main_net", "big_order_net"],
        "_risk_unknown_reasons": [],
    }

    scored = score_stock(base, [], 90, config)
    risk = RiskManager(config).evaluate_buy({**scored, "total_score": 99}, {"pass": True}, 4000)

    assert risk["allow"] is True
    assert scored["capital_score"] < 55
    assert "main_net_missing" in scored["score_reasons"]


def test_fund_flow_missing_estimates_main_net_from_big_order():
    stock = {**demo_quotes()[0], "main_net": None, "big_order_net": 1234}

    merged = AStockClient("live")._merge_fund_flow(stock, [], "missing", "fund flow down")

    assert merged["main_net"] == 1234
    assert merged["fund_flow_source"] == "estimated_from_big_order_net"
    assert merged["fund_flow_error"] == "fund flow down"


def test_st_name_variations_are_rejected():
    config = load_config()
    for name in ["ST测试", "*ST测试", "S*ST测试", "测试退", "测试退市"]:
        stock = {**demo_quotes()[0], "name": name, "is_st": False}
        derived = AStockClient("live")._derive_live_safety_and_shape(stock)

        result = initial_filter(derived, config)

        assert result["pass"] is False
        assert "st_stock" in result["reject_reasons"]


def test_suspended_quote_variants_are_rejected():
    config = load_config()
    client = AStockClient("live")
    variants = [
        {"price": 0.0, "amount_wan": 10000, "volume": 1000},
        {"price": 10.0, "amount_wan": 0.0, "volume": 1000},
        {"price": 10.0, "amount_wan": 10000, "volume": 0},
        {"price": 10.0, "amount_wan": 10000, "volume": 1000, "_quote_missing": True},
    ]

    for patch in variants:
        stock = {**demo_quotes()[0], **patch, "is_suspended": False}
        derived = client._derive_live_safety_and_shape(stock)
        result = initial_filter(derived, config)

        assert derived["is_suspended"] is True
        assert result["pass"] is False
        assert "suspended" in result["reject_reasons"]


def _seed_without_list_date():
    return {
        "code": "300001",
        "name": "Demo Robotics",
        "price": 18.5,
        "change_pct": 4.8,
        "vol_ratio": 1.6,
        "turnover_pct": 8.4,
        "amount_wan": 36500,
        "float_mcap_yi": 118,
        "limit_up": 20.31,
        "limit_down": 16.65,
        "is_limit_up": False,
        "is_st": False,
        "is_suspended": False,
        "is_bj_stock": False,
        "theme_tags": ["Robotics"],
        "_sources": {"code": "unit.seed", "name": "unit.seed"},
    }


def _quote_for(seed):
    quote = dict(seed)
    quote["_freshness"] = {
        "tencent_quote": {
            "source": "tencent_quote",
            "data_date": "2026-05-22",
            "data_time": "14:30:00",
            "is_stale": False,
            "stale_reason": "",
        }
    }
    quote["_sources"] = {key: "unit.quote" for key in quote if not key.startswith("_")}
    return quote
