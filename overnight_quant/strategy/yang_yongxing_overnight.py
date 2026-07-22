from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

from overnight_quant.execution.manual_ticket import append_signal_csv, build_manual_ticket, save_manual_ticket
from overnight_quant.reports.scan_reports import write_scan_summary
from overnight_quant.risk.risk_manager import RiskManager
from overnight_quant.strategy.chip_volume import build_chip_volume_confidence
from overnight_quant.strategy.filters import evaluate_market_gate, evaluate_tail_stability, initial_filter
from overnight_quant.strategy.scoring import rank_scored, score_stock


DEFAULT_CONFIG = {
    "strategy": {"name": "yang_yongxing_overnight_v1", "mode": "conservative", "min_total_score": 75, "final_pick_count": 1},
    "scan": {"default_mode": "demo", "max_candidates": 100},
    "after_close": {"max_a_count": 5, "max_b_count": 10, "max_c_count": 10, "min_a_score": 80, "min_b_score": 70, "min_c_score": 60},
    "health_check": {"enabled": True, "show_in_dashboard": False},
    "auction": {
        "enabled": True,
        "live_start": "09:25",
        "live_end": "09:30",
        "max_candidates": 50,
        "strong_gap_pct": 3.0,
        "weak_gap_pct": -2.0,
        "min_amount_wan": 1000,
    },
    "news_briefing": {
        "enabled": True,
        "lookback_start_time": "15:00",
        "morning_end_time": "09:25",
        "max_global_news": 80,
        "max_cls_news": 80,
        "max_stock_news_per_code": 10,
    },
    "tail_observation": {"live_start": "14:50", "live_end": "15:00", "after_close_replay_enabled": True},
    "dashboard": {"show_maintenance_actions": False, "hide_live_dry_run": True},
    "chip_volume": {
        "enabled": True,
        "profile_lookback_days": 60,
        "avg_cost_windows": [20, 60],
        "bucket_pct": 1.0,
        "high_volume_prev_days": 3,
        "volume_confirm_ratio": 1.2,
        "max_confidence_bonus": 8,
        "max_confidence_penalty": -10,
    },
    "filters": {
        "allowed_code_prefixes": ["00", "60"],
        "enforce_allowed_code_prefixes": False,
        "min_change_pct": 3,
        "max_change_pct": 7,
        "min_vol_ratio": 1,
        "min_turnover_pct": 5,
        "max_turnover_pct": 18,
        "min_amount_wan": 15000,
        "min_float_mcap_yi": 30,
        "max_float_mcap_yi": 250,
        "min_price": 3,
        "max_price": 80,
        "max_tail_pullback_pct": 3.5,
    },
    "trend": {"max_5d_gain_pct": 30, "max_10d_gain_pct": 45, "max_upper_shadow_ratio": 0.45},
    "risk": {
        "max_position_ratio_per_stock": 0.2,
        "max_order_value": 5000,
        "max_daily_trades": 2,
        "hard_stop_loss_pct": -3,
        "disaster_stop_loss_pct": -5,
        "no_trade_if_market_fail": True,
    },
    "sell": {"take_profit_pct_1": 3, "take_profit_pct_2": 6, "stop_loss_pct": -3, "force_exit_before": "10:30", "allow_hold_if_limit_up": True},
    "cost": {"commission_rate": 0.0003, "min_commission": 5, "stamp_tax_rate": 0.0005, "slippage_pct": 0.0},
    "backtest": {
        "initial_capital": 100000,
        "output_dir": "overnight_quant/backtest_outputs",
        "sample_data_dir": "overnight_quant/examples/historical",
        "local_data_dir": "overnight_quant/backtest_data/processed",
        "raw_data_dir": "overnight_quant/backtest_data/raw",
        "manifest_dir": "overnight_quant/backtest_data/manifests",
        "historical_cache_dir": "overnight_quant/backtest_data/cache/a_stock_data",
        "astock_default_max_codes": 10,
        "astock_min_sleep_seconds": 0.2,
        "preparation_sample_dir": "overnight_quant/examples/historical_prepare_raw",
        "preparation_positive_sample_dir": "overnight_quant/examples/historical_prepare_positive_raw",
        "intraday_assumption": "conservative",
    },
    "paths": {
        "records_dir": "overnight_quant/records",
        "reports_dir": "overnight_quant/reports",
        "examples_dir": "overnight_quant/examples",
    },
}

OBSERVATION_HARD_REJECT_REASONS = {
    "non_trading_day",
    "outside_tail_session",
    "quote_stale",
    "timestamp_missing",
    "freshness_unknown",
}


def load_config(path: str | None = None) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
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


class YangYongxingOvernightStrategy:
    def __init__(self, client, config: dict):
        self.client = client
        self.config = config
        self.risk_manager = RiskManager(config)

    def scan(self, trade_date: str | None = None, dry_run: bool = False) -> dict:
        trade_date = trade_date or date.today().isoformat()
        scan_config = copy.deepcopy(self.config)
        if getattr(self.client, "mode", "") == "live":
            scan_config.setdefault("filters", {})["enforce_allowed_code_prefixes"] = True
        records_dir = self.config.get("paths", {}).get("records_dir", "overnight_quant/records")
        reports_dir = self.config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
        market = self.client.get_market_snapshot()
        market_gate = evaluate_market_gate(market)
        _apply_live_session_gate(market_gate, market)
        market_score = market_gate["score"]
        scored_rows: list[dict] = []
        rejected: list[dict] = []
        eligible: list[dict] = []

        for stock in self.client.get_candidate_quotes()[: int(self.config.get("scan", {}).get("max_candidates", 100))]:
            initial = initial_filter(stock, scan_config)
            tail = evaluate_tail_stability(stock, scan_config)
            kline = self.client.get_daily_kline(stock.get("code", "")) if initial["pass"] and tail["pass"] else []
            scored = score_stock(stock, kline, market_score, scan_config)
            if scan_config.get("chip_volume", {}).get("enabled", True):
                chip_volume = build_chip_volume_confidence(
                    {**scored, "_chip_volume_config": scan_config.get("chip_volume", {})},
                    kline,
                )
                _apply_chip_volume_fields(
                    scored,
                    chip_volume,
                    min_score=float(scan_config.get("strategy", {}).get("min_total_score", 75)),
                )
            kline_freshness_reasons = (
                self.client.get_kline_freshness_reasons(stock.get("code", ""))
                if hasattr(self.client, "get_kline_freshness_reasons")
                else []
            )
            scored["_freshness_reasons"] = list(
                dict.fromkeys(list(scored.get("_freshness_reasons", [])) + kline_freshness_reasons)
            )
            scored["filter_reasons"] = initial["reasons"] + tail["reasons"]
            scored["filter_reject_reasons"] = initial["reject_reasons"] + tail["reject_reasons"]
            scored["all_reasons"] = (
                scored["filter_reasons"]
                + scored["filter_reject_reasons"]
                + scored["score_reasons"]
                + scored["_freshness_reasons"]
            )
            if initial["pass"] and tail["pass"]:
                eligible.append(scored)
            else:
                rejected.append(scored)
            scored_rows.append(scored)

        selected: list[dict] = []
        dry_run_selected: list[dict] = []
        tickets: list[str] = []
        daily_trade_count = 0
        final_count = int(self.config.get("strategy", {}).get("final_pick_count", 1))
        planned_amount = float(self.config.get("risk", {}).get("max_order_value", 5000))

        for stock in rank_scored(eligible):
            if len(selected) >= final_count or len(dry_run_selected) >= final_count:
                break
            risk = self.risk_manager.evaluate_buy(stock, market_gate, planned_amount, daily_trade_count)
            stock["risk_gate"] = risk
            if not risk["allow"]:
                rejected.append(stock)
                continue
            if dry_run:
                stock["dry_run_candidate"] = True
                dry_run_selected.append(stock)
                daily_trade_count += 1
                continue
            ticket = build_manual_ticket(stock, risk, self.config, trade_date)
            ticket_path = save_manual_ticket(ticket, reports_dir)
            stock["ticket_path"] = ticket_path
            tickets.append(ticket_path)
            selected.append(stock)
            daily_trade_count += 1

        signal_rows = dry_run_selected if dry_run else selected
        signals_csv = append_signal_csv(signal_rows, records_dir)
        signal_rejections_csv = append_signal_csv(rejected, records_dir, file_name="signal_rejections.csv")
        quality_report_path = ""
        if getattr(self.client, "mode", "") == "live" and hasattr(self.client, "write_quality_report"):
            quality_report = getattr(self.client, "quality_report", None)
            if quality_report is not None:
                quality_report.candidate_counts["scored"] = len(scored_rows)
            quality_report_path = self.client.write_quality_report(reports_dir, trade_date)
        candidate_context = _candidate_observation_context(self.client, market_gate, scored_rows)
        dry_run_report_path = ""
        scan_summary_path = ""
        if getattr(self.client, "mode", "") == "live":
            if dry_run:
                dry_run_report_path = write_scan_summary(
                    {
                        "market_gate": market_gate,
                        "candidate_count": len(scored_rows),
                        "rejected": rejected,
                        "selected": selected,
                        "dry_run_selected": dry_run_selected,
                        "tickets": tickets,
                        "quality_report_path": quality_report_path,
                        **candidate_context,
                    },
                    reports_dir,
                    trade_date,
                    dry_run=True,
                )
            else:
                scan_summary_path = write_scan_summary(
                    {
                        "market_gate": market_gate,
                        "candidate_count": len(scored_rows),
                        "rejected": rejected,
                        "selected": selected,
                        "dry_run_selected": dry_run_selected,
                        "tickets": tickets,
                        "quality_report_path": quality_report_path,
                        **candidate_context,
                    },
                    reports_dir,
                    trade_date,
                    dry_run=False,
                )
        return {
            "market_gate": market_gate,
            "candidate_count": len(scored_rows),
            "rejected": rejected,
            "scored": rank_scored(scored_rows),
            "selected": selected,
            "dry_run_selected": dry_run_selected,
            "tickets": tickets,
            "signals_csv": signals_csv,
            "signal_rejections_csv": signal_rejections_csv,
            "quality_report_path": quality_report_path,
            "dry_run_report_path": dry_run_report_path,
            "scan_summary_path": scan_summary_path,
            "fallback_messages": list(getattr(self.client, "fallback_messages", [])),
            **candidate_context,
        }


def _deep_update(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _apply_live_session_gate(market_gate: dict, market: dict) -> None:
    session_reasons = market.get("session_reject_reasons") or []
    if session_reasons:
        market_gate["pass"] = False
        for reason in session_reasons:
            if reason not in market_gate["reject_reasons"]:
                market_gate["reject_reasons"].append(reason)
    for key in ("trade_date", "run_time", "session_state", "is_trade_day"):
        if key in market:
            market_gate[key] = market[key]


def _apply_chip_volume_fields(stock: dict, chip_volume: dict, min_score: float = 75.0) -> None:
    stock["chip_volume"] = chip_volume
    stock["chip_peak_type"] = chip_volume.get("peak_type", "neutral")
    stock["chip_avg_cost_20d"] = chip_volume.get("chip_avg_cost_20d", 0.0)
    stock["chip_avg_cost_60d"] = chip_volume.get("chip_avg_cost_60d", 0.0)
    stock["current_vs_chip_cost_pct"] = chip_volume.get("current_vs_chip_cost_pct", 0.0)
    stock["overhead_pressure_ratio"] = chip_volume.get("overhead_pressure_ratio", 0.0)
    stock["downside_support_ratio"] = chip_volume.get("downside_support_ratio", 0.0)
    stock["main_force_chip_proxy"] = chip_volume.get("main_force_chip_proxy", 0.0)
    stock["volume_signal"] = chip_volume.get("volume_signal", "")
    stock["confidence_delta"] = chip_volume.get("confidence_delta", 0)
    reasons = [str(reason) for reason in chip_volume.get("reasons", [])]
    stock["chip_volume_reasons"] = "|".join(reasons)
    stock["score_reasons"] = list(dict.fromkeys(list(stock.get("score_reasons") or []) + reasons))
    base_score = float(stock.get("total_score", stock.get("score", 0)) or 0)
    adjusted = max(0.0, min(100.0, base_score + float(chip_volume.get("confidence_delta", 0) or 0)))
    stock["total_score"] = round(adjusted, 2)
    if "score" in stock:
        stock["score"] = round(adjusted, 2)
    stock["decision"] = "BUY_CANDIDATE" if adjusted >= min_score and not stock.get("risk_flags") else "REJECT"


def _candidate_observation_context(client, market_gate: dict, scored_rows: list[dict]) -> dict:
    count = len(scored_rows)
    if getattr(client, "mode", "") != "live":
        return {
            "candidate_source": "demo",
            "live_candidate_count": 0,
            "demo_candidate_count": count,
            "valid_for_trading_observation": False,
        }

    quality_report = getattr(client, "quality_report", None)
    fallback_to_demo = bool(getattr(quality_report, "fallback_to_demo", False))
    candidate_source = "demo_fallback" if fallback_to_demo else "live"
    reject_reasons = set(market_gate.get("reject_reasons") or [])
    for stock in scored_rows:
        reject_reasons.update(stock.get("_freshness_reasons") or [])
    blocked = bool(reject_reasons.intersection(OBSERVATION_HARD_REJECT_REASONS))

    return {
        "candidate_source": candidate_source,
        "live_candidate_count": 0 if fallback_to_demo else count,
        "demo_candidate_count": count if fallback_to_demo else 0,
        "valid_for_trading_observation": candidate_source == "live" and not blocked,
    }
