from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.execution.position_tracker import get_position_summaries, read_order_rows


def write_trade_review(config: dict, code: str, trade_date: str | None = None) -> dict:
    code = str(code).zfill(6)
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    rows = [row for row in read_order_rows(records_dir) if str(row.get("code", "")).zfill(6) == code]
    buy_rows = [row for row in rows if str(row.get("side") or "BUY").upper() == "BUY"]
    sell_rows = [row for row in rows if str(row.get("side") or "").upper() == "SELL"]
    latest_buy = buy_rows[-1] if buy_rows else {}
    latest_sell = sell_rows[-1] if sell_rows else {}
    summary = _find_position_summary(records_dir, code)
    ticket = _read_ticket(latest_buy.get("source_ticket_path", ""))
    signal = _read_signal(records_dir, code)
    trade_date = trade_date or (latest_sell.get("trade_date") or latest_buy.get("trade_date") or "")
    sell_plan = _read_latest_sell_plan(reports_dir, trade_date)
    buy_amount = sum(_as_float(row.get("amount")) for row in buy_rows)
    sell_amount = sum(_as_float(row.get("amount")) for row in sell_rows)
    realized_qty = min(int(summary.get("sell_qty", 0) or 0), int(summary.get("buy_qty", 0) or 0))
    realized_buy_amount = round(_as_float(summary.get("avg_buy_price")) * realized_qty, 2)
    gross_pnl = round(_as_float(summary.get("realized_pnl")), 2) if buy_rows and sell_rows else 0.0
    fees = _calculate_fees(config, realized_buy_amount, sell_amount, bool(realized_qty), bool(sell_rows))
    fee_estimate = round(fees["buy_fee"] + fees["sell_fee"] + fees["stamp_tax"], 2)
    net_pnl = round(gross_pnl - fee_estimate, 2) if buy_rows and sell_rows else 0.0
    return_pct = round(net_pnl / realized_buy_amount * 100, 2) if realized_buy_amount else 0.0
    lifecycle = _read_lifecycle(reports_dir, trade_date)
    conclusion = _review_conclusion(ticket, latest_buy, latest_sell, sell_rows, sell_plan, summary)
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    report = path / f"trade_review_{trade_date}_{code}.md"
    lines = [
        "# Trade Review",
        "",
        f"strategy_name: {latest_buy.get('strategy_name') or ticket.get('strategy') or signal.get('strategy_name', '')}",
        f"code: {code}",
        f"name: {latest_buy.get('name') or signal.get('name', '')}",
        f"buy_ticket_path: {latest_buy.get('source_ticket_path', '')}",
        "",
        "## Buy Plan",
        f"suggested_price: {ticket.get('suggested_price', '')}",
        f"max_acceptable_price: {ticket.get('max_acceptable_price', '')}",
        f"suggested_quantity: {ticket.get('suggested_quantity', '')}",
        f"stop_loss_price: {ticket.get('stop_loss', latest_buy.get('stop_loss_price', ''))}",
        "",
        "## Actual BUY",
        f"buy_price: {latest_buy.get('price', '')}",
        f"buy_qty: {latest_buy.get('qty', '')}",
        f"buy_amount: {round(buy_amount, 2)}",
        f"buy_above_suggested: {_as_float(latest_buy.get('price')) > _as_float(ticket.get('suggested_price')) if latest_buy else ''}",
        f"buy_above_max_acceptable: {_as_float(latest_buy.get('price')) > _as_float(ticket.get('max_acceptable_price')) if latest_buy else ''}",
        "",
        "## Sell Plan",
        f"planned_action: {sell_plan.get('action', '')}",
        f"take_profit_stop_loss: {sell_plan.get('reason', '') or 'see sell plan'}",
        f"force_exit_before: {sell_plan.get('force_exit_before', '')}",
        "",
        "## Actual SELL",
        f"sell_price: {latest_sell.get('price', '')}",
        f"sell_qty: {latest_sell.get('qty', '')}",
        f"sell_amount: {round(sell_amount, 2)}",
        f"sell_below_stop_loss: {_as_float(latest_sell.get('price')) < _as_float(latest_buy.get('stop_loss_price')) if latest_sell else ''}",
        f"sell_after_force_exit: {_sell_after_force_exit(latest_sell.get('trade_time', ''), sell_plan.get('force_exit_before', '')) if latest_sell else ''}",
        "",
        "## PnL",
        f"gross_pnl: {gross_pnl}",
        f"buy_fee: {fees['buy_fee']}",
        f"sell_fee: {fees['sell_fee']}",
        f"stamp_tax: {fees['stamp_tax']}",
        f"fee_estimate: {fee_estimate}",
        f"net_pnl: {net_pnl}",
        f"return_pct: {return_pct}",
        f"realized_buy_cost: {realized_buy_amount}",
        "",
        "## Discipline",
        f"ticket_buy_ok: {bool(latest_buy) and _as_float(latest_buy.get('price')) <= _as_float(ticket.get('max_acceptable_price'))}",
        f"chased_above_max: {_as_float(latest_buy.get('price')) > _as_float(ticket.get('max_acceptable_price')) if latest_buy else ''}",
        f"sell_plan_handled: {bool(latest_sell)}",
        f"stop_loss_violation: {_as_float(latest_sell.get('price')) < _as_float(latest_buy.get('stop_loss_price')) if latest_sell else ''}",
        f"late_exit: {_sell_after_force_exit(latest_sell.get('trade_time', ''), sell_plan.get('force_exit_before', '')) if latest_sell else ''}",
        f"position_status: {summary.get('status', '')}",
        f"lifecycle_status: {lifecycle.get('status', '')}",
        "",
        f"conclusion: {conclusion}",
        "",
        "Risk warning: manual execution only; not investment advice.",
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    return {
        "path": str(report),
        "gross_pnl": gross_pnl,
        "fee_estimate": fee_estimate,
        "net_pnl": net_pnl,
        "return_pct": return_pct,
        "conclusion": conclusion,
    }


def _find_position_summary(records_dir: str, code: str) -> dict:
    for summary in get_position_summaries(records_dir):
        if summary["code"] == code:
            return summary
    return {}


def _read_ticket(path_value: str) -> dict:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.is_file():
        return {}
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower().replace(" ", "_").replace("-", "_")] = value.strip()
    return data


def _read_signal(records_dir: str, code: str) -> dict:
    path = Path(records_dir) / "signals.csv"
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("code", "")).zfill(6) == code:
                return dict(row)
    return {}


def _read_latest_sell_plan(reports_dir: str, trade_date: str = "") -> dict:
    path = Path(reports_dir)
    plans = sorted(path.glob("sell_plan_*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not plans:
        return {}
    if trade_date:
        eligible = [item for item in plans if _sell_plan_date(item) <= trade_date]
        if eligible:
            plans = sorted(eligible, key=_sell_plan_date, reverse=True)
    data = {}
    for line in plans[0].read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower().replace(" ", "_")] = value.strip()
    return data


def _sell_plan_date(path: Path) -> str:
    return path.stem.replace("sell_plan_", "", 1)


def _read_lifecycle(reports_dir: str, trade_date: str) -> dict:
    path = Path(reports_dir) / f"trade_lifecycle_{trade_date}.md"
    if not path.is_file():
        return {}
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower().replace(" ", "_")] = value.strip()
    return data


def _calculate_fees(config: dict, buy_amount: float, sell_amount: float, has_buy: bool, has_sell: bool) -> dict:
    cost = config.get("cost", {})
    commission_rate = _as_float(cost.get("commission_rate", 0.0003))
    min_commission = _as_float(cost.get("min_commission", 5))
    stamp_tax_rate = _as_float(cost.get("stamp_tax_rate", 0.0005))
    buy_fee = max(round(buy_amount * commission_rate, 2), min_commission) if has_buy else 0.0
    sell_fee = max(round(sell_amount * commission_rate, 2), min_commission) if has_sell else 0.0
    stamp_tax = round(sell_amount * stamp_tax_rate, 2) if has_sell else 0.0
    return {"buy_fee": buy_fee, "sell_fee": sell_fee, "stamp_tax": stamp_tax}


def _review_conclusion(ticket: dict, latest_buy: dict, latest_sell: dict, sell_rows: list[dict], sell_plan: dict, summary: dict) -> str:
    if not latest_buy or not ticket or not sell_rows:
        return "INCOMPLETE_TRADE"
    if _as_float(latest_buy.get("price")) > _as_float(ticket.get("max_acceptable_price")):
        return "BUY_VIOLATION"
    if _as_float(latest_sell.get("price")) < _as_float(latest_buy.get("stop_loss_price")):
        return "STOP_LOSS_VIOLATION"
    if _sell_after_force_exit(latest_sell.get("trade_time", ""), sell_plan.get("force_exit_before", "")):
        return "SELL_VIOLATION"
    if summary.get("status") != "CLOSED":
        return "INCOMPLETE_TRADE"
    return "EXECUTION_OK"


def _sell_after_force_exit(trade_time: str, force_exit_before: str) -> bool:
    if not trade_time or not force_exit_before or len(trade_time) < 16:
        return False
    return trade_time[11:16] > force_exit_before


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
