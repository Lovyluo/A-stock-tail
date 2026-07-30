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
    gates = {
        "market": _gate(
            features.get("market_strength", 0) >= float(settings["min_market_strength"]),
            "market_too_weak",
        ),
        "industry": _gate(
            features.get("industry_relative_strength", 0) >= float(settings["min_industry_strength"])
            and features.get("industry_breadth", 0) >= float(settings["min_industry_breadth"]),
            "industry_resonance_missing",
        ),
        "liquidity": _gate(
            not bool(stock.get("suspended"))
            and float(stock.get("amount_wan") or 0) >= float(settings["min_amount_wan"])
            and float(settings["min_turnover_pct"])
            <= float(stock.get("turnover_pct") or 0)
            <= float(settings["max_turnover_pct"])
            and not bool(stock.get("is_limit_up") or stock.get("is_limit_down")),
            "liquidity_or_tradeability_failed",
        ),
        "news_risk": _gate(
            int(features.get("negative_announcement_count") or 0)
            <= int(settings["max_negative_announcements"]),
            "negative_announcement",
        ),
        "data_quality": _gate(
            not demo_paths
            and int(features.get("minute_bar_count") or 0) > 0
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
