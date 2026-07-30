from __future__ import annotations

from typing import Any


def summarize_order_rows(
    rows: list[dict],
    current_prices: dict[str, float] | None = None,
) -> list[dict]:
    current_prices = current_prices or {}
    positions: dict[str, dict[str, Any]] = {}
    indexed_rows = list(enumerate(rows))
    indexed_rows.sort(key=lambda item: (str(item[1].get("trade_time") or ""), item[0]))
    for _, row in indexed_rows:
        code = str(row.get("code", "")).zfill(6)
        if not code:
            continue
        side = str(row.get("side") or "BUY").upper()
        qty = _as_int(row.get("qty") or row.get("quantity"))
        price = _as_float(row.get("price") or row.get("buy_price"))
        amount = _as_float(row.get("amount")) or round(qty * price, 2)
        position = positions.setdefault(code, _empty_position(code, row))
        if side == "BUY":
            _apply_buy(position, row, qty, amount)
        elif side == "SELL":
            _apply_sell(position, row, qty, amount)

    summaries = []
    for position in positions.values():
        if position["open_qty"] > 0:
            average_cost = round(position["open_cost"] / position["open_qty"], 4)
            position["avg_buy_price"] = average_cost
            position["buy_price"] = average_cost
        position["realized_pnl"] = round(position["realized_pnl"], 2)
        current_price = _as_float(current_prices.get(position["code"], 0))
        if current_price and position["open_qty"] > 0:
            position["unrealized_pnl"] = round(
                (current_price - position["avg_buy_price"]) * position["open_qty"],
                2,
            )
        position["status"] = _position_status(position)
        position.pop("open_cost", None)
        position.pop("cycle_sell_qty", None)
        summaries.append(position)
    return summaries


def _empty_position(code: str, row: dict) -> dict[str, Any]:
    return {
        "code": code,
        "name": row.get("name", ""),
        "open_qty": 0,
        "buy_qty": 0,
        "sell_qty": 0,
        "buy_amount": 0.0,
        "sell_amount": 0.0,
        "open_cost": 0.0,
        "cycle_sell_qty": 0,
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
    }


def _apply_buy(position: dict[str, Any], row: dict, qty: int, amount: float) -> None:
    if position["open_qty"] == 0:
        position["open_cost"] = 0.0
        position["cycle_sell_qty"] = 0
    position["buy_qty"] += qty
    position["open_qty"] += qty
    position["buy_amount"] += amount
    position["open_cost"] += amount
    position["buy_price"] = round(position["open_cost"] / position["open_qty"], 4)
    position["avg_buy_price"] = position["buy_price"]
    position["last_buy_time"] = row.get("trade_time", "")
    position["stop_loss_price"] = (
        _as_float(row.get("stop_loss_price") or row.get("stop_loss"))
        or position["stop_loss_price"]
    )
    position["source_ticket_path"] = row.get("source_ticket_path") or position["source_ticket_path"]
    position["ticket_id"] = row.get("ticket_id") or position["ticket_id"]
    position["strategy_name"] = row.get("strategy_name") or position["strategy_name"]
    position["name"] = row.get("name") or position["name"]
    position["buy_rows"].append(row)


def _apply_sell(position: dict[str, Any], row: dict, qty: int, amount: float) -> None:
    open_qty_before = int(position["open_qty"])
    if open_qty_before > 0:
        average_cost = float(position["open_cost"]) / open_qty_before
        matched_qty = min(qty, open_qty_before)
        sell_cost = average_cost * matched_qty
        position["open_cost"] = max(0.0, float(position["open_cost"]) - sell_cost)
        position["realized_pnl"] += amount - sell_cost
    position["sell_qty"] += qty
    position["open_qty"] -= qty
    position["sell_amount"] += amount
    position["cycle_sell_qty"] += qty
    position["last_sell_time"] = row.get("trade_time", "")
    position["sell_rows"].append(row)
    if position["open_qty"] == 0:
        position["open_cost"] = 0.0


def _position_status(position: dict[str, Any]) -> str:
    open_qty = int(position.get("open_qty", 0))
    buy_qty = int(position.get("buy_qty", 0))
    sell_qty = int(position.get("sell_qty", 0))
    if open_qty < 0 or sell_qty > buy_qty:
        return "ERROR_OVER_SOLD"
    if open_qty > 0:
        return "PARTIALLY_CLOSED" if int(position.get("cycle_sell_qty", 0)) > 0 else "OPEN"
    if buy_qty > 0:
        return "CLOSED"
    return "OPEN"


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0
