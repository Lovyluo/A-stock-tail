from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from overnight_quant.execution.position_tracker import (
    get_open_positions,
    read_name_bindings,
    read_order_rows,
    write_name_binding,
)
from overnight_quant.reports.lifecycle_report import write_trade_lifecycle_report
from overnight_quant.reports.trade_review import write_trade_review


ORDER_FIELDS = [
    "order_id",
    "ticket_id",
    "strategy_name",
    "trade_date",
    "trade_time",
    "code",
    "name",
    "side",
    "price",
    "qty",
    "amount",
    "max_acceptable_price",
    "stop_loss_price",
    "source_ticket_path",
    "recorded_at",
    "status",
    "notes",
]


def record_manual_order(
    config: dict,
    code: str,
    price: float,
    qty: int,
    side: str,
    trade_time: str,
    notes: str = "",
) -> dict:
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    code = str(code).zfill(6)
    side = str(side).upper()
    ticket = find_order_ticket(reports_dir, records_dir, code, side, trade_time)
    existing = read_order_rows(records_dir)
    reasons = _validate(config, ticket, existing, code, price, qty, side, trade_time, records_dir)
    report_path = _write_record_report(reports_dir, code, side, trade_time, not reasons, reasons)
    if reasons:
        return {"allow": False, "reasons": reasons, "report_path": report_path}

    row = _build_order_row(ticket, code, price, qty, side, trade_time, notes)
    if side == "BUY":
        bound_name = _resolve_bound_name(records_dir, existing, code, row.get("name", ""))
        if bound_name:
            row["name"] = bound_name
    orders_csv = append_manual_order(row, records_dir)
    name_binding_path = ""
    if side == "BUY" and row.get("name"):
        name_binding_path = write_name_binding(
            records_dir,
            code,
            str(row.get("name", "")),
            source_order_id=str(row.get("order_id", "")),
            updated_at=datetime.now().isoformat(timespec="seconds"),
            notes="auto_bound_from_ticket_buy",
        )
    review_report_path = ""
    lifecycle_report_path = write_trade_lifecycle_report(config, row["trade_date"])
    if side == "SELL":
        review = write_trade_review(config, code, row["trade_date"])
        review_report_path = review["path"]
        lifecycle_report_path = write_trade_lifecycle_report(
            config,
            row["trade_date"],
            trade_review_report_path=review_report_path,
        )
    return {
        "allow": True,
        "reasons": [],
        "row": row,
        "orders_csv": orders_csv,
        "name_binding_path": name_binding_path,
        "report_path": report_path,
        "lifecycle_report_path": lifecycle_report_path,
        "trade_review_report_path": review_report_path,
    }


def record_position_update(
    config: dict,
    code: str,
    price: float,
    qty: int,
    side: str,
    trade_time: str,
    name: str = "",
    notes: str = "",
    stop_loss_price: float | str = "",
) -> dict:
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    code = str(code).strip().zfill(6)
    side = str(side).upper()
    existing = read_order_rows(records_dir)
    reasons = _validate_position_update(existing, code, price, qty, side, trade_time, records_dir)
    report_path = _write_record_report(reports_dir, code, side, trade_time, not reasons, reasons)
    if reasons:
        return {"allow": False, "reasons": reasons, "report_path": report_path}

    resolved_name = _resolve_bound_name(records_dir, existing, code, name)
    row = _build_position_update_row(
        code=code,
        name=resolved_name,
        price=price,
        qty=qty,
        side=side,
        trade_time=trade_time,
        notes=notes,
        stop_loss_price=stop_loss_price,
    )
    orders_csv = append_manual_order(row, records_dir)
    name_binding_path = ""
    if side == "BUY" and row.get("name"):
        name_binding_path = write_name_binding(
            records_dir,
            code,
            str(row.get("name", "")),
            source_order_id=str(row.get("order_id", "")),
            updated_at=datetime.now().isoformat(timespec="seconds"),
            notes="auto_bound_from_buy",
        )
    review_report_path = ""
    lifecycle_report_path = write_trade_lifecycle_report(config, row["trade_date"])
    if side == "SELL":
        review = write_trade_review(config, code, row["trade_date"])
        review_report_path = review["path"]
        lifecycle_report_path = write_trade_lifecycle_report(
            config,
            row["trade_date"],
            trade_review_report_path=review_report_path,
        )
    return {
        "allow": True,
        "reasons": [],
        "row": row,
        "orders_csv": orders_csv,
        "name_binding_path": name_binding_path,
        "report_path": report_path,
        "lifecycle_report_path": lifecycle_report_path,
        "trade_review_report_path": review_report_path,
    }


def bind_stock_name(config: dict, code: str, name: str, notes: str = "") -> dict:
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    code = str(code).strip().zfill(6)
    name = str(name or "").strip()
    reasons: list[str] = []
    if not code.isdigit() or len(code) != 6:
        reasons.append("invalid_code")
    if not name:
        reasons.append("name_required")
    report_path = _write_record_report(reports_dir, code, "NAME_BINDING", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), not reasons, reasons)
    if reasons:
        return {"allow": False, "reasons": reasons, "report_path": report_path}
    binding_path = write_name_binding(
        records_dir,
        code,
        name,
        source_order_id="manual_name_binding",
        updated_at=datetime.now().isoformat(timespec="seconds"),
        notes=notes,
    )
    lifecycle_report_path = write_trade_lifecycle_report(config, datetime.now().date().isoformat())
    return {
        "allow": True,
        "reasons": [],
        "name_binding_path": binding_path,
        "report_path": report_path,
        "lifecycle_report_path": lifecycle_report_path,
    }


def void_manual_order(config: dict, order_id: str, notes: str = "") -> dict:
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    order_id = str(order_id or "").strip()
    rows = read_order_rows(records_dir)
    reasons: list[str] = []
    target = None
    for row in rows:
        if str(row.get("order_id") or "").strip() == order_id:
            target = row
            break
    if not order_id:
        reasons.append("order_id_required")
    elif target is None:
        reasons.append("order_id_not_found")
    elif str(target.get("status") or "FILLED").upper() == "VOID":
        reasons.append("order_already_void")
    report_path = _write_record_report(
        reports_dir,
        str((target or {}).get("code") or "000000").zfill(6),
        "VOID",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        not reasons,
        reasons,
    )
    if reasons:
        return {"allow": False, "reasons": reasons, "report_path": report_path}

    assert target is not None
    target["status"] = "VOID"
    suffix = f"voided_at={datetime.now().isoformat(timespec='seconds')}"
    if notes:
        suffix += f"; void_reason={notes}"
    target["notes"] = f"{target.get('notes', '')}; {suffix}".strip("; ")
    orders_csv = _write_order_rows(rows, records_dir)
    lifecycle_report_path = write_trade_lifecycle_report(config, datetime.now().date().isoformat())
    return {
        "allow": True,
        "reasons": [],
        "orders_csv": orders_csv,
        "report_path": report_path,
        "lifecycle_report_path": lifecycle_report_path,
    }


def find_latest_ticket(reports_dir: str, code: str | None = None) -> dict:
    path = Path(reports_dir)
    pattern = f"manual_order_ticket_*_{str(code).zfill(6)}.md" if code else "manual_order_ticket_*.md"
    tickets = sorted(path.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    if not tickets:
        return {}
    return _load_ticket(tickets[0])


def find_order_ticket(reports_dir: str, records_dir: str, code: str, side: str, trade_time: str) -> dict:
    if side == "SELL":
        positions = [pos for pos in get_open_positions(records_dir) if pos["code"] == code]
        if positions and positions[0].get("source_ticket_path"):
            ticket_path = Path(positions[0]["source_ticket_path"])
            if ticket_path.is_file():
                return _load_ticket(ticket_path)
    trade_date = (trade_time or "")[:10]
    if trade_date:
        ticket_path = Path(reports_dir) / f"manual_order_ticket_{trade_date}_{code}.md"
        if ticket_path.is_file():
            return _load_ticket(ticket_path)
    return find_latest_ticket(reports_dir, code)


def _load_ticket(ticket_path: Path) -> dict:
    data = parse_manual_ticket(ticket_path)
    data["source_ticket_path"] = str(ticket_path)
    data["ticket_id"] = ticket_path.stem.replace("manual_order_ticket_", "")
    data["generated_at"] = _ticket_generated_at(data, ticket_path)
    return data


def _ticket_generated_at(ticket: dict, ticket_path: Path) -> datetime:
    parsed_time = _parse_time(ticket.get("generated_at", ""))
    if parsed_time:
        return parsed_time
    trade_date = ticket.get("date", "")
    inferred_time = _parse_time(f"{trade_date} 14:45:00") if trade_date else None
    return inferred_time or datetime.fromtimestamp(ticket_path.stat().st_mtime)


def parse_manual_ticket(path: Path) -> dict:
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower().replace(" ", "_").replace("-", "_")] = value.strip()
    return data


def append_manual_order(row: dict, records_dir: str) -> str:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    rows = [
        item
        for item in read_order_rows(records_dir)
        if not _is_legacy_demo_placeholder_for_ticket(item, row)
    ]
    rows.append(row)
    return _write_order_rows(rows, records_dir)


def _write_order_rows(rows: list[dict], records_dir: str) -> str:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / "manual_orders.csv"
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ORDER_FIELDS)
        writer.writeheader()
        for item in rows:
            normalized = _normalize_existing_order(item)
            writer.writerow({field: normalized.get(field, "") for field in ORDER_FIELDS})
    return str(file_path)


def _validate(config: dict, ticket: dict, existing: list[dict], code: str, price: float, qty: int, side: str, trade_time: str, records_dir: str) -> list[str]:
    reasons: list[str] = []
    if not ticket:
        reasons.append("ticket_not_found")
    elif str(ticket.get("code", "")).zfill(6) != code:
        reasons.append("ticket_code_mismatch")
    if side not in {"BUY", "SELL"}:
        reasons.append("invalid_side")
    if int(qty) <= 0:
        reasons.append("qty_not_positive")
    if int(qty) % 100 != 0:
        reasons.append("qty_not_board_lot")
    if float(price) <= 0:
        reasons.append("price_not_positive")
    amount = float(price) * int(qty)
    if side == "BUY":
        max_price = _as_float(ticket.get("max_acceptable_price"))
        if max_price and float(price) > max_price:
            reasons.append("price_above_max_acceptable")
        max_order = float(config.get("risk", {}).get("max_order_value", 5000))
        if amount > max_order:
            reasons.append("order_amount_exceeds_limit")
        ticket_time = ticket.get("generated_at")
        parsed_trade_time = _parse_time(trade_time)
        if ticket_time and parsed_trade_time and parsed_trade_time < ticket_time:
            reasons.append("trade_time_before_ticket")
        if ticket and any(row.get("ticket_id") == ticket.get("ticket_id") and str(row.get("side") or "BUY").upper() == "BUY" for row in existing):
            reasons.append("duplicate_buy_for_ticket")
    if side == "SELL":
        positions = [pos for pos in get_open_positions(records_dir) if pos["code"] == code]
        if not positions:
            reasons.append("sell_without_open_buy")
        else:
            position = positions[0]
            if int(qty) > int(position["open_qty"]):
                reasons.append("sell_qty_exceeds_open_position")
            parsed_trade_time = _parse_time(trade_time)
            parsed_buy_time = _parse_time(position.get("last_buy_time", ""))
            if parsed_trade_time and parsed_buy_time and parsed_trade_time <= parsed_buy_time:
                reasons.append("sell_time_not_after_buy_time")
    return reasons


def _validate_position_update(
    existing: list[dict],
    code: str,
    price: float,
    qty: int,
    side: str,
    trade_time: str,
    records_dir: str,
) -> list[str]:
    reasons: list[str] = []
    if not code.isdigit() or len(code) != 6:
        reasons.append("invalid_code")
    if side not in {"BUY", "SELL"}:
        reasons.append("invalid_side")
    if int(qty) <= 0:
        reasons.append("qty_not_positive")
    if int(qty) % 100 != 0:
        reasons.append("qty_not_board_lot")
    if float(price) <= 0:
        reasons.append("price_not_positive")
    parsed_trade_time = _parse_time(trade_time)
    if not parsed_trade_time:
        reasons.append("invalid_trade_time")
    if side == "SELL":
        positions = [pos for pos in get_open_positions(records_dir) if pos["code"] == code]
        if not positions:
            reasons.append("sell_without_open_buy")
        else:
            position = positions[0]
            if int(qty) > int(position["open_qty"]):
                reasons.append("sell_qty_exceeds_open_position")
            parsed_buy_time = _parse_time(position.get("last_buy_time", ""))
            if parsed_trade_time and parsed_buy_time and parsed_trade_time <= parsed_buy_time:
                reasons.append("sell_time_not_after_buy_time")
    return reasons


def _build_order_row(ticket: dict, code: str, price: float, qty: int, side: str, trade_time: str, notes: str) -> dict:
    amount = round(float(price) * int(qty), 2)
    ticket_id = ticket.get("ticket_id", "")
    compact_time = "".join(ch for ch in trade_time if ch.isdigit())
    stop_loss = _as_float(ticket.get("stop_loss"))
    record_notes = notes
    if side == "SELL":
        below_stop = bool(stop_loss and float(price) < stop_loss)
        suffix = f"below_stop_loss={str(below_stop).lower()}"
        record_notes = f"{notes}; {suffix}" if notes else suffix
    return {
        "order_id": f"{ticket_id}_{side}_{compact_time}",
        "ticket_id": ticket_id,
        "strategy_name": ticket.get("strategy", ""),
        "trade_date": (trade_time or "")[:10] or ticket.get("date", ""),
        "trade_time": trade_time,
        "code": code,
        "name": ticket.get("name", ""),
        "side": side,
        "price": round(float(price), 4),
        "qty": int(qty),
        "amount": amount,
        "max_acceptable_price": ticket.get("max_acceptable_price", ""),
        "stop_loss_price": ticket.get("stop_loss", ""),
        "source_ticket_path": ticket.get("source_ticket_path", ""),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "status": "FILLED",
        "notes": record_notes,
    }


def _build_position_update_row(
    code: str,
    name: str,
    price: float,
    qty: int,
    side: str,
    trade_time: str,
    notes: str,
    stop_loss_price: float | str,
) -> dict:
    amount = round(float(price) * int(qty), 2)
    compact_time = "".join(ch for ch in trade_time if ch.isdigit())
    stop_loss = _as_float(stop_loss_price)
    record_notes = notes
    if side == "SELL":
        below_stop = bool(stop_loss and float(price) < stop_loss)
        suffix = f"below_stop_loss={str(below_stop).lower()}"
        record_notes = f"{notes}; {suffix}" if notes else suffix
    return {
        "order_id": f"position_update_{code}_{side}_{compact_time}",
        "ticket_id": "",
        "strategy_name": "manual_position_update",
        "trade_date": (trade_time or "")[:10],
        "trade_time": trade_time,
        "code": code,
        "name": name or code,
        "side": side,
        "price": round(float(price), 4),
        "qty": int(qty),
        "amount": amount,
        "max_acceptable_price": "",
        "stop_loss_price": stop_loss if stop_loss else "",
        "source_ticket_path": "",
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "status": "FILLED",
        "notes": record_notes,
    }


def _normalize_existing_order(row: dict) -> dict:
    if "order_id" in row and row.get("order_id"):
        return row
    side = str(row.get("side") or "BUY").upper()
    price = row.get("price") or row.get("buy_price") or ""
    qty = row.get("qty") or row.get("quantity") or ""
    return {
        "order_id": row.get("order_id", ""),
        "ticket_id": row.get("ticket_id", ""),
        "strategy_name": row.get("strategy_name") or row.get("strategy", ""),
        "trade_date": row.get("trade_date", ""),
        "trade_time": row.get("trade_time", ""),
        "code": str(row.get("code", "")).zfill(6),
        "name": row.get("name", ""),
        "side": side,
        "price": price,
        "qty": qty,
        "amount": row.get("amount") or (round(_as_float(price) * _as_float(qty), 2) if price and qty else ""),
        "max_acceptable_price": row.get("max_acceptable_price", ""),
        "stop_loss_price": row.get("stop_loss_price") or row.get("stop_loss", ""),
        "source_ticket_path": row.get("source_ticket_path", ""),
        "recorded_at": row.get("recorded_at", ""),
        "status": row.get("status", "FILLED"),
        "notes": row.get("notes", ""),
    }


def _resolve_bound_name(records_dir: str, existing: list[dict], code: str, incoming_name: str) -> str:
    bindings = read_name_bindings(records_dir)
    if bindings.get(code):
        return bindings[code]
    for row in existing:
        if str(row.get("status") or "FILLED").upper() == "VOID":
            continue
        if str(row.get("side") or "BUY").upper() != "BUY":
            continue
        if str(row.get("code", "")).zfill(6) == code and str(row.get("name") or "").strip():
            return str(row.get("name") or "").strip()
    return str(incoming_name or "").strip() or code


def _is_legacy_demo_placeholder_for_ticket(existing: dict, incoming: dict) -> bool:
    if not incoming.get("ticket_id") and not incoming.get("source_ticket_path"):
        return False
    return (
        not existing.get("order_id")
        and not existing.get("ticket_id")
        and not existing.get("source_ticket_path")
        and str(existing.get("side") or "BUY").upper() == "BUY"
        and str(existing.get("code", "")).zfill(6) == str(incoming.get("code", "")).zfill(6)
        and str(incoming.get("side", "")).upper() == "BUY"
    )


def _write_record_report(reports_dir: str, code: str, side: str, trade_time: str, allow: bool, reasons: list[str]) -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    date_value = (trade_time or datetime.now().isoformat())[:10]
    report = path / f"manual_order_record_{date_value}_{code}_{side}.md"
    lines = [
        "# Manual Order Record",
        "",
        f"status: {'RECORDED' if allow else 'REJECTED'}",
        f"code: {code}",
        f"side: {side}",
        f"trade_time: {trade_time}",
        f"reasons: {', '.join(reasons) if reasons else 'PASS'}",
        "",
        "Risk warning: manual execution only; not investment advice.",
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    return str(report)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
