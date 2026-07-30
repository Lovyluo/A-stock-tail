from __future__ import annotations

from datetime import datetime
from typing import Any

from overnight_quant.data.point_in_time import demo_field_paths, parse_cn_datetime


DEFAULT_GATE_CONFIG = {
    "min_market_strength": 0.35,
    "min_industry_strength": 0.4,
    "min_industry_breadth": 0.45,
    "min_amount_wan": 15000,
    "min_turnover_pct": 1.0,
    "max_turnover_pct": 25.0,
    "require_1450_minute": True,
    "max_negative_announcements": 0,
}


def evaluate_hard_gates(
    stock: dict[str, Any],
    features: dict[str, Any],
    *,
    decision_time: str | datetime,
    mode: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = {**DEFAULT_GATE_CONFIG, **(config or {})}
    decision = parse_cn_datetime(decision_time)
    last_bar = parse_cn_datetime(features.get("last_bar_time"))
    minute_complete = bool(
        decision
        and last_bar
        and last_bar.date() == decision.date()
        and last_bar.time().replace(second=0, microsecond=0)
        >= decision.time().replace(second=0, microsecond=0)
    )
    demo_paths = demo_field_paths(stock) if str(mode).lower() in {"live", "shadow", "paper", "replay"} else []
    availability = features.get("feature_availability") or {}
    market_available = bool(availability.get("market"))
    industry_available = bool(availability.get("industry"))
    quote_available = bool(availability.get("quote"))
    minute_available = bool(availability.get("minute"))
    chip_available = bool(availability.get("chip"))
    news_source_available = bool(availability.get("news_source"))
    gates = {
        "market": _gate(
            market_available
            and _at_least(features.get("market_strength"), settings["min_market_strength"]),
            "market_data_missing" if not market_available else "market_too_weak",
        ),
        "industry": _gate(
            industry_available
            and _at_least(features.get("industry_relative_strength"), settings["min_industry_strength"])
            and _at_least(features.get("industry_breadth"), settings["min_industry_breadth"]),
            "industry_data_missing" if not industry_available else "industry_resonance_missing",
        ),
        "liquidity": _gate(
            quote_available
            and not bool(stock.get("suspended"))
            and float(stock.get("amount_wan") or 0) >= float(settings["min_amount_wan"])
            and float(settings["min_turnover_pct"])
            <= float(stock.get("turnover_pct") or 0)
            <= float(settings["max_turnover_pct"])
            and not bool(stock.get("is_limit_up") or stock.get("is_limit_down")),
            "quote_data_missing" if not quote_available else "liquidity_or_tradeability_failed",
        ),
        "news_risk": _gate(
            news_source_available
            and int(features.get("negative_announcement_count") or 0)
            <= int(settings["max_negative_announcements"]),
            "news_source_unavailable" if not news_source_available else "negative_announcement",
        ),
        "data_quality": _gate(
            not demo_paths
            and market_available
            and industry_available
            and quote_available
            and minute_available
            and chip_available
            and news_source_available
            and (minute_complete or not settings.get("require_1450_minute", True)),
            "point_in_time_data_incomplete",
        ),
    }
    reject_reasons = [item["reason"] for item in gates.values() if not item["pass"]]
    if demo_paths:
        reject_reasons.append("demo_data_prohibited")
    return {
        "all_pass": not reject_reasons,
        "gates": gates,
        "reject_reasons": list(dict.fromkeys(reject_reasons)),
        "demo_field_count": len(demo_paths),
        "demo_field_paths": demo_paths,
        "minute_complete_1450": minute_complete,
    }


def _gate(passed: bool, reason: str) -> dict[str, Any]:
    return {"pass": bool(passed), "reason": "" if passed else reason}


def _at_least(value: Any, minimum: Any) -> bool:
    try:
        return value is not None and float(value) >= float(minimum)
    except (TypeError, ValueError):
        return False
