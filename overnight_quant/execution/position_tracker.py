from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.data.position_accounting import summarize_order_rows


def read_order_rows(records_dir: str) -> list[dict]:
    path = Path(records_dir) / "manual_orders.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def get_open_positions(records_dir: str) -> list[dict]:
    return [pos for pos in get_position_summaries(records_dir) if int(pos.get("open_qty", 0)) > 0]


def get_position_summaries(records_dir: str, current_prices: dict[str, float] | None = None) -> list[dict]:
    return summarize_order_rows(read_order_rows(records_dir), current_prices)


def has_open_position(records_dir: str, code: str) -> bool:
    return any(pos["code"] == str(code).zfill(6) for pos in get_open_positions(records_dir))
