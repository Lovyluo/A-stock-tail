from __future__ import annotations


def decide_sell_action(order: dict, current: dict, config: dict) -> dict:
    buy_price = float(order.get("buy_price", 0) or order.get("suggested_price", 0) or 0)
    price = float(current.get("price", 0) or 0)
    open_change = float(current.get("open_change_pct", 0) or 0)
    if buy_price <= 0 or price <= 0:
        return {"action": "SELL_NOW", "level": "DATA_MISSING", "reason": "missing_price_data", "pnl_pct": 0.0}

    pnl_pct = round((price / buy_price - 1) * 100, 2)
    if current.get("is_limit_down"):
        return {"action": "LIMIT_DOWN_RISK", "level": "G", "reason": "limit_down_cannot_exit_normally", "pnl_pct": pnl_pct}
    if current.get("is_limit_up") and config.get("sell", {}).get("allow_hold_if_limit_up", True):
        return {"action": "LIMIT_UP_WATCH", "level": "F", "reason": "limit_up_watch_open_break", "pnl_pct": pnl_pct}
    if open_change >= 3 or pnl_pct >= float(config.get("sell", {}).get("take_profit_pct_1", 3)):
        return {"action": "TAKE_PROFIT", "level": "A", "reason": "gap_or_profit_reached", "pnl_pct": pnl_pct}
    if pnl_pct <= float(config.get("sell", {}).get("stop_loss_pct", -3)) or open_change <= -3:
        return {"action": "STOP_LOSS", "level": "E", "reason": "stop_loss_or_large_low_open", "pnl_pct": pnl_pct}
    if 1 <= open_change < 3:
        return {"action": "WAIT_10_MIN", "level": "B", "reason": "moderate_high_open_watch_strength", "pnl_pct": pnl_pct}
    if -1 <= open_change < 1:
        return {"action": "WAIT_10_MIN", "level": "C", "reason": "flat_open_watch_until_10", "pnl_pct": pnl_pct}
    if -3 < open_change < -1:
        return {"action": "SELL_NOW", "level": "D", "reason": "low_open_sell_on_failed_rebound", "pnl_pct": pnl_pct}
    return {"action": "SELL_NOW", "level": "NORMAL", "reason": "default_next_day_exit", "pnl_pct": pnl_pct}

