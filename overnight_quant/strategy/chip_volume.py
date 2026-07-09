from __future__ import annotations

from statistics import median
from typing import Any


DEFAULT_CHIP_VOLUME_CONFIG = {
    "enabled": True,
    "profile_lookback_days": 60,
    "avg_cost_windows": [20, 60],
    "bucket_pct": 1.0,
    "high_volume_prev_days": 3,
    "volume_confirm_ratio": 1.2,
    "max_confidence_bonus": 8,
    "max_confidence_penalty": -10,
}

PEAK_TYPES = {"accumulation", "washout", "markup", "distribution", "neutral"}


def calculate_volume_signals(daily_bars: list[dict]) -> dict:
    return _calculate_volume_signals(daily_bars, DEFAULT_CHIP_VOLUME_CONFIG)


def build_price_volume_profile(daily_bars: list[dict], lookback: int = 60, bucket_pct: float = 1.0) -> dict:
    bars = _valid_bars(daily_bars)[-max(1, int(lookback)) :]
    if not bars:
        return {
            "levels": [],
            "total_volume": 0.0,
            "max_volume_price": 0.0,
            "bucket_size": 0.0,
            "lookback": lookback,
            "bucket_pct": bucket_pct,
            "reasons": ["chip_volume_data_missing"],
        }

    prices = [_typical_price(bar) for bar in bars]
    base_price = median(prices) if prices else 0.0
    bucket_size = max(base_price * max(float(bucket_pct), 0.1) / 100.0, 0.01)
    buckets: dict[float, float] = {}
    for bar, price in zip(bars, prices):
        bucket = round(round(price / bucket_size) * bucket_size, 4)
        buckets[bucket] = buckets.get(bucket, 0.0) + _volume(bar)

    total = sum(buckets.values())
    levels = [
        {"price": price, "volume": volume, "volume_ratio": round(_safe_div(volume, total), 4)}
        for price, volume in sorted(buckets.items())
    ]
    max_level = max(levels, key=lambda item: item["volume"], default={"price": 0.0})
    return {
        "levels": levels,
        "total_volume": round(total, 4),
        "max_volume_price": max_level["price"],
        "bucket_size": round(bucket_size, 4),
        "lookback": len(bars),
        "bucket_pct": bucket_pct,
        "reasons": [],
    }


def calculate_chip_metrics(daily_bars: list[dict], current_price: float) -> dict:
    return _calculate_chip_metrics(daily_bars, current_price, DEFAULT_CHIP_VOLUME_CONFIG)


def classify_chip_peak(stock: dict, daily_bars: list[dict], fund_flow: list[dict] | None = None) -> dict:
    config = _merged_config(stock.get("_chip_volume_config") if isinstance(stock, dict) else None)
    volume = _calculate_volume_signals(daily_bars, config)
    current_price = _current_price(stock, daily_bars)
    metrics = _calculate_chip_metrics(daily_bars, current_price, config)
    main_force, fund_reasons = _main_force_proxy(stock, fund_flow)
    reasons = list(dict.fromkeys(volume.get("reasons", []) + metrics.get("reasons", []) + fund_reasons))

    if "chip_volume_data_missing" in reasons:
        return {
            "peak_type": "neutral",
            "main_force_chip_proxy": main_force,
            "confidence_delta": 0,
            "reasons": reasons,
        }

    vs_cost = float(metrics.get("current_vs_chip_cost_pct", 0.0) or 0.0)
    overhead = float(metrics.get("overhead_pressure_ratio", 0.0) or 0.0)
    support = float(metrics.get("downside_support_ratio", 0.0) or 0.0)
    tail_pullback = _to_float(stock.get("tail_pullback_pct"), 0.0)
    upper_shadow = _to_float(stock.get("upper_shadow_ratio"), 0.0)
    today_confirm = bool(volume.get("today_volume_confirm"))
    prev_high = bool(volume.get("prev_day_high_volume"))
    above_cost = vs_cost >= 2.0
    near_cost = -3.0 <= vs_cost <= 5.0
    extended = vs_cost >= 8.0
    weak_tail = tail_pullback >= 2.0 or upper_shadow >= 0.45

    if today_confirm and (extended or weak_tail or overhead >= 0.55) and main_force < 0:
        peak_type = "distribution"
    elif today_confirm and above_cost and main_force >= 0 and overhead <= 0.58:
        peak_type = "markup"
    elif prev_high and not today_confirm and near_cost and support >= 0.35 and main_force <= 5:
        peak_type = "washout"
    elif (prev_high or today_confirm) and near_cost and main_force >= 0 and overhead <= 0.55:
        peak_type = "accumulation"
    else:
        peak_type = "neutral"

    reasons.append(f"chip_peak_{peak_type}")
    if overhead >= 0.55:
        reasons.append("overhead_pressure_high")
    if support >= 0.45:
        reasons.append("downside_support_visible")
    return {
        "peak_type": peak_type,
        "main_force_chip_proxy": main_force,
        "confidence_delta": 0,
        "reasons": list(dict.fromkeys(reasons)),
    }


def build_chip_volume_confidence(
    stock: dict,
    daily_bars: list[dict],
    fund_flow: list[dict] | None = None,
) -> dict:
    config = _merged_config(stock.get("_chip_volume_config") if isinstance(stock, dict) else None)
    if not config.get("enabled", True):
        return _empty_confidence("chip_volume_disabled")

    volume = _calculate_volume_signals(daily_bars, config)
    current_price = _current_price(stock, daily_bars)
    metrics = _calculate_chip_metrics(daily_bars, current_price, config)
    peak = classify_chip_peak({**stock, "_chip_volume_config": config}, daily_bars, fund_flow)
    reasons = list(dict.fromkeys(volume.get("reasons", []) + metrics.get("reasons", []) + peak.get("reasons", [])))

    if "chip_volume_data_missing" in reasons:
        delta = 0
    else:
        peak_type = str(peak.get("peak_type", "neutral"))
        delta = _base_peak_delta(peak_type)
        if peak_type != "neutral":
            if volume.get("today_volume_confirm"):
                delta += 2
            elif volume.get("prev_day_high_volume"):
                delta += 1
            else:
                delta -= 1
            main_force = float(peak.get("main_force_chip_proxy", 0.0) or 0.0)
            if main_force > 0:
                delta += 1
            elif main_force < 0:
                delta -= 3
            if float(metrics.get("overhead_pressure_ratio", 0.0) or 0.0) >= 0.55:
                delta -= 2
            if float(metrics.get("downside_support_ratio", 0.0) or 0.0) >= 0.45:
                delta += 1

    delta = _clamp_int(
        delta,
        int(config.get("max_confidence_penalty", -10)),
        int(config.get("max_confidence_bonus", 8)),
    )
    return {
        "prev_day_high_volume": bool(volume.get("prev_day_high_volume")),
        "today_volume_confirm": bool(volume.get("today_volume_confirm")),
        "volume_expansion_ratio_3d": round(float(volume.get("volume_expansion_ratio_3d", 0.0) or 0.0), 3),
        "volume_signal": volume.get("volume_signal", "volume_data_missing"),
        "chip_avg_cost_20d": round(float(metrics.get("chip_avg_cost_20d", 0.0) or 0.0), 3),
        "chip_avg_cost_60d": round(float(metrics.get("chip_avg_cost_60d", 0.0) or 0.0), 3),
        "current_vs_chip_cost_pct": round(float(metrics.get("current_vs_chip_cost_pct", 0.0) or 0.0), 3),
        "overhead_pressure_ratio": round(float(metrics.get("overhead_pressure_ratio", 0.0) or 0.0), 3),
        "downside_support_ratio": round(float(metrics.get("downside_support_ratio", 0.0) or 0.0), 3),
        "main_force_chip_proxy": round(float(peak.get("main_force_chip_proxy", 0.0) or 0.0), 3),
        "peak_type": peak.get("peak_type", "neutral"),
        "confidence_delta": delta,
        "reasons": reasons or ["chip_volume_neutral"],
    }


def _calculate_volume_signals(daily_bars: list[dict], config: dict) -> dict:
    bars = _valid_bars(daily_bars)
    if len(bars) < 2:
        return {
            "prev_day_high_volume": False,
            "today_volume_confirm": False,
            "volume_expansion_ratio_3d": 0.0,
            "volume_signal": "volume_data_missing",
            "reasons": ["chip_volume_data_missing"],
        }

    today_volume = _volume(bars[-1])
    prev_volume = _volume(bars[-2])
    high_days = max(1, int(config.get("high_volume_prev_days", 3)))
    prior_for_prev = [_volume(bar) for bar in bars[-(high_days + 2) : -2]]
    prior_for_today = [_volume(bar) for bar in bars[-4:-1]]
    if not prior_for_today or today_volume <= 0 or prev_volume <= 0:
        return {
            "prev_day_high_volume": False,
            "today_volume_confirm": False,
            "volume_expansion_ratio_3d": 0.0,
            "volume_signal": "volume_data_missing",
            "reasons": ["chip_volume_data_missing"],
        }

    prev_day_high = bool(prior_for_prev) and prev_volume > max(prior_for_prev)
    avg_3d = sum(prior_for_today) / len(prior_for_today)
    expansion = _safe_div(today_volume, avg_3d)
    today_confirm = expansion >= float(config.get("volume_confirm_ratio", 1.2))
    reasons: list[str] = []
    if prev_day_high:
        reasons.append("prev_day_high_volume")
    if today_confirm:
        reasons.append("today_volume_confirm")
    if not reasons:
        reasons.append("volume_not_confirmed")
    signal = "today_confirmed" if today_confirm else ("prev_high_volume" if prev_day_high else "weak_volume")
    return {
        "prev_day_high_volume": prev_day_high,
        "today_volume_confirm": today_confirm,
        "volume_expansion_ratio_3d": expansion,
        "volume_signal": signal,
        "reasons": reasons,
    }


def _calculate_chip_metrics(daily_bars: list[dict], current_price: float, config: dict) -> dict:
    bars = _valid_bars(daily_bars)
    if not bars or current_price <= 0:
        return _missing_chip_metrics()

    windows = list(config.get("avg_cost_windows") or [20, 60])
    cost_by_window = {int(window): _volume_weighted_price(bars[-int(window) :]) for window in windows}
    cost_20 = cost_by_window.get(20) or _volume_weighted_price(bars[-20:])
    cost_60 = cost_by_window.get(60) or _volume_weighted_price(bars[-60:])
    if cost_20 <= 0 and cost_60 <= 0:
        return _missing_chip_metrics()

    reference_cost = cost_20 or cost_60
    profile = build_price_volume_profile(
        bars,
        lookback=int(config.get("profile_lookback_days", 60)),
        bucket_pct=float(config.get("bucket_pct", 1.0)),
    )
    levels = profile.get("levels") or []
    total = float(profile.get("total_volume", 0.0) or 0.0)
    overhead = sum(float(level.get("volume", 0.0) or 0.0) for level in levels if float(level.get("price", 0.0) or 0.0) > current_price)
    support = sum(float(level.get("volume", 0.0) or 0.0) for level in levels if float(level.get("price", 0.0) or 0.0) <= current_price)
    reasons: list[str] = []
    if len(bars) < max(windows):
        reasons.append("chip_history_short")
    return {
        "chip_avg_cost_20d": cost_20,
        "chip_avg_cost_60d": cost_60,
        "current_vs_chip_cost_pct": _pct_change(current_price, reference_cost),
        "overhead_pressure_ratio": _safe_div(overhead, total),
        "downside_support_ratio": _safe_div(support, total),
        "price_volume_profile": profile,
        "reasons": reasons,
    }


def _main_force_proxy(stock: dict, fund_flow: list[dict] | None = None) -> tuple[float, list[str]]:
    reasons: list[str] = []
    values: list[float] = []
    for row in fund_flow or []:
        values.extend(
            value
            for value in (
                _optional_float(row.get("main_net")),
                _optional_float(row.get("large_net")),
                _optional_float(row.get("super_net")),
            )
            if value is not None
        )
    if values:
        total = sum(values)
        reasons.append("main_force_from_fund_flow")
    else:
        main = _optional_float(stock.get("main_net"))
        big = _optional_float(stock.get("big_order_net"))
        if main is None and big is None:
            return 0.0, ["chip_volume_data_missing", "main_force_proxy_missing"]
        total = sum(value for value in (main, big) if value is not None)
        reasons.append("main_force_from_candidate_fields")

    if total > 0:
        proxy = min(100.0, 20.0 + abs(total) ** 0.5 / 100.0)
    elif total < 0:
        proxy = -min(100.0, 20.0 + abs(total) ** 0.5 / 100.0)
    else:
        proxy = 0.0
    if proxy > 0:
        reasons.append("main_force_chip_proxy_positive")
    elif proxy < 0:
        reasons.append("main_force_chip_proxy_negative")
    else:
        reasons.append("main_force_chip_proxy_neutral")
    return proxy, reasons


def _base_peak_delta(peak_type: str) -> int:
    return {
        "accumulation": 3,
        "washout": 2,
        "markup": 5,
        "distribution": -6,
        "neutral": 0,
    }.get(peak_type, 0)


def _current_price(stock: dict, daily_bars: list[dict]) -> float:
    for key in ("price", "close_price", "current_price", "close"):
        value = _optional_float(stock.get(key))
        if value and value > 0:
            return value
    bars = _valid_bars(daily_bars)
    return _typical_price(bars[-1]) if bars else 0.0


def _valid_bars(daily_bars: list[dict]) -> list[dict]:
    rows = []
    for bar in daily_bars or []:
        close = _optional_float(bar.get("close"))
        volume = _optional_float(bar.get("volume", bar.get("vol")))
        if close is None or close <= 0 or volume is None or volume <= 0:
            continue
        rows.append(bar)
    return rows


def _volume_weighted_price(bars: list[dict]) -> float:
    valid = _valid_bars(bars)
    total_volume = sum(_volume(bar) for bar in valid)
    if not valid or total_volume <= 0:
        return 0.0
    return sum(_typical_price(bar) * _volume(bar) for bar in valid) / total_volume


def _typical_price(bar: dict) -> float:
    close = _to_float(bar.get("close"), 0.0)
    high = _to_float(bar.get("high"), close)
    low = _to_float(bar.get("low"), close)
    if high <= 0 or low <= 0:
        return close
    return (high + low + close) / 3.0


def _volume(bar: dict) -> float:
    return _to_float(bar.get("volume", bar.get("vol")), 0.0)


def _optional_float(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any, default: float = 0.0) -> float:
    result = _optional_float(value)
    return default if result is None else result


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _pct_change(value: float, base: float) -> float:
    return (value / base - 1.0) * 100.0 if base else 0.0


def _clamp_int(value: float, lower: int, upper: int) -> int:
    return int(max(lower, min(upper, round(value))))


def _merged_config(config: dict | None) -> dict:
    merged = dict(DEFAULT_CHIP_VOLUME_CONFIG)
    if isinstance(config, dict):
        merged.update(config)
    return merged


def _missing_chip_metrics() -> dict:
    return {
        "chip_avg_cost_20d": 0.0,
        "chip_avg_cost_60d": 0.0,
        "current_vs_chip_cost_pct": 0.0,
        "overhead_pressure_ratio": 0.0,
        "downside_support_ratio": 0.0,
        "price_volume_profile": {"levels": [], "total_volume": 0.0},
        "reasons": ["chip_volume_data_missing"],
    }


def _empty_confidence(reason: str) -> dict:
    return {
        "prev_day_high_volume": False,
        "today_volume_confirm": False,
        "volume_expansion_ratio_3d": 0.0,
        "volume_signal": reason,
        "chip_avg_cost_20d": 0.0,
        "chip_avg_cost_60d": 0.0,
        "current_vs_chip_cost_pct": 0.0,
        "overhead_pressure_ratio": 0.0,
        "downside_support_ratio": 0.0,
        "main_force_chip_proxy": 0.0,
        "peak_type": "neutral",
        "confidence_delta": 0,
        "reasons": [reason],
    }
