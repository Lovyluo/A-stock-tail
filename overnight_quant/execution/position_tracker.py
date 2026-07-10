from __future__ import annotations

import csv
from pathlib import Path


NAME_BINDING_FIELDS = ["code", "name", "source_order_id", "updated_at", "notes"]
ACTIVE_STATUSES = {"", "FILLED", "RECORDED"}


def read_order_rows(records_dir: str) -> list[dict]:
    path = Path(records_dir) / "manual_orders.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_name_bindings(records_dir: str) -> dict[str, str]:
    path = Path(records_dir) / "stock_name_bindings.csv"
    if not path.exists():
        return {}
    bindings: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            code = _normalize_code(row.get("code"))
            name = str(row.get("name") or "").strip()
            if code and name:
                bindings[code] = name
    return bindings


def write_name_binding(
    records_dir: str,
    code: str,
    name: str,
    source_order_id: str = "",
    updated_at: str = "",
    notes: str = "",
) -> str:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / "stock_name_bindings.csv"
    code = _normalize_code(code)
    name = str(name or "").strip()
    if not code or not name:
        return str(file_path)

    rows: dict[str, dict] = {}
    if file_path.exists():
        with file_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                existing_code = _normalize_code(row.get("code"))
                if existing_code:
                    rows[existing_code] = dict(row)
    rows[code] = {
        "code": code,
        "name": name,
        "source_order_id": source_order_id,
        "updated_at": updated_at,
        "notes": notes,
    }
    with file_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=NAME_BINDING_FIELDS)
        writer.writeheader()
        for item in rows.values():
            writer.writerow({field: item.get(field, "") for field in NAME_BINDING_FIELDS})
    return str(file_path)


def active_order_rows(records_dir: str) -> list[dict]:
    return [row for row in read_order_rows(records_dir) if _is_active_fill(row)]


def get_open_positions(records_dir: str) -> list[dict]:
    return [pos for pos in get_position_summaries(records_dir) if int(pos.get("open_qty", 0)) > 0]


def get_position_summaries(records_dir: str, current_prices: dict[str, float] | None = None) -> list[dict]:
    return build_position_summaries(
        read_order_rows(records_dir),
        current_prices=current_prices,
        name_bindings=read_name_bindings(records_dir),
    )


def build_position_summaries(
    rows: list[dict],
    current_prices: dict[str, float] | None = None,
    name_bindings: dict[str, str] | None = None,
) -> list[dict]:
    current_prices = current_prices or {}
    name_bindings = name_bindings or {}
    summaries: list[dict] = []
    current_by_code: dict[str, dict] = {}
    cycle_counts: dict[str, int] = {}
    for row in rows:
        if not _is_active_fill(row):
            continue
        code = _normalize_code(row.get("code"))
        if not code:
            continue
        side = str(row.get("side") or "BUY").upper()
        if side not in {"BUY", "SELL"}:
            continue
        qty = _as_int(row.get("qty") or row.get("quantity"))
        price = _as_float(row.get("price") or row.get("buy_price"))
        amount = _as_float(row.get("amount")) or round(qty * price, 2)
        pos = current_by_code.get(code)
        if side == "BUY":
            if pos and _position_status(pos) == "CLOSED":
                _finalize_position(pos, current_prices)
                summaries.append(pos)
                pos = None
            if not pos:
                cycle_counts[code] = cycle_counts.get(code, 0) + 1
                pos = _new_position(code, row, cycle_counts[code], name_bindings)
                current_by_code[code] = pos
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
            if not name_bindings.get(code):
                pos["name"] = pos["name"] or row.get("name", "")
            pos["buy_rows"].append(row)
        elif side == "SELL":
            if not pos:
                cycle_counts[code] = cycle_counts.get(code, 0) + 1
                pos = _new_position(code, row, cycle_counts[code], name_bindings)
                current_by_code[code] = pos
            pos["sell_qty"] += qty
            pos["open_qty"] -= qty
            pos["sell_amount"] += amount
            pos["last_sell_time"] = row.get("trade_time", "")
            pos["sell_rows"].append(row)
    for pos in current_by_code.values():
        _finalize_position(pos, current_prices)
        summaries.append(pos)
    return summaries


def has_open_position(records_dir: str, code: str) -> bool:
    return any(pos["code"] == str(code).zfill(6) for pos in get_open_positions(records_dir))


def _new_position(code: str, row: dict, cycle_no: int, name_bindings: dict[str, str]) -> dict:
    return {
        "position_id": f"{code}-{cycle_no}",
        "cycle_no": cycle_no,
        "code": code,
        "name": name_bindings.get(code) or row.get("name", ""),
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
        "first_buy_time": "",
        "last_buy_time": "",
        "last_sell_time": "",
        "closed_at": "",
        "buy_rows": [],
        "sell_rows": [],
    }


def _finalize_position(pos: dict, current_prices: dict[str, float]) -> None:
    avg_buy = round(pos["buy_amount"] / pos["buy_qty"], 4) if pos["buy_qty"] else 0.0
    pos["avg_buy_price"] = avg_buy
    pos["buy_price"] = avg_buy
    realized_cost = avg_buy * min(pos["sell_qty"], pos["buy_qty"])
    pos["realized_pnl"] = round(pos["sell_amount"] - realized_cost, 2)
    current_price = _as_float(current_prices.get(pos["code"], 0))
    if current_price and pos["open_qty"] > 0:
        pos["unrealized_pnl"] = round((current_price - avg_buy) * pos["open_qty"], 2)
    pos["status"] = _position_status(pos)
    if pos["status"] == "CLOSED":
        pos["closed_at"] = pos.get("last_sell_time", "")
    if pos.get("buy_rows") and not pos.get("first_buy_time"):
        pos["first_buy_time"] = pos["buy_rows"][0].get("trade_time", "")


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


def _is_active_fill(row: dict) -> bool:
    status = str(row.get("status") or "FILLED").upper()
    return status in ACTIVE_STATUSES


def _normalize_code(value) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


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
