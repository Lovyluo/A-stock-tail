from __future__ import annotations

import copy
from datetime import datetime, time
from pathlib import Path
from typing import Any

from overnight_quant.data.market_calendar import (
    AFTERNOON_SESSION,
    CALL_AUCTION,
    CN_TZ,
    LUNCH_BREAK,
    MORNING_SESSION,
    NON_TRADING_DAY,
    TAIL_SESSION,
    get_session_state,
    is_likely_cn_trade_day,
)


DEFAULT_INTRADAY_CONFIG = {
    "intraday": {
        "max_candidates": 20,
        "min_signal_score_a": 82,
        "min_signal_score_b": 68,
        "min_price_above_vwap_pct": 0.2,
        "max_price_above_vwap_pct": 3.8,
        "vwap_pullback_tolerance_pct": 0.45,
        "vwap_confirm_pct": 0.25,
        "volume_expansion_ratio": 1.15,
        "max_open_gap_pct": 6.5,
        "max_intraday_change_pct": 8.5,
        "min_limit_up_gap_pct": 1.2,
        "min_range_position": 0.52,
    },
    "paths": {
        "records_dir": "overnight_quant/records",
        "reports_dir": "overnight_quant/reports",
        "examples_dir": "overnight_quant/examples",
    },
}

BUY_POINT_A = "BUY_POINT_A"
BUY_POINT_B = "BUY_POINT_B"
BUY_WATCH = "BUY_WATCH"
NO_BUY = "NO_BUY"

BUY_WINDOWS = {"PRIMARY_BUY", "SECONDARY_BUY", "AFTERNOON_RECLAIM"}
WATCH_WINDOWS = BUY_WINDOWS | {"OPEN_FILTER", "AUCTION_OBSERVE"}


def load_intraday_config(path: str | None = None) -> dict:
    config = copy.deepcopy(DEFAULT_INTRADAY_CONFIG)
    config_path = Path(path) if path else Path(__file__).resolve().parents[1] / "config.yaml"
    if not config_path.exists():
        return config
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        _deep_update(config, loaded)
    except Exception:
        return config
    return config


class IntradayObservationAnalyzer:
    def __init__(
        self,
        client,
        config: dict,
        mode: str,
        now: datetime | None = None,
        candidate_rows: list[dict[str, Any]] | None = None,
    ):
        self.client = client
        self.config = config
        self.mode = mode
        self.now = _coerce_now(now)
        self.candidate_rows = [dict(row) for row in (candidate_rows or [])]

    def analyze(self, trade_date: str | None = None) -> dict:
        date_value = trade_date or self.now.date().isoformat()
        session_state = get_session_state(self.now)
        window = intraday_window(self.now)
        result = {
            "trade_date": date_value,
            "run_time": self.now.isoformat(timespec="seconds"),
            "mode": self.mode,
            "status": "UNKNOWN",
            "session_state": session_state,
            "intraday_window": window,
            "candidate_source": "after_close_watchlist",
            "valid_for_trading_observation": "DEMO_ONLY" if self.mode == "demo" else "NO",
            "market_gate": {"pass": False, "reasons": [], "reject_reasons": []},
            "rows": [],
            "quality": _client_quality(self.client),
        }
        if self.mode == "live" and not is_likely_cn_trade_day(self.now):
            return _blocked(result, "NOT_TRADING_DAY")
        if self.mode == "live" and session_state == NON_TRADING_DAY:
            return _blocked(result, "NOT_TRADING_DAY")
        if self.mode == "live" and window not in WATCH_WINDOWS:
            return _blocked(result, "NOT_INTRADAY_WINDOW")

        market = self.client.get_market_snapshot()
        result["market"] = market
        result["market_gate"] = _market_gate(market)
        candidates = _limit_candidates(self.candidate_rows, self.config)
        if not candidates:
            return _blocked(result, "NO_INTRADAY_CANDIDATES")

        rows = [self._evaluate_candidate(row, result["market_gate"], window) for row in candidates]
        result["rows"] = rows
        result["quality"] = _client_quality(self.client)
        counts = _signal_counts(rows)
        result.update(counts)
        if self.mode == "demo":
            result["status"] = "DEMO_INTRADAY_OBSERVATION"
            result["valid_for_trading_observation"] = "DEMO_ONLY"
        elif not result["market_gate"]["pass"]:
            result["status"] = "MARKET_BLOCKED"
            result["valid_for_trading_observation"] = "NO"
        elif counts["buy_point_a_count"] or counts["buy_point_b_count"]:
            result["status"] = "INTRADAY_SIGNAL_READY"
            result["valid_for_trading_observation"] = "YES"
        elif counts["buy_watch_count"]:
            result["status"] = "INTRADAY_WATCH_ONLY"
            result["valid_for_trading_observation"] = "YES"
        else:
            result["status"] = "INTRADAY_NO_SIGNAL"
            result["valid_for_trading_observation"] = "YES"
        return result

    def _evaluate_candidate(self, candidate: dict, market_gate: dict, window: str) -> dict:
        code = _normalize_code(candidate.get("code"))
        current = self.client.get_current_price(code)
        bars = self.client.get_intraday_bars(code) if hasattr(self.client, "get_intraday_bars") else []
        snapshot = _snapshot_from_current(current, candidate, bars)
        metrics = _intraday_metrics(snapshot, bars, self.config)
        risk_flags = _candidate_risk_flags(candidate)
        score, reasons, invalid = _score_intraday(candidate, snapshot, metrics, market_gate, window, risk_flags, self.config)
        signal = _classify_signal(candidate, score, metrics, market_gate, window, risk_flags, invalid, self.config)
        return {
            "code": code,
            "name": candidate.get("name") or current.get("name", ""),
            "source_category": candidate.get("category", ""),
            "source_bucket": candidate.get("source_bucket", "watchlist"),
            "source_buckets": candidate.get("source_buckets", candidate.get("source_bucket", "watchlist")),
            "after_close_score": _as_float(candidate.get("score"), 0.0),
            "signal": signal,
            "action_bias": _action_bias(candidate, snapshot, metrics, market_gate, risk_flags, invalid),
            "signal_score": round(score, 2),
            "price": snapshot["price"],
            "vwap": snapshot["vwap"],
            "distance_to_vwap_pct": metrics["distance_to_vwap_pct"],
            "change_pct": snapshot["change_pct"],
            "open_change_pct": snapshot["open_change_pct"],
            "range_position": metrics["range_position"],
            "limit_up_gap_pct": snapshot["limit_up_gap_pct"],
            "volume_confirmation": metrics["volume_confirmation"],
            "intraday_source": "eastmoney_intraday_trends" if bars else "quote_vwap_proxy",
            "has_intraday_series": bool(bars),
            "buy_zone": _buy_zone(snapshot, metrics),
            "reasons": reasons,
            "invalid_conditions": invalid,
            "risk_flags": risk_flags,
            "defence_conditions": _defence_conditions(candidate, metrics),
            "observation_conditions": _observation_conditions(candidate),
        }


def intraday_window(value: datetime) -> str:
    current = _coerce_now(value)
    current_time = current.time()
    if current_time < time(9, 15):
        return "PRE_MARKET"
    if time(9, 15) <= current_time < time(9, 30):
        return "AUCTION_OBSERVE"
    if time(9, 30) <= current_time < time(9, 45):
        return "OPEN_FILTER"
    if time(9, 45) <= current_time < time(10, 30):
        return "PRIMARY_BUY"
    if time(10, 30) <= current_time < time(11, 30):
        return "SECONDARY_BUY"
    if time(11, 30) <= current_time < time(13, 0):
        return "LUNCH_BREAK"
    if time(13, 0) <= current_time < time(14, 20):
        return "AFTERNOON_RECLAIM"
    if time(14, 20) <= current_time < time(15, 0):
        return "NO_NEW_BUY"
    return "AFTER_CLOSE"


def _score_intraday(
    candidate: dict,
    snapshot: dict,
    metrics: dict,
    market_gate: dict,
    window: str,
    risk_flags: list[str],
    config: dict,
) -> tuple[float, list[str], list[str]]:
    cfg = config.get("intraday", {})
    score = 0.0
    reasons: list[str] = []
    reject: list[str] = []
    if market_gate.get("pass"):
        score += 15
        reasons.append("market_gate_pass")
    else:
        reject.extend(market_gate.get("reject_reasons") or ["market_gate_fail"])
    if window in BUY_WINDOWS:
        score += 10
        reasons.append(f"buy_window:{window}")
    elif window in WATCH_WINDOWS:
        score += 4
        reasons.append(f"watch_window:{window}")
    else:
        reject.append(f"outside_buy_window:{window}")
    if candidate.get("category") == "A":
        score += 12
        reasons.append("after_close_category_a")
    elif candidate.get("category") == "B":
        score += 8
        reasons.append("after_close_category_b")
    score += min(_as_float(candidate.get("score"), 0.0) / 10, 10)

    distance = metrics["distance_to_vwap_pct"]
    if distance >= float(cfg.get("min_price_above_vwap_pct", 0.2)):
        score += 12
        reasons.append("price_above_vwap")
    else:
        reject.append("below_or_too_close_to_vwap")
    if distance <= float(cfg.get("max_price_above_vwap_pct", 3.8)):
        score += 8
        reasons.append("not_far_above_vwap")
    else:
        reject.append("too_far_above_vwap")
    if metrics["vwap_reclaim"]:
        score += 14
        reasons.append("vwap_pullback_reclaim")
    elif metrics["vwap_proxy_reclaim"]:
        score += 7
        reasons.append("quote_proxy_vwap_reclaim")
    else:
        reject.append("vwap_reclaim_not_confirmed")
    if metrics["low_point_lift"]:
        score += 8
        reasons.append("intraday_lows_lifting")
    if metrics["volume_confirmation"]:
        score += 8
        reasons.append("rebound_volume_expansion")
    if metrics["range_position"] >= float(cfg.get("min_range_position", 0.52)):
        score += 6
        reasons.append("range_position_ok")
    else:
        reject.append("range_position_weak")
    if snapshot["open_change_pct"] <= float(cfg.get("max_open_gap_pct", 6.5)):
        score += 4
    else:
        reject.append("open_gap_too_high")
    if snapshot["change_pct"] <= float(cfg.get("max_intraday_change_pct", 8.5)):
        score += 4
    else:
        reject.append("intraday_change_too_high")
    if snapshot["limit_up_gap_pct"] >= float(cfg.get("min_limit_up_gap_pct", 1.2)) or not snapshot["limit_up_gap_pct"]:
        score += 4
    else:
        reject.append("too_close_to_limit_up")
    if risk_flags:
        reject.extend(risk_flags)
    return score, _unique(reasons), _unique(reject)


def _classify_signal(
    candidate: dict,
    score: float,
    metrics: dict,
    market_gate: dict,
    window: str,
    risk_flags: list[str],
    invalid: list[str],
    config: dict,
) -> str:
    cfg = config.get("intraday", {})
    if risk_flags or not market_gate.get("pass") or window not in WATCH_WINDOWS:
        return NO_BUY
    if window not in BUY_WINDOWS:
        return BUY_WATCH
    if invalid and not set(invalid).issubset({"vwap_reclaim_not_confirmed"}):
        return BUY_WATCH if metrics["distance_to_vwap_pct"] > 0 else NO_BUY
    min_a = float(cfg.get("min_signal_score_a", 82))
    min_b = float(cfg.get("min_signal_score_b", 68))
    if (
        score >= min_a
        and candidate.get("category") == "A"
        and metrics["has_intraday_series"]
        and metrics["vwap_reclaim"]
        and metrics["volume_confirmation"]
    ):
        return BUY_POINT_A
    if score >= min_b and metrics["has_intraday_series"] and metrics["vwap_reclaim"]:
        return BUY_POINT_B
    if metrics["distance_to_vwap_pct"] > 0 or metrics["vwap_proxy_reclaim"]:
        return BUY_WATCH
    return NO_BUY


def _intraday_metrics(snapshot: dict, bars: list[dict], config: dict) -> dict:
    cfg = config.get("intraday", {})
    price = snapshot["price"]
    vwap = snapshot["vwap"]
    distance = _pct(price, vwap)
    high = snapshot["high"]
    low = snapshot["low"]
    range_position = (price - low) / (high - low) if high > low else 0.0
    if not bars:
        return {
            "distance_to_vwap_pct": round(distance, 2),
            "range_position": round(range_position, 3),
            "vwap_reclaim": False,
            "vwap_proxy_reclaim": low <= vwap * (1 + float(cfg.get("vwap_pullback_tolerance_pct", 0.45)) / 100)
            and price >= vwap * (1 + float(cfg.get("vwap_confirm_pct", 0.25)) / 100),
            "low_point_lift": range_position >= float(cfg.get("min_range_position", 0.52)),
            "volume_confirmation": False,
            "has_intraday_series": False,
        }
    recent = bars[-6:]
    prior = bars[:-6] or bars[:1]
    tolerance = float(cfg.get("vwap_pullback_tolerance_pct", 0.45)) / 100
    confirm = float(cfg.get("vwap_confirm_pct", 0.25)) / 100
    touched_vwap = any(_bar_low(row) <= _bar_vwap(row) * (1 + tolerance) for row in recent)
    last_close = _bar_close(bars[-1])
    last_vwap = _bar_vwap(bars[-1])
    vwap_reclaim = touched_vwap and last_close >= last_vwap * (1 + confirm)
    early_low = min(_bar_low(row) for row in prior)
    recent_low = min(_bar_low(row) for row in recent)
    low_point_lift = recent_low >= early_low * 0.995
    recent_volume = sum(_bar_volume(row) for row in recent[-3:]) / max(len(recent[-3:]), 1)
    prior_volume = sum(_bar_volume(row) for row in prior[-6:]) / max(len(prior[-6:]), 1)
    volume_confirmation = prior_volume > 0 and recent_volume >= prior_volume * float(cfg.get("volume_expansion_ratio", 1.15))
    return {
        "distance_to_vwap_pct": round(distance, 2),
        "range_position": round(range_position, 3),
        "vwap_reclaim": vwap_reclaim,
        "vwap_proxy_reclaim": False,
        "low_point_lift": low_point_lift,
        "volume_confirmation": volume_confirmation,
        "has_intraday_series": True,
    }


def _snapshot_from_current(current: dict, candidate: dict, bars: list[dict]) -> dict:
    last_bar = bars[-1] if bars else {}
    price = _as_float(last_bar.get("close"), _as_float(current.get("price"), _as_float(candidate.get("close_price"), 0.0)))
    vwap = _as_float(last_bar.get("vwap"), _as_float(current.get("vwap"), price))
    last_close = _as_float(current.get("last_close"), 0.0)
    change_pct = _as_float(current.get("change_pct"), _as_float(current.get("open_change_pct"), 0.0))
    if last_close and not change_pct:
        change_pct = _pct(price, last_close)
    open_price = _as_float(current.get("open_price"), _as_float(current.get("open"), price))
    open_change_pct = _as_float(current.get("open_change_pct"), _pct(open_price, last_close) if last_close else 0.0)
    high = max(_as_float(current.get("high"), price), price, _as_float(last_bar.get("high"), price))
    low = min(value for value in [_as_float(current.get("low"), price), price, _as_float(last_bar.get("low"), price)] if value > 0)
    limit_up = _as_float(current.get("limit_up"), _as_float(candidate.get("limit_up"), 0.0))
    return {
        "price": round(price, 3),
        "vwap": round(vwap or price, 3),
        "last_close": last_close,
        "open_price": open_price,
        "open_change_pct": round(open_change_pct, 2),
        "change_pct": round(change_pct, 2),
        "high": high,
        "low": low,
        "limit_up": limit_up,
        "limit_up_gap_pct": round(_pct(limit_up, price), 2) if limit_up and price else 0.0,
    }


def _market_gate(market: dict) -> dict:
    reasons: list[str] = []
    rejects: list[str] = []
    indices = market.get("indices") or {}
    changes = [_as_float(item.get("change_pct"), 0.0) for item in indices.values() if isinstance(item, dict)]
    avg_change = sum(changes) / len(changes) if changes else 0.0
    if avg_change >= -0.7:
        reasons.append("index_not_weak")
    else:
        rejects.append("index_weak")
    if _as_float(market.get("northbound_net_yi"), 0.0) >= -80:
        reasons.append("northbound_not_extreme_outflow")
    else:
        rejects.append("northbound_extreme_outflow")
    if int(market.get("limit_down_count", 0) or 0) <= 35:
        reasons.append("limit_down_count_controlled")
    else:
        rejects.append("limit_down_count_high")
    return {"pass": not rejects, "reasons": reasons, "reject_reasons": rejects}


def _candidate_risk_flags(candidate: dict) -> list[str]:
    raw = candidate.get("risk_flags", "")
    if isinstance(raw, list):
        tokens = raw
    else:
        tokens = str(raw or "").replace(",", "|").split("|")
    hard = {
        "st_stock",
        "suspended",
        "new_stock",
        "bj_stock",
        "limit_up_chase_risk",
        "quote_stale",
        "freshness_unknown",
        "safety_field_unknown",
    }
    return _unique(token.strip() for token in tokens if token.strip() in hard)


def _is_holding(candidate: dict) -> bool:
    return candidate.get("source_bucket") == "holding" or "holding" in str(candidate.get("source_buckets", ""))


def _action_bias(candidate: dict, snapshot: dict, metrics: dict, market_gate: dict, risk_flags: list[str], invalid: list[str]) -> str:
    holding = _is_holding(candidate)
    if risk_flags or not snapshot.get("price") or not snapshot.get("vwap"):
        return "avoid"
    below_vwap = float(metrics.get("distance_to_vwap_pct", 0) or 0) < 0
    if holding and (below_vwap or not market_gate.get("pass")):
        return "defend"
    if not market_gate.get("pass") or invalid:
        return "defend" if holding else "observe"
    if float(metrics.get("distance_to_vwap_pct", 0) or 0) >= 0 and metrics.get("volume_confirmation"):
        return "attack"
    return "observe"


def _defence_conditions(candidate: dict, metrics: dict) -> list[str]:
    if not _is_holding(candidate):
        return []
    conditions = ["跌破 VWAP 后反抽不过", "市场方向转弱"]
    cost = _as_float(candidate.get("avg_buy_price") or candidate.get("buy_price"), 0.0)
    stop = _as_float(candidate.get("stop_loss_price"), 0.0)
    if cost:
        conditions.append(f"跌破持仓成本 {cost:.3f}")
    if stop:
        conditions.append(f"跌破止损观察线 {stop:.3f}")
    if float(metrics.get("distance_to_vwap_pct", 0) or 0) < 0:
        conditions.append("当前位于 VWAP 下方，优先观察承接")
    return conditions


def _observation_conditions(candidate: dict) -> list[str]:
    if _is_holding(candidate):
        return ["重新站稳 VWAP 且分时低点不再下移"]
    conditions = ["回踩 VWAP 不破", "放量重新站上 VWAP"]
    if "auction" in str(candidate.get("source_buckets", "")):
        conditions.append("竞价偏强但盘中承接转弱时降低观察等级")
    return conditions


def _limit_candidates(rows: list[dict], config: dict) -> list[dict]:
    limit = int(config.get("intraday", {}).get("max_candidates", 20) or 20)
    normalized = [_normalize_candidate(row) for row in rows if _normalize_code(row.get("code"))]
    return normalized[:limit]


def _normalize_candidate(row: dict) -> dict:
    item = dict(row)
    item["code"] = _normalize_code(item.get("code"))
    item["category"] = str(item.get("category", "")).strip().upper() or "B"
    item["score"] = _as_float(item.get("score"), _as_float(item.get("after_close_score"), 0.0))
    return item


def _signal_counts(rows: list[dict]) -> dict:
    return {
        "signal_count": sum(1 for row in rows if row.get("signal") in {BUY_POINT_A, BUY_POINT_B, BUY_WATCH}),
        "buy_point_a_count": sum(1 for row in rows if row.get("signal") == BUY_POINT_A),
        "buy_point_b_count": sum(1 for row in rows if row.get("signal") == BUY_POINT_B),
        "buy_watch_count": sum(1 for row in rows if row.get("signal") == BUY_WATCH),
    }


def _buy_zone(snapshot: dict, metrics: dict) -> str:
    vwap = snapshot["vwap"]
    if not vwap:
        return ""
    low = round(vwap * 1.002, 2)
    high = round(vwap * 1.012, 2)
    if metrics["distance_to_vwap_pct"] > 2.2:
        return f"wait_pullback_to_{low}-{high}"
    return f"{low}-{high}"


def _blocked(result: dict, status: str) -> dict:
    result["status"] = status
    result["valid_for_trading_observation"] = "DEMO_ONLY" if result.get("mode") == "demo" else "NO"
    return result


def _client_quality(client) -> dict:
    report = getattr(client, "quality_report", None)
    return {
        "fallback_to_demo": bool(getattr(report, "fallback_to_demo", False)),
        "source_status": list(getattr(report, "source_status", []) or []),
        "warnings": list(getattr(report, "warnings", []) or []),
    }


def _bar_close(row: dict) -> float:
    return _as_float(row.get("close"), 0.0)


def _bar_low(row: dict) -> float:
    return _as_float(row.get("low"), _bar_close(row))


def _bar_vwap(row: dict) -> float:
    return _as_float(row.get("vwap"), _bar_close(row))


def _bar_volume(row: dict) -> float:
    return _as_float(row.get("volume"), 0.0)


def _pct(value: float, base: float) -> float:
    if not value or not base:
        return 0.0
    return (float(value) - float(base)) / float(base) * 100


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _unique(values) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _coerce_now(now: datetime | None) -> datetime:
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        return current.replace(tzinfo=CN_TZ)
    return current.astimezone(CN_TZ)


def _deep_update(base: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
