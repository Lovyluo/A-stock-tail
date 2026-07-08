from __future__ import annotations


def evaluate_market_gate(market: dict) -> dict:
    reasons: list[str] = []
    reject_reasons: list[str] = []
    indices = market.get("indices", {})
    sh_change = _index_change(indices, "sh000001")
    cyb_change = _index_change(indices, "sz399006")
    positive_indices = sum(1 for item in indices.values() if float(item.get("change_pct", 0)) > 0)

    if positive_indices >= 2:
        reasons.append("major_indices_positive")
    if market.get("tail_30m_stable", False):
        reasons.append("index_tail_stable")
    else:
        reject_reasons.append("index_tail_dive")
    if int(market.get("hot_theme_count", 0)) >= 3:
        reasons.append("hot_themes_present")
    else:
        reject_reasons.append("no_clear_hot_theme")
    if float(market.get("northbound_net_yi", 0)) > -20:
        reasons.append("northbound_not_extreme_outflow")
    else:
        reject_reasons.append("northbound_extreme_outflow")
    if int(market.get("limit_down_count", 0)) <= 30:
        reasons.append("limit_down_count_controlled")
    else:
        reject_reasons.append("market_emotion_extreme")
    if sh_change <= -1.2:
        reject_reasons.append("sse_drop_too_large")
    if cyb_change <= -1.8:
        reject_reasons.append("chinext_drop_too_large")

    passed = len(reasons) >= 2 and not reject_reasons
    score = max(0, min(100, 45 + len(reasons) * 10 - len(reject_reasons) * 18))
    return {
        "pass": passed,
        "score": score,
        "reasons": reasons,
        "reject_reasons": reject_reasons,
    }


def initial_filter(stock: dict, config: dict) -> dict:
    rules = config.get("filters", {})
    reject_reasons: list[str] = []
    reasons: list[str] = []

    allowed_prefixes = tuple(str(item) for item in rules.get("allowed_code_prefixes", []) if str(item))
    if rules.get("enforce_allowed_code_prefixes") and allowed_prefixes:
        code = str(stock.get("code", "")).zfill(6)
        if not code.startswith(allowed_prefixes):
            reject_reasons.append("code_prefix_not_allowed")

    name_upper = str(stock.get("name", "")).strip().upper()
    if stock.get("is_st") or name_upper.startswith(("ST", "*ST")):
        reject_reasons.append("st_stock")
    if stock.get("is_suspended") or float(stock.get("price", 0) or 0) <= 0:
        reject_reasons.append("suspended")
    if stock.get("is_new_stock") or int(stock.get("listed_days", 9999) or 9999) < 60:
        reject_reasons.append("new_stock")
    if stock.get("is_bj") or str(stock.get("code", "")).startswith(("8", "4")):
        reject_reasons.append("bj_stock")
    if stock.get("is_limit_up") or _near(float(stock.get("price", 0) or 0), float(stock.get("limit_up", -1) or -1)):
        reject_reasons.append("limit_up_unavailable")

    _range_check(stock, "price", rules.get("min_price", 3), rules.get("max_price", 80), "price", reject_reasons, reasons)
    _range_check(stock, "change_pct", rules.get("min_change_pct", 3), rules.get("max_change_pct", 7), "change_pct", reject_reasons, reasons)
    _min_check(stock, "vol_ratio", rules.get("min_vol_ratio", 1), "vol_ratio", reject_reasons, reasons)
    _range_check(stock, "turnover_pct", rules.get("min_turnover_pct", 5), rules.get("max_turnover_pct", 18), "turnover_pct", reject_reasons, reasons)
    _min_check(stock, "amount_wan", rules.get("min_amount_wan", 15000), "amount_wan", reject_reasons, reasons)
    _range_check(stock, "float_mcap_yi", rules.get("min_float_mcap_yi", 30), rules.get("max_float_mcap_yi", 250), "float_mcap_yi", reject_reasons, reasons)

    return {
        "pass": not reject_reasons,
        "reasons": reasons,
        "reject_reasons": reject_reasons,
    }


def evaluate_tail_stability(stock: dict, config: dict) -> dict:
    max_pullback = float(config.get("filters", {}).get("max_tail_pullback_pct", 3.5))
    pullback = float(stock.get("tail_pullback_pct", 0) or 0)
    if pullback > max_pullback:
        return {
            "pass": False,
            "reasons": [],
            "reject_reasons": ["tail_pullback_too_large"],
        }
    return {
        "pass": True,
        "reasons": ["tail_stable"],
        "reject_reasons": [],
    }


def _index_change(indices: dict, code: str) -> float:
    return float((indices.get(code) or {}).get("change_pct", 0) or 0)


def _near(left: float, right: float, tolerance: float = 0.005) -> bool:
    return right > 0 and abs(left - right) <= tolerance


def _range_check(stock: dict, field: str, min_value: float, max_value: float, label: str, rejects: list[str], reasons: list[str]) -> None:
    value = float(stock.get(field, 0) or 0)
    if value < float(min_value):
        rejects.append(f"{label}_below_min")
    elif value > float(max_value):
        rejects.append(f"{label}_above_max")
    else:
        reasons.append(f"{label}_ok")


def _min_check(stock: dict, field: str, min_value: float, label: str, rejects: list[str], reasons: list[str]) -> None:
    value = float(stock.get(field, 0) or 0)
    if value < float(min_value):
        rejects.append(f"{label}_below_min")
    else:
        reasons.append(f"{label}_ok")
