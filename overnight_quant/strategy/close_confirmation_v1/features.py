from __future__ import annotations

from datetime import datetime, time
import math
from statistics import mean, pstdev
from typing import Any

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import parse_cn_datetime
from overnight_quant.strategy.chip_volume import calculate_chip_metrics


SCORE_WEIGHTS = {
    "market_confirmation": 15,
    "industry_confirmation": 20,
    "stock_relative_strength": 15,
    "price_volume_confirmation": 25,
    "catalyst_quality": 10,
    "chip_structure_proxy": 15,
}


def build_close_confirmation_features(
    stock: dict[str, Any],
    *,
    decision_time: str | datetime,
) -> dict[str, Any]:
    decision = parse_cn_datetime(decision_time)
    if decision is None:
        raise ValueError("decision_time_invalid")
    bars = bars_available_at(stock.get("intraday_bars") or [], decision)
    market = stock.get("market") or {}
    industry = stock.get("industry") or {}
    news = news_available_at(stock.get("news") or [], decision)
    current_price = _bar_price(bars[-1]) if bars else 0.0
    vwap_values = _rolling_vwap(bars)
    latest_vwap = vwap_values[-1] if vwap_values else 0.0
    vwap_position_pct = _pct(current_price, latest_vwap)
    vwap_slope_pct = _pct(vwap_values[-1], vwap_values[-6]) if len(vwap_values) >= 6 else 0.0
    support_score = _tail_support_score(bars, latest_vwap)
    abnormal_volume_z = _abnormal_volume_zscore(bars)
    order_imbalance = _order_imbalance(bars[-1] if bars else {})
    market_strength = _market_strength(market)
    industry_strength = _industry_strength(industry)
    industry_breadth_value = _optional_float(industry.get("breadth_ratio"))
    industry_breadth = (
        _clamp(industry_breadth_value)
        if industry_breadth_value is not None
        else None
    )
    stock_return = _optional_float(stock.get("change_pct"))
    if stock_return is None and current_price > 0:
        prev_close = _optional_float(stock.get("prev_close"))
        stock_return = _pct(current_price, prev_close) if prev_close else None
    industry_return = _optional_float(industry.get("change_pct"))
    relative_to_industry = (
        stock_return - industry_return
        if stock_return is not None and industry_return is not None
        else None
    )
    news_source_ready = bool(stock.get("_news_source_ready", bool(news)))
    catalyst = _catalyst_score(news) if news_source_ready else None
    negative_announcements = [
        item for item in news if _is_negative_news(item) and str(item.get("kind") or "").lower() == "announcement"
    ]
    daily_bars = stock.get("daily_bars") or []
    fund_flow = stock.get("fund_flow") or []
    chip_data_ready = len(_valid_daily_bars(daily_bars)) >= 60 and _fund_flow_available(fund_flow)
    chip = (
        calculate_chip_metrics(daily_bars, current_price)
        if chip_data_ready and current_price
        else {}
    )
    chip_proxy = _chip_proxy_score(chip, fund_flow) if chip_data_ready else None
    minute_data_ready = len(bars) >= 12 and bool(
        bars
        and parse_cn_datetime(bars[-1].get("_pit_time"))
        and parse_cn_datetime(bars[-1].get("_pit_time")).replace(second=0, microsecond=0)
        >= decision.replace(second=0, microsecond=0)
    )
    price_volume = (
        _price_volume_score(
            vwap_position_pct,
            vwap_slope_pct,
            support_score,
            abnormal_volume_z,
            order_imbalance,
        )
        if minute_data_ready
        else None
    )
    market_data_ready = market_strength is not None
    industry_data_ready = industry_strength is not None and industry_breadth is not None
    quote_data_ready = _quote_fields_available(stock)
    industry_confirmation = (
        _clamp(0.55 * industry_strength + 0.45 * industry_breadth)
        if industry_data_ready
        else None
    )
    stock_relative_strength = (
        _clamp(0.5 + relative_to_industry / 10.0)
        if relative_to_industry is not None
        else None
    )
    return {
        "decision_time": decision.isoformat(timespec="seconds"),
        "minute_bar_count": len(bars),
        "last_bar_time": _bar_time_text(bars[-1]) if bars else "",
        "current_price": round(current_price, 4),
        "vwap": round(latest_vwap, 4),
        "vwap_position_pct": round(vwap_position_pct, 4),
        "vwap_slope_pct": round(vwap_slope_pct, 4),
        "tail_support_score": round(support_score, 4),
        "abnormal_volume_z": round(abnormal_volume_z, 4),
        "extreme_order_imbalance": round(order_imbalance, 4),
        "market_strength": _round_optional(market_strength),
        "industry_relative_strength": _round_optional(industry_strength),
        "industry_breadth": _round_optional(industry_breadth),
        "stock_relative_to_industry_pct": _round_optional(relative_to_industry),
        "catalyst_score": _round_optional(catalyst),
        "negative_announcement_count": len(negative_announcements),
        "eligible_news_count": len(news),
        "chip_avg_cost_20d": _optional_float(chip.get("chip_avg_cost_20d")),
        "chip_avg_cost_60d": _optional_float(chip.get("chip_avg_cost_60d")),
        "overhead_pressure_ratio": _optional_float(chip.get("overhead_pressure_ratio")),
        "downside_support_ratio": _optional_float(chip.get("downside_support_ratio")),
        "main_force_chip_proxy": _optional_float(chip.get("main_force_chip_proxy")),
        "feature_availability": {
            "market": market_data_ready,
            "industry": industry_data_ready,
            "quote": quote_data_ready,
            "minute": minute_data_ready,
            "chip": chip_data_ready,
            "news_source": news_source_ready,
        },
        "component_inputs": {
            "market_confirmation": market_strength,
            "industry_confirmation": industry_confirmation,
            "stock_relative_strength": stock_relative_strength,
            "price_volume_confirmation": price_volume,
            "catalyst_quality": catalyst,
            "chip_structure_proxy": chip_proxy,
        },
        "proxy_disclosure": [
            "main_force_fund_flow_is_proxy",
            "chip_cost_is_price_volume_proxy_not_holder_cost",
        ],
        "feature_reasons": _feature_reasons(
            vwap_position_pct,
            vwap_slope_pct,
            support_score,
            abnormal_volume_z,
            order_imbalance,
            relative_to_industry,
            catalyst,
        ),
    }


def bars_available_at(rows: list[dict[str, Any]], decision_time: datetime) -> list[dict[str, Any]]:
    selected = []
    for row in rows:
        stamp = _bar_datetime(row, decision_time)
        if stamp and stamp <= decision_time:
            selected.append({**row, "_pit_time": stamp.isoformat(timespec="seconds")})
    return sorted(selected, key=lambda item: item["_pit_time"])


def news_available_at(rows: list[dict[str, Any]], decision_time: datetime) -> list[dict[str, Any]]:
    selected = []
    for row in rows:
        published = parse_cn_datetime(row.get("published_at"))
        available = parse_cn_datetime(row.get("available_at") or row.get("published_at"))
        if published is None or available is None:
            continue
        if published <= decision_time and available <= decision_time:
            selected.append(dict(row))
    return selected


def _rolling_vwap(rows: list[dict[str, Any]]) -> list[float]:
    total_amount = 0.0
    total_volume = 0.0
    values = []
    for row in rows:
        volume = max(0.0, _float(row.get("volume") or row.get("vol"), 0.0))
        price = _bar_price(row)
        amount = _float(row.get("amount"), 0.0) or volume * price
        total_amount += amount
        total_volume += volume
        values.append(total_amount / total_volume if total_volume else price)
    return values


def _tail_support_score(rows: list[dict[str, Any]], vwap: float) -> float:
    if len(rows) < 5 or vwap <= 0:
        return 0.0
    tail = rows[-5:]
    lows = [_float(item.get("low"), _bar_price(item)) for item in tail]
    closes = [_bar_price(item) for item in tail]
    held = sum(1 for low in lows if low >= vwap * 0.995) / len(lows)
    recovery = _clamp(0.5 + _pct(closes[-1], min(lows)) / 4.0)
    return _clamp(0.65 * held + 0.35 * recovery)


def _abnormal_volume_zscore(rows: list[dict[str, Any]]) -> float:
    volumes = [_float(item.get("volume") or item.get("vol"), 0.0) for item in rows]
    if len(volumes) < 12:
        return 0.0
    baseline = volumes[-32:-2] if len(volumes) >= 34 else volumes[:-2]
    recent = mean(volumes[-2:])
    deviation = pstdev(baseline) if len(baseline) >= 2 else 0.0
    return (recent - mean(baseline)) / deviation if deviation > 0 else 0.0


def _order_imbalance(row: dict[str, Any]) -> float:
    bid = sum(_float(row.get(f"bid_vol{i}"), 0.0) for i in range(1, 6))
    ask = sum(_float(row.get(f"ask_vol{i}"), 0.0) for i in range(1, 6))
    if bid + ask <= 0:
        return 0.0
    return (bid - ask) / (bid + ask)


def _market_strength(market: dict[str, Any]) -> float | None:
    index_change = _optional_float(market.get("index_change_pct"))
    breadth_value = _optional_float(market.get("breadth_ratio"))
    if index_change is None or breadth_value is None:
        return None
    breadth = _clamp(breadth_value)
    return _clamp(0.5 + index_change / 4.0) * 0.55 + breadth * 0.45


def _industry_strength(industry: dict[str, Any]) -> float | None:
    change = _optional_float(industry.get("change_pct"))
    relative = _optional_float(industry.get("relative_strength_pct"))
    if relative is None:
        relative = change
    if relative is None:
        return None
    return _clamp(0.5 + relative / 6.0)


def _price_volume_score(
    position_pct: float,
    slope_pct: float,
    support: float,
    volume_z: float,
    imbalance: float,
) -> float:
    position = _clamp(0.5 + position_pct / 3.0)
    slope = _clamp(0.5 + slope_pct / 1.5)
    volume = _clamp(0.5 + volume_z / 4.0)
    orders = _clamp(0.5 + imbalance)
    return _clamp(0.25 * position + 0.2 * slope + 0.25 * support + 0.2 * volume + 0.1 * orders)


def _catalyst_score(news: list[dict[str, Any]]) -> float:
    if not news:
        return 0.0
    positive = sum(1 for item in news if _is_positive_news(item))
    negative = sum(1 for item in news if _is_negative_news(item))
    sourced = sum(1 for item in news if item.get("source"))
    return _clamp(0.45 + 0.12 * positive - 0.2 * negative + 0.02 * sourced)


def _chip_proxy_score(chip: dict[str, Any], fund_flow: list[dict[str, Any]]) -> float | None:
    pressure = _optional_float(chip.get("overhead_pressure_ratio"))
    support = _optional_float(chip.get("downside_support_ratio"))
    if pressure is None or support is None:
        return None
    flow_values = [_float(item.get("main_net"), 0.0) for item in fund_flow if item.get("main_net") is not None]
    if not flow_values:
        return None
    flow = _clamp(0.5 + sum(flow_values[-5:]) / max(1.0, sum(abs(v) for v in flow_values[-5:])) * 0.5)
    return _clamp(0.45 * support + 0.3 * (1.0 - pressure) + 0.25 * flow)


def _feature_reasons(
    vwap_position: float,
    vwap_slope: float,
    support: float,
    volume_z: float,
    imbalance: float,
    relative: float | None,
    catalyst: float | None,
) -> list[str]:
    return [
        f"vwap_position_pct:{vwap_position:.2f}",
        f"vwap_slope_pct:{vwap_slope:.2f}",
        f"tail_support:{support:.2f}",
        f"abnormal_volume_z:{volume_z:.2f}",
        f"order_imbalance:{imbalance:.2f}",
        f"stock_vs_industry_pct:{_metric_text(relative)}",
        f"catalyst_quality:{_metric_text(catalyst)}",
    ]


def _bar_datetime(row: dict[str, Any], decision: datetime) -> datetime | None:
    value = row.get("datetime") or row.get("time") or row.get("event_time")
    text = str(value or "")
    if len(text) <= 8 and ":" in text:
        parsed_time = _parse_clock(text)
        return datetime.combine(decision.date(), parsed_time, tzinfo=CN_TZ) if parsed_time else None
    return parse_cn_datetime(value)


def _bar_time_text(row: dict[str, Any]) -> str:
    return str(row.get("_pit_time") or row.get("datetime") or row.get("time") or "")


def _parse_clock(value: str) -> time | None:
    parts = str(value).split(":")
    try:
        return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
    except (TypeError, ValueError):
        return None


def _bar_price(row: dict[str, Any]) -> float:
    return _float(row.get("price") or row.get("close") or row.get("open"), 0.0)


def _is_positive_news(item: dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    return any(word in text for word in ("中标", "增持", "回购", "获批", "增长", "突破", "支持"))


def _is_negative_news(item: dict[str, Any]) -> bool:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    return any(word in text for word in ("立案", "处罚", "减持", "退市", "终止", "亏损", "问询"))


def _pct(value: float, base: float) -> float:
    return (value / base - 1.0) * 100.0 if base else 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _round_optional(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _metric_text(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "missing"


def _valid_daily_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if (_optional_float(row.get("close")) or 0) > 0
        and (_optional_float(row.get("volume", row.get("vol"))) or 0) > 0
    ]


def _fund_flow_available(rows: list[dict[str, Any]]) -> bool:
    return any(
        any(
            _optional_float(row.get(field)) is not None
            for field in ("main_net", "large_net", "super_net")
        )
        for row in rows
    )


def _quote_fields_available(stock: dict[str, Any]) -> bool:
    return bool(
        (_optional_float(stock.get("price")) or 0) > 0
        and (_optional_float(stock.get("prev_close")) or 0) > 0
        and _optional_float(stock.get("amount_wan")) is not None
        and _optional_float(stock.get("turnover_pct")) is not None
        and "suspended" in stock
        and "is_limit_up" in stock
        and "is_limit_down" in stock
    )


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))
