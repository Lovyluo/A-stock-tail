from __future__ import annotations

import csv
from pathlib import Path


def read_order_rows(records_dir: str) -> list[dict]:
    path = Path(records_dir) / "manual_orders.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def get_open_positions(records_dir: str) -> list[dict]:
    return [pos for pos in get_position_summaries(records_dir) if int(pos.get("open_qty", 0)) > 0]


def get_position_summaries(records_dir: str, current_prices: dict[str, float] | None = None) -> list[dict]:
    current_prices = current_prices or {}
    positions: dict[str, dict] = {}
    for row in read_order_rows(records_dir):
        code = str(row.get("code", "")).zfill(6)
        if not code:
            continue
        side = str(row.get("side") or "BUY").upper()
        qty = _as_int(row.get("qty") or row.get("quantity"))
        price = _as_float(row.get("price") or row.get("buy_price"))
        amount = _as_float(row.get("amount")) or round(qty * price, 2)
        pos = positions.setdefault(
            code,
            {
                "code": code,
                "name": row.get("name", ""),
                "open_qty": 0,
                "buy_qty": 0,
                "sell_qty": 0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "buy_price": 0.0,
                "avg_buy_price": 0.0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "status": "OPEN",
                "stop_loss_price": _as_float(row.get("stop_loss_price") or row.get("stop_loss")),
                "source_ticket_path": row.get("source_ticket_path", ""),
                "ticket_id": row.get("ticket_id", ""),
                "strategy_name": row.get("strategy_name") or row.get("strategy", ""),
                "trade_date": row.get("trade_date", ""),
                "last_buy_time": "",
                "last_sell_time": "",
                "buy_rows": [],
                "sell_rows": [],
            },
        )
        if side == "BUY":
            pos["buy_qty"] += qty
            pos["open_qty"] += qty
            pos["buy_amount"] += amount
            pos["buy_price"] = round(pos["buy_amount"] / pos["buy_qty"], 4) if pos["buy_qty"] else 0.0
            pos["avg_buy_price"] = pos["buy_price"]
            pos["last_buy_time"] = row.get("trade_time", "")
            pos["stop_loss_price"] = _as_float(row.get("stop_loss_price") or row.get("stop_loss")) or pos["stop_loss_price"]
            pos["source_ticket_path"] = row.get("source_ticket_path") or pos["source_ticket_path"]
            pos["ticket_id"] = row.get("ticket_id") or pos["ticket_id"]
            pos["strategy_name"] = row.get("strategy_name") or pos["strategy_name"]
            pos["name"] = row.get("name") or pos["name"]
            pos["buy_rows"].append(row)
        elif side == "SELL":
            pos["sell_qty"] += qty
            pos["open_qty"] -= qty
            pos["sell_amount"] += amount
            pos["last_sell_time"] = row.get("trade_time", "")
            pos["sell_rows"].append(row)
    summaries = []
    for pos in positions.values():
        avg_buy = round(pos["buy_amount"] / pos["buy_qty"], 4) if pos["buy_qty"] else 0.0
        pos["avg_buy_price"] = avg_buy
        pos["buy_price"] = avg_buy
        realized_cost = avg_buy * min(pos["sell_qty"], pos["buy_qty"])
        pos["realized_pnl"] = round(pos["sell_amount"] - realized_cost, 2)
        current_price = _as_float(current_prices.get(pos["code"], 0))
        if current_price and pos["open_qty"] > 0:
            pos["unrealized_pnl"] = round((current_price - avg_buy) * pos["open_qty"], 2)
        pos["status"] = _position_status(pos)
        summaries.append(pos)
    return summaries


def has_open_position(records_dir: str, code: str) -> bool:
    return any(pos["code"] == str(code).zfill(6) for pos in get_open_positions(records_dir))


def _position_status(pos: dict) -> str:
    open_qty = int(pos.get("open_qty", 0))
    buy_qty = int(pos.get("buy_qty", 0))
    sell_qty = int(pos.get("sell_qty", 0))
    if open_qty < 0 or sell_qty > buy_qty:
        return "ERROR_OVER_SOLD"
    if buy_qty > 0 and sell_qty == 0 and open_qty > 0:
        return "OPEN"
    if buy_qty > 0 and sell_qty > 0 and open_qty > 0:
        return "PARTIALLY_CLOSED"
    if buy_qty > 0 and sell_qty == buy_qty and open_qty == 0:
        return "CLOSED"
    return "OPEN"


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0
