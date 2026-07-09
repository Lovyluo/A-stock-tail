from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path


def build_manual_ticket(stock: dict, risk: dict, config: dict, trade_date: str) -> dict:
    max_order = float(config.get("risk", {}).get("max_order_value", 5000))
    price = float(stock.get("price", 0) or 0)
    quantity = _board_lot_quantity(max_order, price)
    suggested_amount = round(quantity * price, 2)
    stop_loss_pct = abs(float(config.get("risk", {}).get("hard_stop_loss_pct", -3))) / 100
    decision_time = config.get("scan", {}).get("buy_decision_time", "14:45")
    return {
        "strategy": config.get("strategy", {}).get("name", "yang_yongxing_overnight_v1"),
        "date": trade_date,
        "generated_at": f"{trade_date} {decision_time}:00",
        "direction": "BUY",
        "code": stock.get("code", ""),
        "name": stock.get("name", ""),
        "suggested_price": round(price, 2),
        "max_acceptable_price": round(price * 1.015, 2),
        "suggested_amount": suggested_amount,
        "suggested_quantity": quantity,
        "stop_loss": round(price * (1 - stop_loss_pct), 2),
        "next_day_plan": "Sell next trading day according to open/pullback rules before 10:30 unless limit-up watch is active.",
        "risk_level": risk.get("risk_level", "MEDIUM"),
        "total_score": stock.get("total_score", 0),
        "risk_reasons": risk.get("reasons", []),
    }


def save_manual_ticket(ticket: dict, reports_dir: str) -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / f"manual_order_ticket_{ticket['date']}_{ticket['code']}.md"
    lines = [
        "======== Manual Order Ticket ========",
        f"Strategy: {ticket['strategy']}",
        f"Date: {ticket['date']}",
        f"Generated At: {ticket.get('generated_at', '')}",
        f"Direction: {ticket['direction']}",
        f"Code: {ticket['code']}",
        f"Name: {ticket['name']}",
        f"Suggested Price: {ticket['suggested_price']}",
        f"Max Acceptable Price: {ticket['max_acceptable_price']}",
        f"Suggested Amount: {ticket['suggested_amount']}",
        f"Suggested Quantity: {ticket['suggested_quantity']}",
        f"Stop Loss: {ticket['stop_loss']}",
        f"Next-Day Plan: {ticket['next_day_plan']}",
        f"Risk Level: {ticket['risk_level']}",
        f"Total Score: {ticket['total_score']}",
        "Confirmations:",
        "[ ] I confirm this is a manual order, not automated trading.",
        "[ ] I confirm the single-trade loss is acceptable.",
        "[ ] I confirm I will not chase above the max acceptable price.",
        "=====================================",
        "",
    ]
    file_path.write_text("\n".join(lines), encoding="utf-8")
    return str(file_path)


def append_signal_csv(rows: list[dict], records_dir: str, file_name: str = "signals.csv") -> str:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / file_name
    fieldnames = [
        "time",
        "code",
        "name",
        "decision",
        "total_score",
        "price",
        "change_pct",
        "vol_ratio",
        "turnover_pct",
        "amount_wan",
        "float_mcap_yi",
        "theme_tags",
        "risk_flags",
        "main_net_source",
        "capital_score_source",
        "estimated_capital_flow",
        "chip_peak_type",
        "chip_avg_cost_20d",
        "current_vs_chip_cost_pct",
        "volume_signal",
        "confidence_delta",
        "chip_volume_reasons",
        "reasons",
    ]
    with file_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "code": row.get("code", ""),
                    "name": row.get("name", ""),
                    "decision": row.get("decision", ""),
                    "total_score": row.get("total_score", ""),
                    "price": row.get("price", ""),
                    "change_pct": row.get("change_pct", ""),
                    "vol_ratio": row.get("vol_ratio", ""),
                    "turnover_pct": row.get("turnover_pct", ""),
                    "amount_wan": row.get("amount_wan", ""),
                    "float_mcap_yi": row.get("float_mcap_yi", ""),
                    "theme_tags": "|".join(row.get("theme_tags") or []),
                    "risk_flags": "|".join(row.get("risk_flags") or []),
                    "main_net_source": row.get("main_net_source", ""),
                    "capital_score_source": row.get("capital_score_source", ""),
                    "estimated_capital_flow": row.get("estimated_capital_flow", ""),
                    "chip_peak_type": row.get("chip_peak_type", ""),
                    "chip_avg_cost_20d": row.get("chip_avg_cost_20d", ""),
                    "current_vs_chip_cost_pct": row.get("current_vs_chip_cost_pct", ""),
                    "volume_signal": row.get("volume_signal", ""),
                    "confidence_delta": row.get("confidence_delta", ""),
                    "chip_volume_reasons": row.get("chip_volume_reasons", ""),
                    "reasons": "|".join(row.get("all_reasons") or row.get("score_reasons") or []),
                }
            )
    return str(file_path)


def _board_lot_quantity(amount: float, price: float) -> int:
    if price <= 0:
        return 0
    lots = int(amount // price // 100)
    return max(100, lots * 100) if lots > 0 else 0
