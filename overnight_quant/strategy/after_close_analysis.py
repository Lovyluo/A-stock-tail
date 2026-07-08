from __future__ import annotations

import copy
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from overnight_quant.data.market_calendar import (
    AFTER_CLOSE,
    CALL_AUCTION,
    CN_TZ,
    PRE_MARKET,
    effective_after_close_trade_day,
    get_session_state,
    is_likely_cn_trade_day,
    previous_likely_cn_trade_day,
)


DEFAULT_AFTER_CLOSE_CONFIG = {
    "after_close": {
        "max_a_count": 5,
        "max_b_count": 10,
        "max_c_count": 10,
        "min_a_score": 80,
        "min_b_score": 70,
        "min_c_score": 60,
    },
    "filters": {
        "min_price": 3,
        "min_amount_wan": 15000,
        "max_tail_pullback_pct": 3.5,
    },
    "trend": {"max_upper_shadow_ratio": 0.45},
    "paths": {
        "records_dir": "overnight_quant/records",
        "reports_dir": "overnight_quant/reports",
        "examples_dir": "overnight_quant/examples",
    },
}

CRITICAL_QUALITY_FLAGS = {"quote_stale", "freshness_unknown", "safety_field_unknown"}
SAFETY_FIELDS = {"limit_up", "limit_down", "is_limit_up", "is_st", "is_suspended", "is_new_stock", "is_bj_stock"}

USER_REASON_LABELS = {
    "change_ideal": "涨幅处于理想观察区间",
    "change_extended": "涨幅偏大，次日容易分歧",
    "change_outside_watch_range": "涨幅偏离理想观察区间",
    "amount_strong": "成交额充足",
    "amount_adequate": "成交额达到观察门槛",
    "turnover_ideal": "换手率处于理想区间",
    "turnover_adequate": "换手率达到观察要求",
    "volume_ratio_strong": "量比明显放大",
    "volume_ratio_adequate": "量比达到观察要求",
    "close_near_high": "收盘接近日内高位",
    "theme_missing": "题材信息缺失",
    "theme_present": "具备题材归因",
    "theme_top1": "属于当日最强题材",
    "theme_top3": "属于当日前三题材",
    "theme_top5": "属于当日前五题材",
    "theme_breadth": "同题材存在板块联动",
    "theme_industry_fallback": "具备行业/概念归属（弱题材）",
    "theme_external_fallback": "具备概念/行业归属（非当日热点）",
    "theme_recent_continuity": "题材近几日持续活跃",
    "theme_mainline_continuity": "题材具备主线延续性",
    "theme_unconfirmed_recent": "题材未获近几日热点验证",
    "theme_one_day_risk": "题材仅单日活跃，存在一日游风险",
    "theme_relative_strength_in_pullback": "主线回调中个股逆势走强",
    "theme_mainline_pullback": "主线题材短线回调",
    "theme_rotation_risk": "板块弱势且主线延续性不足，存在题材轮换风险",
    "ma_bullish_alignment": "均线结构偏强",
    "tail_stable": "尾盘承接稳定",
    "tail_pullback": "尾盘回落偏大",
    "shadow_controlled": "上影线可接受",
    "upper_shadow_risk": "上影线偏长",
    "main_net_missing": "主力资金字段缺失",
    "main_net_positive": "主力资金偏正",
    "main_net_negative": "主力资金流出",
    "big_order_missing": "大单资金字段缺失",
    "big_order_positive": "大单净额偏正",
    "big_order_negative": "大单净额偏弱",
    "capital_estimated_only": "资金数据为估算值，仅供辅助观察",
    "limit_up_chase_risk": "涨停或近涨停，存在追高风险",
    "capital_outflow": "资金流出风险",
    "freshness_risk": "数据时效性不足",
    "safety_unknown": "关键安全字段存在不确定性",
    "st_stock": "ST或退市风险股票",
    "suspended": "停牌或成交异常",
    "bj_stock": "北交所股票不在观察范围",
    "new_stock": "次新股上市时间不足",
    "price_below_min": "股价低于观察下限",
    "amount_below_min": "成交额低于观察门槛",
    "turnover_below_min": "换手率低于观察门槛",
    "vol_ratio_below_min": "量比低于观察门槛",
}

MISSING_REASON_KEYS = {
    "main_net_missing",
    "big_order_missing",
    "capital_estimated_only",
    "freshness_risk",
    "safety_unknown",
}

INFO_GAP_REASON_KEYS = {
    "theme_missing",
    "theme_unconfirmed_recent",
}

RISK_REASON_KEYS = {
    "change_extended",
    "change_outside_watch_range",
    "tail_pullback",
    "upper_shadow_risk",
    "main_net_negative",
    "big_order_negative",
    "limit_up_chase_risk",
    "capital_outflow",
    "st_stock",
    "suspended",
    "bj_stock",
    "new_stock",
    "price_below_min",
    "amount_below_min",
    "turnover_below_min",
    "vol_ratio_below_min",
    "theme_one_day_risk",
    "theme_rotation_risk",
}

RISK_FLAG_REASON_KEYS = {
    "st_stock": "st_stock",
    "suspended": "suspended",
    "bj_stock": "bj_stock",
    "new_stock": "new_stock",
    "price_below_min": "price_below_min",
    "amount_below_min": "amount_below_min",
    "change_pct_outside_watch_range": "change_outside_watch_range",
    "turnover_below_min": "turnover_below_min",
    "vol_ratio_below_min": "vol_ratio_below_min",
    "tail_pullback_too_large": "tail_pullback",
    "limit_up_chase_risk": "limit_up_chase_risk",
    "capital_outflow": "capital_outflow",
    "quote_stale": "freshness_risk",
    "freshness_unknown": "freshness_risk",
    "safety_field_unknown": "safety_unknown",
    "theme_one_day_risk": "theme_one_day_risk",
    "theme_rotation_risk": "theme_rotation_risk",
}


def load_after_close_config(path: str | None = None) -> dict:
    config = copy.deepcopy(DEFAULT_AFTER_CLOSE_CONFIG)
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


class AfterCloseAnalyzer:
    def __init__(
        self,
        client,
        config: dict,
        mode: str,
        now: datetime | None = None,
        analysis_mode: str = "after_close",
    ):
        self.client = client
        self.config = config
        self.mode = mode
        self.now = _coerce_now(now)
        self.analysis_mode = analysis_mode

    def analyze(self, trade_date: str | None = None) -> dict:
        date_value = trade_date or self.now.date().isoformat()
        result = self._base_result(date_value)
        if self.mode == "live":
            if self.analysis_mode == "previous_close_replay":
                if not is_likely_cn_trade_day(self.now) or result["session_state"] not in {PRE_MARKET, CALL_AUCTION}:
                    return self._blocked_result(result, "NOT_REPLAY_WINDOW")
            else:
                effective_after_close_day = effective_after_close_trade_day(self.now)
                if not is_likely_cn_trade_day(self.now):
                    return self._blocked_result(result, "NOT_TRADING_DAY")
                if not effective_after_close_day or date_value != effective_after_close_day.isoformat():
                    return self._blocked_result(result, "NOT_AFTER_CLOSE")

        market = self.client.get_market_snapshot()
        result["market"] = market
        result["market_score"], result["market_reasons"] = _market_score(market)
        if hasattr(self.client, "get_after_close_universe_quotes"):
            rows = self.client.get_after_close_universe_quotes()
        else:
            rows = self.client.get_candidate_quotes()
        result["quality"] = _client_quality(self.client)
        if self.mode == "live" and _has_fallback(self.client):
            result["candidate_source"] = "demo_fallback"
            return self._blocked_result(result, self._status("DATA_FALLBACK_DEMO", "REPLAY_DATA_FALLBACK_DEMO"))

        if self.analysis_mode != "previous_close_replay":
            source = getattr(self.client, "after_close_candidate_source", "")
            result["candidate_source"] = "demo" if self.mode == "demo" else (source or "full_market_00_60")
        evaluated = []
        for stock in rows:
            kline = self.client.get_daily_kline(stock.get("code", ""))
            scored = _score_candidate(stock, kline, result["market_score"], self.config)
            if hasattr(self.client, "get_kline_freshness_reasons"):
                for reason in self.client.get_kline_freshness_reasons(stock.get("code", "")):
                    scored["data_quality_flags"] = _unique(
                        scored["data_quality_flags"] + (["freshness_unknown"] if reason == "timestamp_missing" else [reason])
                    )
            scored["risk_flags"] = _hard_exclusions(scored, self.config)
            _apply_risk_flag_reasons(scored)
            scored["would_be_a_or_b"] = (
                scored["total_score"] >= float(self.config.get("after_close", {}).get("min_b_score", 70))
                and not [reason for reason in scored["risk_flags"] if reason not in CRITICAL_QUALITY_FLAGS]
            )
            evaluated.append(scored)
        result["quality"] = _client_quality(self.client)
        result["evaluated_rows"] = evaluated
        result["themes"] = _theme_summary(evaluated)
        result["recent_hot_themes"] = (
            self.client.get_recent_hot_theme_summary()
            if hasattr(self.client, "get_recent_hot_theme_summary")
            else []
        )

        if self.mode == "live" and _has_fallback(self.client):
            result["candidate_source"] = "demo_fallback"
            return self._blocked_result(result, self._status("DATA_FALLBACK_DEMO", "REPLAY_DATA_FALLBACK_DEMO"))

        if self.mode == "live" and any(
            row["would_be_a_or_b"] and CRITICAL_QUALITY_FLAGS.intersection(row["data_quality_flags"])
            for row in evaluated
        ):
            return self._blocked_result(result, self._status("DATA_QUALITY_BLOCKED", "REPLAY_DATA_QUALITY_BLOCKED"))

        result["categories"] = _classify(evaluated, self.config)
        if self.mode == "demo":
            result["status"] = "DEMO_ANALYSIS"
            result["valid_for_trading_observation"] = "DEMO_ONLY"
        elif any(result["categories"].values()):
            result["status"] = self._status("WATCHLIST_READY", "MORNING_REPLAY_READY")
            result["valid_for_trading_observation"] = "YES"
        else:
            result["status"] = self._status("NO_WATCHLIST", "MORNING_REPLAY_NO_WATCHLIST")
            result["valid_for_trading_observation"] = "YES"
        result["final_view"] = _final_view(result["status"])
        return result

    def _base_result(self, trade_date: str) -> dict:
        result = {
            "trade_date": trade_date,
            "next_trade_date": _next_likely_trade_date(trade_date),
            "next_trade_date_calendar": "weekday_proxy",
            "analysis_mode": self.analysis_mode,
            "mode": self.mode,
            "status": "",
            "session_state": get_session_state(self.now),
            "candidate_source": "demo" if self.mode == "demo" else "live",
            "valid_for_trading_observation": "DEMO_ONLY" if self.mode == "demo" else "NO",
            "final_view": "",
            "market": {},
            "market_score": 0.0,
            "market_reasons": [],
            "themes": [],
            "recent_hot_themes": [],
            "industry_rank_available": False,
            "categories": {"A": [], "B": [], "C": []},
            "evaluated_rows": [],
            "quality": {"source_status": [], "warnings": [], "fallback_to_demo": False},
        }
        if self.analysis_mode == "previous_close_replay":
            observation_date = self.now.date().isoformat()
            replay_as_of_date = previous_likely_cn_trade_day(self.now).isoformat()
            result.update(
                {
                    "observation_date": observation_date,
                    "replay_as_of_date": replay_as_of_date,
                    "replay_calendar": "weekday_proxy",
                    "trade_date": replay_as_of_date,
                    "next_trade_date": observation_date,
                    "candidate_source": "live_previous_close_replay",
                    "freshness_basis": "previous_close_expected",
                }
            )
        return result

    def _status(self, standard_status: str, replay_status: str) -> str:
        return replay_status if self.analysis_mode == "previous_close_replay" else standard_status

    @staticmethod
    def _blocked_result(result: dict, status: str) -> dict:
        result["status"] = status
        result["valid_for_trading_observation"] = "NO"
        result["categories"] = {"A": [], "B": [], "C": []}
        result["final_view"] = _final_view(status)
        return result


def _score_candidate(stock: dict, kline: list[dict], market_score: float, config: dict) -> dict:
    row = dict(stock)
    quality_flags = _quality_flags(row)
    theme_market_state, theme_market_reasons = _theme_market_state(row)
    if theme_market_state:
        row["theme_market_state"] = theme_market_state
    pv, pv_reasons = _price_volume_score(row)
    theme, theme_reasons = _theme_score(row)
    trend, trend_reasons = _trend_score(row, kline, config)
    capital, capital_reasons = _capital_score(row)
    risk, risk_reasons = _risk_score(row, quality_flags, config)
    total = market_score * 0.10 + theme * 0.25 + pv * 0.25 + trend * 0.20 + capital * 0.10 + risk * 0.10
    source = row.get("fund_flow_source") or (row.get("_sources") or {}).get("main_net", "") or "unknown"
    reason_keys = _unique(pv_reasons + theme_reasons + theme_market_reasons + trend_reasons + capital_reasons + risk_reasons)
    positive_keys, info_gap_keys, missing_keys, risk_keys = _split_reason_keys(reason_keys)
    row.update(
        {
            "close_price": row.get("price", row.get("close_price", "")),
            "score": round(total, 2),
            "total_score": round(total, 2),
            "main_net_source": source,
            "estimated_capital_flow": source == "estimated_from_big_order_net",
            "data_quality_flags": quality_flags,
            "positive_reason_keys": positive_keys,
            "info_gap_reason_keys": info_gap_keys,
            "missing_reason_keys": missing_keys,
            "risk_reason_keys": risk_keys,
            "positive_reasons": "；".join(_user_reason_list(positive_keys)),
            "info_gap_reasons": "；".join(_user_reason_list(info_gap_keys)),
            "missing_reasons": "；".join(_user_reason_list(missing_keys)),
            "risk_reasons": "；".join(_user_reason_list(risk_keys)),
            "reason": "；".join(_user_reason_list(reason_keys)),
        }
    )
    return row


def _classify(rows: list[dict], config: dict) -> dict[str, list[dict]]:
    settings = config.get("after_close", {})
    result = {"A": [], "B": [], "C": []}
    ordered = sorted(rows, key=lambda item: float(item.get("total_score", 0)), reverse=True)
    for row in ordered:
        hard = row.get("risk_flags") or []
        score = float(row.get("total_score", 0))
        category = ""
        if not hard and score >= float(settings.get("min_a_score", 80)):
            category = "A"
        elif not hard and score >= float(settings.get("min_b_score", 70)):
            category = "B"
        elif score >= float(settings.get("min_c_score", 60)) or _risk_observation_candidate(row):
            category = "C"
        if not category:
            continue
        row = dict(row)
        row["category"] = category
        row["risk_flags"] = list(hard)
        row["tomorrow_watch_plan"], row["invalid_conditions"] = _watch_plan(row, category)
        limit = int(settings.get(f"max_{category.lower()}_count", 0))
        if len(result[category]) < limit:
            result[category].append(row)
    return result


def _quality_flags(stock: dict) -> list[str]:
    flags: list[str] = []
    if not stock.get("theme_tags"):
        flags.append("theme_missing")
    if stock.get("main_net") in (None, "") or stock.get("big_order_net") in (None, ""):
        flags.append("capital_missing")
    if stock.get("fund_flow_source") == "estimated_from_big_order_net" or stock.get("estimated_capital_flow"):
        flags.append("estimated_capital_flow")
    freshness = stock.get("_freshness_reasons") or []
    if "quote_stale" in freshness:
        flags.append("quote_stale")
    if any(reason in freshness for reason in ("freshness_unknown", "timestamp_missing")):
        flags.append("freshness_unknown")
    missing = set(stock.get("_missing_fields") or [])
    if stock.get("_risk_unknown_reasons") or SAFETY_FIELDS.intersection(missing):
        flags.append("safety_field_unknown")
    return _unique(flags)


def _theme_market_state(stock: dict) -> tuple[str, list[str]]:
    block_change = _optional_float(stock.get("theme_block_change_pct"))
    if block_change is None or block_change > -1.0:
        return "", []
    stock_change = _optional_float(stock.get("change_pct")) or 0.0
    rotation = str(stock.get("theme_rotation_state") or "")
    active_days = int(stock.get("theme_active_days", 0) or 0)
    mainline = rotation in {"mainline", "continuing"} or active_days >= 2
    relative_strength = stock_change >= 3.0 and stock_change - block_change >= 4.0
    if mainline and relative_strength:
        return "mainline_pullback_relative_strength", ["theme_relative_strength_in_pullback"]
    if mainline:
        return "mainline_pullback", ["theme_mainline_pullback"]
    return "rotation_risk", ["theme_rotation_risk"]


def _hard_exclusions(stock: dict, config: dict) -> list[str]:
    flags: list[str] = []
    name = str(stock.get("name", "")).upper()
    if stock.get("is_st") or name.startswith(("ST", "*ST")):
        flags.append("st_stock")
    if stock.get("is_suspended") or float(stock.get("price", 0) or 0) <= 0:
        flags.append("suspended")
    if stock.get("is_bj") or stock.get("is_bj_stock") or str(stock.get("code", "")).startswith(("8", "4")):
        flags.append("bj_stock")
    if stock.get("is_new_stock") or int(stock.get("listed_days", 9999) or 9999) < 60:
        flags.append("new_stock")
    rules = config.get("filters", {})
    if float(stock.get("price", 0) or 0) < float(rules.get("min_price", 3)):
        flags.append("price_below_min")
    if float(stock.get("amount_wan", 0) or 0) < float(rules.get("min_amount_wan", 15000)):
        flags.append("amount_below_min")
    if float(stock.get("change_pct", 0) or 0) < 3 or float(stock.get("change_pct", 0) or 0) > 10:
        flags.append("change_pct_outside_watch_range")
    if float(stock.get("turnover_pct", 0) or 0) < 3:
        flags.append("turnover_below_min")
    if float(stock.get("vol_ratio", 0) or 0) < 1:
        flags.append("vol_ratio_below_min")
    if float(stock.get("tail_pullback_pct", 0) or 0) > float(rules.get("max_tail_pullback_pct", 3.5)):
        flags.append("tail_pullback_too_large")
    if stock.get("is_limit_up"):
        flags.append("limit_up_chase_risk")
    if float(stock.get("main_net", 0) or 0) < 0:
        flags.append("capital_outflow")
    if stock.get("theme_rotation_state") == "new_or_one_day":
        flags.append("theme_one_day_risk")
    if stock.get("theme_market_state") == "rotation_risk":
        flags.append("theme_rotation_risk")
    flags.extend(flag for flag in stock.get("data_quality_flags", []) if flag in CRITICAL_QUALITY_FLAGS)
    return _unique(flags)


def _market_score(market: dict) -> tuple[float, list[str]]:
    score = 45.0
    reasons = []
    positive = sum(1 for item in (market.get("indices") or {}).values() if float(item.get("change_pct", 0) or 0) > 0)
    if positive >= 2:
        score += 20
        reasons.append("indices_supportive")
    if market.get("tail_30m_stable"):
        score += 15
        reasons.append("tail_stable")
    if int(market.get("hot_theme_count", 0) or 0) >= 3:
        score += 10
        reasons.append("themes_active")
    if float(market.get("northbound_net_yi", 0) or 0) > -20:
        score += 5
        reasons.append("northbound_not_extreme_outflow")
    if int(market.get("limit_down_count", 0) or 0) <= 30:
        score += 5
        reasons.append("limit_down_controlled")
    return _clamp(score), reasons


def _price_volume_score(stock: dict) -> tuple[float, list[str]]:
    score = 40.0
    reasons = []
    change = float(stock.get("change_pct", 0) or 0)
    amount = float(stock.get("amount_wan", 0) or 0)
    turnover = float(stock.get("turnover_pct", 0) or 0)
    ratio = float(stock.get("vol_ratio", 0) or 0)
    position = float(stock.get("range_position", 0) or 0)
    if 3 <= change <= 7:
        score += 20
        reasons.append("change_ideal")
    elif 7 < change <= 10:
        score += 10
        reasons.append("change_extended")
    else:
        score -= 20
        reasons.append("change_outside_watch_range")
    if amount >= 20000:
        score += 15
        reasons.append("amount_strong")
    elif amount >= 15000:
        score += 8
        reasons.append("amount_adequate")
    if 5 <= turnover <= 15:
        score += 15
        reasons.append("turnover_ideal")
    elif turnover >= 3:
        score += 8
        reasons.append("turnover_adequate")
    if ratio >= 1.2:
        score += 10
        reasons.append("volume_ratio_strong")
    elif ratio >= 1:
        score += 5
        reasons.append("volume_ratio_adequate")
    if position >= 0.65:
        score += 10
        reasons.append("close_near_high")
    return _clamp(score), reasons


def _theme_score(stock: dict) -> tuple[float, list[str]]:
    if not stock.get("theme_tags"):
        return 10.0, ["theme_missing"]
    if stock.get("theme_source") == "industry_fallback":
        return 35.0, ["theme_industry_fallback"]
    if stock.get("theme_source") == "baidu_concept_blocks":
        score = 38.0
        reasons = ["theme_external_fallback"]
    else:
        score = 45.0
        reasons = ["theme_present"]
    rank = stock.get("theme_rank")
    if rank == 1:
        score += 35
        reasons.append("theme_top1")
    elif rank and rank <= 3:
        score += 25
        reasons.append("theme_top3")
    elif rank and rank <= 5:
        score += 15
        reasons.append("theme_top5")
    if int(stock.get("same_theme_strong_count", 0) or 0) >= 2:
        score += 15
        reasons.append("theme_breadth")
    active_days = int(stock.get("theme_active_days", 0) or 0)
    rotation = stock.get("theme_rotation_state", "")
    if active_days >= 3 or rotation == "mainline":
        score += 20
        reasons.append("theme_mainline_continuity")
    elif active_days >= 2 or rotation == "continuing":
        score += 12
        reasons.append("theme_recent_continuity")
    elif rotation == "new_or_one_day":
        reasons.append("theme_one_day_risk")
    elif rotation == "unconfirmed":
        score -= 5
        reasons.append("theme_unconfirmed_recent")
    state = stock.get("theme_market_state")
    if state == "mainline_pullback_relative_strength":
        score += 10
    elif state == "mainline_pullback":
        score += 3
    elif state == "rotation_risk":
        score -= 20
    return _clamp(score), reasons


def _trend_score(stock: dict, kline: list[dict], config: dict) -> tuple[float, list[str]]:
    score = 45.0
    reasons = []
    if kline:
        last = kline[-1]
        close = float(last.get("close", stock.get("price", 0)) or 0)
        if close > float(last.get("ma5", close) or close) > float(last.get("ma10", close) or close) > float(last.get("ma20", close) or close):
            score += 30
            reasons.append("ma_bullish_alignment")
    if float(stock.get("range_position", 0) or 0) >= 0.65:
        score += 15
        reasons.append("close_near_high")
    if float(stock.get("tail_pullback_pct", 0) or 0) <= 1:
        score += 10
        reasons.append("tail_stable")
    else:
        score -= 25
        reasons.append("tail_pullback")
    if float(stock.get("upper_shadow_ratio", 0) or 0) <= float(config.get("trend", {}).get("max_upper_shadow_ratio", 0.45)):
        score += 10
        reasons.append("shadow_controlled")
    else:
        score -= 25
        reasons.append("upper_shadow_risk")
    return _clamp(score), reasons


def _capital_score(stock: dict) -> tuple[float, list[str]]:
    score = 45.0
    reasons = []
    main = stock.get("main_net")
    big = stock.get("big_order_net")
    if main in (None, ""):
        score -= 10
        reasons.append("main_net_missing")
    elif float(main) > 0:
        score += 25
        reasons.append("main_net_positive")
    else:
        score -= 30
        reasons.append("main_net_negative")
    if big in (None, ""):
        score -= 10
        reasons.append("big_order_missing")
    elif float(big) > 0:
        score += 20
        reasons.append("big_order_positive")
    else:
        score -= 25
        reasons.append("big_order_negative")
    if stock.get("fund_flow_source") == "estimated_from_big_order_net":
        score -= 10
        reasons.append("capital_estimated_only")
    return _clamp(score), reasons


def _risk_score(stock: dict, quality_flags: list[str], config: dict) -> tuple[float, list[str]]:
    score = 100.0
    reasons = []
    if stock.get("is_limit_up"):
        score -= 35
        reasons.append("limit_up_chase_risk")
    if float(stock.get("main_net", 0) or 0) < 0:
        score -= 30
        reasons.append("capital_outflow")
    if "theme_missing" in quality_flags:
        score -= 5
        reasons.append("theme_missing")
    if stock.get("theme_rotation_state") == "new_or_one_day":
        score -= 25
        reasons.append("theme_one_day_risk")
    if stock.get("theme_market_state") == "rotation_risk":
        score -= 30
        reasons.append("theme_rotation_risk")
    if "freshness_unknown" in quality_flags or "quote_stale" in quality_flags:
        score -= 40
        reasons.append("freshness_risk")
    if "safety_field_unknown" in quality_flags:
        score -= 40
        reasons.append("safety_unknown")
    if float(stock.get("tail_pullback_pct", 0) or 0) > float(config.get("filters", {}).get("max_tail_pullback_pct", 3.5)):
        score -= 30
        reasons.append("tail_pullback")
    return _clamp(score), reasons


def _watch_plan(stock: dict, category: str) -> tuple[str, str]:
    if category == "C":
        return "", "只看不碰；若继续走弱或板块分歧加大，移出观察。"
    if stock.get("is_limit_up") or float(stock.get("change_pct", 0) or 0) >= 9:
        return (
            "观察条件：不要追一字高开；只有出现可交易开板且承接稳定时再继续观察。",
            "失效条件：高开低走、快速放量下杀、题材分歧明显加大。",
        )
    if float(stock.get("range_position", 0) or 0) >= 0.8 and float(stock.get("vol_ratio", 0) or 0) >= 1.2:
        return (
            "观察条件：开盘不应直接跌破突破位；前五分钟量价仍需配合；回踩应守住昨日突破区域。",
            "失效条件：低开低走、跌回原平台、成交明显萎缩。",
        )
    if int(stock.get("theme_rank") or 99) > 3:
        return (
            "观察条件：只有题材核心股继续走强时才继续观察；跟风股不能先于核心股走弱。",
            "失效条件：题材核心股低开走弱、板块宽度明显收缩、个股高开低走。",
        )
    return (
        "观察条件：高开幅度尽量不超过 5%；前 10 分钟应守住昨收；量能与题材核心股保持稳定。",
        "失效条件：低开超过 3%、跌破昨收后无法收回、题材核心股明显转弱。",
    )


def _risk_observation_candidate(stock: dict) -> bool:
    return (
        float(stock.get("change_pct", 0) or 0) >= 3
        and float(stock.get("amount_wan", 0) or 0) >= 15000
        and bool(stock.get("risk_flags"))
    )


def _theme_summary(rows: list[dict]) -> list[dict]:
    counts: Counter[str] = Counter()
    symbols: dict[str, list[str]] = {}
    for row in rows:
        for tag in row.get("theme_tags") or []:
            counts[tag] += 1
            symbols.setdefault(tag, []).append(str(row.get("name", "")))
    return [
        {"theme": theme, "strength": count, "representative_stocks": symbols[theme][:3]}
        for theme, count in counts.most_common(10)
    ]


def _client_quality(client) -> dict:
    quality = getattr(client, "quality_report", None)
    return {
        "fallback_to_demo": bool(getattr(quality, "fallback_to_demo", False)),
        "source_status": list(getattr(quality, "source_status", [])),
        "warnings": list(getattr(quality, "warnings", [])),
    }


def _has_fallback(client) -> bool:
    quality = getattr(client, "quality_report", None)
    if bool(getattr(quality, "fallback_to_demo", False)):
        return True
    return any(
        "fallback to demo" in str(message).lower() or "using demo" in str(message).lower()
        for message in getattr(client, "fallback_messages", [])
    )


def _next_likely_trade_date(trade_date: str) -> str:
    current = date.fromisoformat(trade_date) + timedelta(days=1)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current.isoformat()


def _coerce_now(now: datetime | None) -> datetime:
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CN_TZ)
    return current.astimezone(CN_TZ)


def _final_view(status: str) -> str:
    return {
        "DEMO_ANALYSIS": "当前为演示观察池，仅用于流程演练，不可作为正式实盘观察。",
        "NOT_TRADING_DAY": "当前为非交易日，不生成正式盘后观察池。",
        "NOT_AFTER_CLOSE": "请在收盘后运行，才能生成正式盘后观察池。",
        "DATA_FALLBACK_DEMO": "Live 数据已回退到 demo，仅保留演练结果，不生成正式观察池。",
        "DATA_QUALITY_BLOCKED": "关键时效或安全字段不确定，正式观察池已阻断。",
        "WATCHLIST_READY": "已生成次日早盘观察结果，仅供人工观察确认。",
        "NO_WATCHLIST": "数据可用，但今天没有符合条件的正式观察行。",
        "NOT_REPLAY_WINDOW": "早盘 replay 仅允许在交易日连续竞价开始前运行。",
        "REPLAY_DATA_FALLBACK_DEMO": "早盘回放数据回退到 demo，不生成正式观察池。",
        "REPLAY_DATA_QUALITY_BLOCKED": "早盘回放存在关键时效或安全字段不确定，已阻断正式观察池。",
        "MORNING_REPLAY_READY": "已基于前一交易日收盘数据重建早盘观察结果，仅供人工确认。",
        "MORNING_REPLAY_NO_WATCHLIST": "前收盘数据可用，但没有符合条件的 replay 观察行。",
    }.get(status, "")


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _optional_float(value) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _user_reason_list(values: list[str]) -> list[str]:
    return _unique([_user_reason_text(value) for value in values if value])


def _user_reason_text(value: str) -> str:
    return USER_REASON_LABELS.get(value, value.replace("_", " "))


def _split_reason_keys(values: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    info_gap = [value for value in values if value in INFO_GAP_REASON_KEYS]
    missing = [value for value in values if value in MISSING_REASON_KEYS]
    risk = [value for value in values if value in RISK_REASON_KEYS]
    excluded = INFO_GAP_REASON_KEYS | MISSING_REASON_KEYS | RISK_REASON_KEYS
    positive = [value for value in values if value not in excluded]
    return positive, info_gap, missing, risk


def _apply_risk_flag_reasons(row: dict) -> None:
    mapped = [RISK_FLAG_REASON_KEYS[flag] for flag in row.get("risk_flags", []) if flag in RISK_FLAG_REASON_KEYS]
    if not mapped:
        return
    row["risk_reason_keys"] = _unique(list(row.get("risk_reason_keys") or []) + mapped)
    row["risk_reasons"] = "；".join(_user_reason_list(row["risk_reason_keys"]))
    reason_keys = _unique(
        list(row.get("positive_reason_keys") or [])
        + list(row.get("info_gap_reason_keys") or [])
        + list(row.get("missing_reason_keys") or [])
        + list(row.get("risk_reason_keys") or [])
    )
    row["reason"] = "；".join(_user_reason_list(reason_keys))


def _deep_update(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
