from __future__ import annotations


def score_stock(stock: dict, kline: list[dict], market_score: float, config: dict) -> dict:
    price_volume_score, pv_reasons = _price_volume_score(stock)
    trend_score, trend_reasons = _trend_score(stock, kline, config)
    theme_score, theme_reasons = _theme_score(stock)
    capital_score, capital_reasons = _capital_score(stock)
    risk_score, risk_reasons, risk_flags = _risk_score(stock, config)
    total_score = (
        market_score * 0.10
        + price_volume_score * 0.25
        + trend_score * 0.20
        + theme_score * 0.25
        + capital_score * 0.15
        + risk_score * 0.05
    )
    min_score = float(config.get("strategy", {}).get("min_total_score", 75))
    result = dict(stock)
    main_net_source = stock.get("fund_flow_source") or (stock.get("_sources") or {}).get("main_net", "")
    estimated_capital_flow = main_net_source == "estimated_from_big_order_net"
    capital_score_source = main_net_source or "unknown"
    if capital_score_source:
        capital_reasons.append(f"capital_score_source:{capital_score_source}")
    if estimated_capital_flow:
        capital_reasons.append("estimated_capital_flow")
    result.update(
        {
            "market_score": round(market_score, 2),
            "price_volume_score": round(price_volume_score, 2),
            "trend_score": round(trend_score, 2),
            "theme_score": round(theme_score, 2),
            "capital_score": round(capital_score, 2),
            "risk_score": round(risk_score, 2),
            "total_score": round(total_score, 2),
            "main_net_source": main_net_source,
            "capital_score_source": capital_score_source,
            "estimated_capital_flow": estimated_capital_flow,
            "score_reasons": pv_reasons + trend_reasons + theme_reasons + capital_reasons + risk_reasons,
            "risk_flags": risk_flags,
            "decision": "BUY_CANDIDATE" if total_score >= min_score and not risk_flags else "REJECT",
        }
    )
    return result


def rank_scored(scored: list[dict]) -> list[dict]:
    return sorted(
        scored,
        key=lambda row: (
            float(row.get("total_score", 0) or 0),
            -int(row.get("theme_rank") or 99),
            float(row.get("amount_wan", 0) or 0),
        ),
        reverse=True,
    )


def _price_volume_score(stock: dict) -> tuple[float, list[str]]:
    score = 45.0
    reasons: list[str] = []
    vol_ratio = float(stock.get("vol_ratio", 0) or 0)
    turnover = float(stock.get("turnover_pct", 0) or 0)
    amount = float(stock.get("amount_wan", 0) or 0)
    range_position = float(stock.get("range_position", 0) or 0)
    tail_pullback = float(stock.get("tail_pullback_pct", 0) or 0)

    if vol_ratio >= 1.2:
        score += 20
        reasons.append("vol_ratio_strong")
    elif vol_ratio >= 1:
        score += 10
        reasons.append("vol_ratio_acceptable")
    else:
        score -= 30
        reasons.append("vol_ratio_weak")

    if 5 <= turnover <= 12:
        score += 20
        reasons.append("turnover_ideal")
    elif 12 < turnover <= 18:
        score += 8
        reasons.append("turnover_high_but_usable")
    elif turnover > 20:
        score -= 20
        reasons.append("turnover_too_high")
    else:
        score -= 20
        reasons.append("turnover_too_low")

    if amount >= 20000:
        score += 15
        reasons.append("amount_strong")
    elif amount >= 15000:
        score += 8
        reasons.append("amount_acceptable")
    else:
        score -= 15
        reasons.append("amount_weak")

    if range_position >= 0.65:
        score += 15
        reasons.append("near_intraday_high")
    else:
        score -= 10
        reasons.append("not_near_intraday_high")

    if tail_pullback <= 1:
        score += 10
        reasons.append("tail_stable")
    elif tail_pullback > 2:
        score -= 25
        reasons.append("tail_pullback_penalty")

    return _clamp(score), reasons


def _trend_score(stock: dict, kline: list[dict], config: dict) -> tuple[float, list[str]]:
    if not kline:
        return 50.0, ["kline_missing_neutral"]
    score = 45.0
    reasons: list[str] = []
    last = kline[-1]
    close = float(last.get("close", stock.get("price", 0)) or 0)
    ma5 = float(last.get("ma5", close) or close)
    ma10 = float(last.get("ma10", close) or close)
    ma20 = float(last.get("ma20", close) or close)

    if close > ma5 > ma10 > ma20:
        score += 35
        reasons.append("ma_bullish_alignment")
    elif close > ma5 and close > ma10 and close > ma20:
        score += 22
        reasons.append("above_key_mas")
    else:
        score -= 25
        reasons.append("below_key_mas")

    if len(kline) >= 6:
        gain_5d = (close / float(kline[-6]["close"]) - 1) * 100
        if gain_5d <= float(config.get("trend", {}).get("max_5d_gain_pct", 30)):
            score += 10
            reasons.append("five_day_gain_controlled")
        else:
            score -= 20
            reasons.append("five_day_gain_overheated")
    if len(kline) >= 11:
        gain_10d = (close / float(kline[-11]["close"]) - 1) * 100
        if gain_10d <= float(config.get("trend", {}).get("max_10d_gain_pct", 45)):
            score += 8
            reasons.append("ten_day_gain_controlled")
        else:
            score -= 20
            reasons.append("ten_day_gain_overheated")

    upper_shadow = float(stock.get("upper_shadow_ratio", 0) or 0)
    if upper_shadow > float(config.get("trend", {}).get("max_upper_shadow_ratio", 0.45)):
        score -= 30
        reasons.append("upper_shadow_too_long")
    else:
        score += 8
        reasons.append("upper_shadow_acceptable")

    return _clamp(score), reasons


def _theme_score(stock: dict) -> tuple[float, list[str]]:
    tags = stock.get("theme_tags") or []
    rank = stock.get("theme_rank")
    same_theme_count = int(stock.get("same_theme_strong_count", 0) or 0)
    if not tags:
        return 10.0, ["no_theme_tags"]
    score = 55.0
    reasons = ["theme_tags_present"]
    if rank == 1:
        score += 35
        reasons.append("top1_theme")
    elif rank and rank <= 3:
        score += 30
        reasons.append("top3_theme")
    elif rank and rank <= 5:
        score += 18
        reasons.append("top5_theme")
    else:
        score += 5
        reasons.append("theme_rank_low")
    if same_theme_count >= 2:
        score += 10
        reasons.append("theme_has_group_effect")
    return _clamp(score), reasons


def _capital_score(stock: dict) -> tuple[float, list[str]]:
    score = 55.0
    reasons: list[str] = []
    big_order_raw = stock.get("big_order_net")
    main_net_raw = stock.get("main_net")
    big_order_missing = big_order_raw in (None, "")
    main_net_missing = main_net_raw in (None, "")
    big_order = float(big_order_raw or 0)
    main_net = float(main_net_raw or 0)
    if big_order_missing:
        score -= 12
        reasons.append("big_order_net_missing")
    elif big_order > 0:
        score += 20
        reasons.append("big_order_positive")
    else:
        score -= 25
        reasons.append("big_order_negative")
    if main_net_missing:
        score -= 12
        reasons.append("main_net_missing")
    elif main_net > 0:
        score += 20
        reasons.append("main_net_positive")
    else:
        score -= 25
        reasons.append("main_net_negative")
    if big_order > 1000 and main_net > 2000:
        score += 5
        reasons.append("capital_flow_strong")
    return _clamp(score), reasons


def _risk_score(stock: dict, config: dict) -> tuple[float, list[str], list[str]]:
    score = 90.0
    reasons: list[str] = ["base_risk_ok"]
    flags: list[str] = []
    flag_map = {
        "is_st": "st_stock",
        "is_suspended": "suspended",
        "is_new_stock": "new_stock",
        "is_limit_up": "limit_up_unavailable",
        "is_bj": "bj_stock",
    }
    for field, reason in flag_map.items():
        if stock.get(field):
            score -= 35
            flags.append(reason)
            reasons.append(reason)
    for reason in stock.get("_risk_unknown_reasons", []):
        score -= 20
        if reason not in flags:
            flags.append(reason)
        reasons.append(reason)
    if float(stock.get("tail_pullback_pct", 0) or 0) > float(config.get("filters", {}).get("max_tail_pullback_pct", 3.5)):
        score -= 25
        flags.append("tail_pullback_too_large")
        reasons.append("tail_pullback_too_large")
    if not stock.get("theme_tags"):
        score -= 40
        if stock.get("_allow_missing_theme"):
            reasons.append("theme_unavailable_score_discount")
        else:
            flags.append("theme_missing")
            reasons.append("theme_missing_risk")
    if float(stock.get("main_net", 0) or 0) < 0:
        score -= 30
        flags.append("capital_outflow")
        reasons.append("capital_outflow_risk")
    return _clamp(score), reasons, flags


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))
