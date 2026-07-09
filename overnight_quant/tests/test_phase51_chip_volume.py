from __future__ import annotations

from pathlib import Path

from overnight_quant.strategy.chip_volume import (
    build_chip_volume_confidence,
    build_price_volume_profile,
    calculate_chip_metrics,
    calculate_volume_signals,
    classify_chip_peak,
)


def _bars(closes: list[float], volumes: list[float] | None = None) -> list[dict]:
    if volumes is None:
        volumes = [1000 + index * 10 for index in range(len(closes))]
    return [
        {
            "date": f"2026-05-{(index % 28) + 1:02d}",
            "open": round(close * 0.99, 3),
            "high": round(close * 1.01, 3),
            "low": round(close * 0.99, 3),
            "close": close,
            "volume": volumes[index],
            "amount": close * volumes[index],
        }
        for index, close in enumerate(closes)
    ]


def test_prev_day_high_volume_is_detected():
    signals = calculate_volume_signals(_bars([10, 10, 10, 10, 10], [100, 110, 120, 500, 150]))

    assert signals["prev_day_high_volume"] is True
    assert signals["today_volume_confirm"] is False
    assert "prev_day_high_volume" in signals["reasons"]


def test_today_volume_confirmation_uses_prior_three_day_average():
    signals = calculate_volume_signals(_bars([10, 10, 10, 10, 10], [100, 110, 120, 130, 220]))

    assert signals["today_volume_confirm"] is True
    assert signals["volume_expansion_ratio_3d"] > 1.2
    assert signals["volume_signal"] == "today_confirmed"


def test_chip_avg_cost_20d_and_60d_are_volume_weighted():
    closes = [10.0] * 40 + [12.0] * 20
    bars = _bars(closes, [1000] * 60)

    metrics = calculate_chip_metrics(bars, current_price=12.0)

    assert round(metrics["chip_avg_cost_20d"], 2) == 12.0
    assert round(metrics["chip_avg_cost_60d"], 2) == 10.67
    assert metrics["current_vs_chip_cost_pct"] == 0.0


def test_pressure_and_support_ratios_split_profile_around_current_price():
    bars = _bars([9.5, 9.6, 9.7, 10.3, 10.4, 10.5], [100, 100, 100, 300, 300, 300])

    profile = build_price_volume_profile(bars, lookback=6, bucket_pct=1.0)
    metrics = calculate_chip_metrics(bars, current_price=10.0)

    assert profile["levels"]
    assert metrics["overhead_pressure_ratio"] > metrics["downside_support_ratio"]
    assert round(metrics["overhead_pressure_ratio"], 2) == 0.75
    assert round(metrics["downside_support_ratio"], 2) == 0.25


def test_accumulation_peak_is_identified_near_proxy_cost_with_supportive_flow():
    bars = _bars([10.0] * 60, [1000] * 56 + [1100, 1200, 5000, 1500])
    stock = {"price": 10.05, "main_net": 3000, "big_order_net": 1000, "tail_pullback_pct": 0.5}

    peak = classify_chip_peak(stock, bars)
    confidence = build_chip_volume_confidence(stock, bars)

    assert peak["peak_type"] == "accumulation"
    assert confidence["peak_type"] == "accumulation"
    assert confidence["confidence_delta"] > 0


def test_markup_peak_is_identified_on_confirmed_volume_breakout():
    closes = [10.0] * 40 + [10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4, 11.6, 11.8, 12.0]
    volumes = [1000] * (len(closes) - 1) + [2200]
    bars = _bars(closes, volumes)
    stock = {"price": 12.0, "main_net": 6000, "big_order_net": 2000, "tail_pullback_pct": 0.3}

    confidence = build_chip_volume_confidence(stock, bars)

    assert confidence["peak_type"] == "markup"
    assert confidence["today_volume_confirm"] is True
    assert confidence["confidence_delta"] > 0


def test_distribution_peak_is_penalized_when_extended_volume_has_outflow():
    closes = [10.0] * 50 + [13.0, 13.2, 13.4, 13.6, 13.8]
    volumes = [1000] * (len(closes) - 1) + [2600]
    bars = _bars(closes, volumes)
    stock = {
        "price": 13.8,
        "main_net": -6000,
        "big_order_net": -2500,
        "tail_pullback_pct": 2.8,
        "upper_shadow_ratio": 0.5,
    }

    confidence = build_chip_volume_confidence(stock, bars)

    assert confidence["peak_type"] == "distribution"
    assert confidence["confidence_delta"] < 0
    assert "chip_peak_distribution" in confidence["reasons"]


def test_missing_daily_bars_degrade_safely():
    confidence = build_chip_volume_confidence({"price": 10.0}, [])

    assert confidence["peak_type"] == "neutral"
    assert confidence["confidence_delta"] == 0
    assert confidence["volume_signal"] == "volume_data_missing"
    assert "chip_volume_data_missing" in confidence["reasons"]


def test_chip_volume_module_does_not_introduce_execution_keywords():
    source = Path("overnight_quant/strategy/chip_volume.py").read_text(encoding="utf-8")
    forbidden = [
        "place_order",
        "broker_api",
        "auto_trade",
        "自动下单",
        "自动交易",
        "点击证券软件",
    ]

    assert not any(token in source for token in forbidden)


def test_after_close_report_exposes_chip_volume_section_for_dashboard(tmp_path):
    from overnight_quant.reports.after_close_report import write_after_close_report
    from overnight_quant.ui.result_parser import parse_after_close_chip_volume_table

    row = {
        "trade_date": "2026-05-22",
        "next_trade_date": "2026-05-25",
        "code": "600001",
        "name": "测试股份",
        "category": "A",
        "score": 88.0,
        "theme_tags": ["AI"],
        "theme_market_state": "",
        "theme_block_change_pct": 1.2,
        "close_price": 10.5,
        "change_pct": 4.2,
        "turnover_pct": 7.5,
        "amount_wan": 23000,
        "vol_ratio": 1.5,
        "main_net": 1200,
        "main_net_source": "eastmoney_fund_flow_minute",
        "estimated_capital_flow": False,
        "chip_peak_type": "accumulation",
        "chip_avg_cost_20d": 10.1,
        "chip_avg_cost_60d": 9.8,
        "current_vs_chip_cost_pct": 3.96,
        "overhead_pressure_ratio": 0.2,
        "downside_support_ratio": 0.8,
        "main_force_chip_proxy": 22,
        "volume_signal": "today_confirmed",
        "confidence_delta": 6,
        "chip_volume_reasons": "today_volume_confirm|chip_peak_accumulation",
        "reason": "today_volume_confirm|chip_peak_accumulation",
        "positive_reasons": "当日成交量确认",
        "info_gap_reasons": "",
        "missing_reasons": "",
        "risk_reasons": "",
        "risk_flags": [],
        "tomorrow_watch_plan": "仅人工观察",
        "invalid_conditions": "跌破关键位失效",
        "data_quality_flags": [],
    }
    result = {
        "trade_date": "2026-05-22",
        "next_trade_date": "2026-05-25",
        "next_trade_date_calendar": "weekday_proxy",
        "analysis_mode": "after_close",
        "mode": "demo",
        "status": "WATCHLIST_READY",
        "session_state": "AFTER_CLOSE",
        "candidate_source": "demo",
        "valid_for_trading_observation": "DEMO_ONLY",
        "final_view": "demo",
        "market_score": 70,
        "market": {},
        "themes": [],
        "recent_hot_themes": [],
        "industry_rank_available": False,
        "categories": {"A": [row], "B": [], "C": []},
        "evaluated_rows": [row],
        "quality": {"fallback_to_demo": False, "source_status": [], "warnings": []},
    }

    report_path = Path(write_after_close_report(result, str(tmp_path)))
    text = report_path.read_text(encoding="utf-8")
    table = parse_after_close_chip_volume_table(report_path)

    assert "## 6. 筹码与量价确认" in text
    assert not table.empty
    assert table.to_dict("records")[0]["代码"] == "600001"
    assert "建仓峰" in table.to_dict("records")[0]["峰型 proxy"]
