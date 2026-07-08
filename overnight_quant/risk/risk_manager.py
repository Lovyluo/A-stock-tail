from __future__ import annotations

from overnight_quant.data.live_data_quality import SAFETY_FIELD_REASONS


class RiskManager:
    def __init__(self, config: dict):
        self.config = config

    def evaluate_buy(self, stock: dict, market_gate: dict, planned_amount: float, daily_trade_count: int = 0) -> dict:
        reasons: list[str] = []
        risk_config = self.config.get("risk", {})
        strategy_config = self.config.get("strategy", {})

        if risk_config.get("no_trade_if_market_fail", True) and not market_gate.get("pass"):
            reasons.append("market_gate_fail")
            for reason in market_gate.get("reject_reasons", []):
                if reason not in reasons:
                    reasons.append(reason)
        if float(stock.get("total_score", 0) or 0) < float(strategy_config.get("min_total_score", 75)):
            reasons.append("score_below_threshold")
        for reason in stock.get("risk_flags", []):
            if reason not in reasons:
                reasons.append(reason)
        for reason in stock.get("_risk_unknown_reasons", []):
            if reason not in reasons:
                reasons.append(reason)
        for reason in stock.get("_freshness_reasons", []):
            if reason not in reasons:
                reasons.append(reason)
        for field_name in stock.get("_missing_fields", []):
            reason = SAFETY_FIELD_REASONS.get(field_name)
            if reason and reason not in reasons:
                reasons.append(reason)
        raw_flags = {
            "is_st": "st_stock",
            "is_suspended": "suspended",
            "is_new_stock": "new_stock",
            "is_limit_up": "limit_up_unavailable",
            "is_bj": "bj_stock",
            "is_bj_stock": "bj_stock",
        }
        for field, reason in raw_flags.items():
            if stock.get(field) and reason not in reasons:
                reasons.append(reason)
        if planned_amount > float(risk_config.get("max_order_value", 5000)):
            reasons.append("order_value_exceeds_limit")
        if daily_trade_count >= int(risk_config.get("max_daily_trades", 2)):
            reasons.append("daily_trade_limit_reached")

        hard_stop = abs(float(risk_config.get("hard_stop_loss_pct", -3))) / 100
        max_loss = round(planned_amount * hard_stop, 2)
        allow = not reasons
        if not allow:
            risk_level = "HIGH"
        elif float(stock.get("total_score", 0) or 0) >= 85:
            risk_level = "LOW"
        else:
            risk_level = "MEDIUM"
        return {
            "allow": allow,
            "reason": "PASS" if allow else "; ".join(reasons),
            "reasons": reasons,
            "risk_level": risk_level,
            "max_loss_amount": max_loss,
        }
