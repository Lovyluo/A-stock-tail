from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from math import floor, sqrt
from typing import Any, Iterable

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import parse_cn_datetime


@dataclass(frozen=True)
class ExecutionConfig:
    entry_start: str = "14:51"
    cancel_at: str = "14:56"
    participation_rate: float = 0.10
    slippage_bps: float = 5.0
    impact_bps_at_full_participation: float = 10.0
    lot_size: int = 100


@dataclass(frozen=True)
class Fill:
    code: str
    side: str
    event_time: str
    quantity: int
    price: float
    notional: float
    slippage_bps: float
    impact_bps: float
    partial: bool
    blocked_reason: str = ""


def price_limit_pct(code: str, trade_date: str | date, *, is_st: bool = False) -> float:
    day = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date))
    if is_st:
        return 5.0
    if code.startswith(("300", "301")) and day >= date(2020, 8, 24):
        return 20.0
    if code.startswith("688") and day >= date(2019, 7, 22):
        return 20.0
    if code.startswith(("8", "4")):
        return 30.0
    return 10.0


def simulate_entry_fill(
    signal: dict[str, Any],
    minute_bars: Iterable[dict[str, Any]],
    config: ExecutionConfig | None = None,
) -> dict[str, Any]:
    cfg = config or ExecutionConfig()
    target_quantity = _round_lot(int(signal.get("quantity") or 0), cfg.lot_size)
    if target_quantity <= 0:
        return {"fills": [], "filled_quantity": 0, "unfilled_quantity": 0, "status": "INVALID_QUANTITY"}
    day = date.fromisoformat(str(signal["trade_date"]))
    start = datetime.combine(day, _parse_clock(cfg.entry_start), tzinfo=CN_TZ)
    contract_start = parse_cn_datetime(
        signal.get("execution_not_before")
    )
    if contract_start is not None:
        start = max(start, contract_start)
    cancel = datetime.combine(day, _parse_clock(cfg.cancel_at), tzinfo=CN_TZ)
    fills: list[Fill] = []
    remaining = target_quantity
    for bar in sorted(minute_bars, key=lambda row: str(row.get("event_time") or row.get("datetime") or "")):
        event_time = parse_cn_datetime(bar.get("event_time") or bar.get("datetime"))
        if event_time is None or event_time < start or event_time >= cancel or remaining <= 0:
            continue
        blocked = _entry_block_reason(signal, bar, day)
        if blocked:
            continue
        available = _round_lot(
            floor(float(bar.get("volume") or bar.get("vol") or 0) * cfg.participation_rate),
            cfg.lot_size,
        )
        fill_quantity = min(remaining, available)
        if fill_quantity <= 0:
            continue
        reference = _execution_reference_price(bar)
        if reference <= 0:
            continue
        participation = fill_quantity / max(float(bar.get("volume") or bar.get("vol") or 0), 1.0)
        impact = cfg.impact_bps_at_full_participation * min(
            participation / max(cfg.participation_rate, 0.0001),
            1.0,
        )
        price = reference * (1.0 + (cfg.slippage_bps + impact) / 10000.0)
        fills.append(
            Fill(
                code=str(signal.get("code") or ""),
                side="BUY",
                event_time=event_time.isoformat(),
                quantity=fill_quantity,
                price=round(price, 4),
                notional=round(price * fill_quantity, 2),
                slippage_bps=cfg.slippage_bps,
                impact_bps=round(impact, 4),
                partial=fill_quantity < remaining,
            )
        )
        remaining -= fill_quantity
    return {
        "fills": [asdict(fill) for fill in fills],
        "filled_quantity": target_quantity - remaining,
        "unfilled_quantity": remaining,
        "status": "FILLED" if remaining == 0 else ("PARTIAL" if fills else "UNFILLED"),
        "execution_not_before": start.isoformat(),
        "cancel_at": cancel.isoformat(),
    }


def simulate_exit_fill(
    position: dict[str, Any],
    minute_bars: Iterable[dict[str, Any]],
    *,
    trade_date: str | date,
    earliest_time: str = "09:31",
    config: ExecutionConfig | None = None,
) -> dict[str, Any]:
    cfg = config or ExecutionConfig()
    day = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date))
    if day <= date.fromisoformat(str(position["entry_date"])):
        return {"fills": [], "status": "T_PLUS_ONE_BLOCKED", "blocked_days": 1}
    remaining = _round_lot(int(position.get("quantity") or 0), cfg.lot_size)
    fills: list[Fill] = []
    blocked_limit_down = False
    start = datetime.combine(day, _parse_clock(earliest_time), tzinfo=CN_TZ)
    for bar in sorted(minute_bars, key=lambda row: str(row.get("event_time") or row.get("datetime") or "")):
        event_time = parse_cn_datetime(bar.get("event_time") or bar.get("datetime"))
        if event_time is None or event_time < start or remaining <= 0:
            continue
        if bool(bar.get("suspended")):
            continue
        if _at_limit_down(position, bar, day):
            blocked_limit_down = True
            continue
        available = _round_lot(
            floor(float(bar.get("volume") or bar.get("vol") or 0) * cfg.participation_rate),
            cfg.lot_size,
        )
        quantity = min(remaining, available)
        reference = _execution_reference_price(bar)
        if quantity <= 0 or reference <= 0:
            continue
        participation = quantity / max(float(bar.get("volume") or bar.get("vol") or 0), 1.0)
        impact = cfg.impact_bps_at_full_participation * min(
            participation / max(cfg.participation_rate, 0.0001),
            1.0,
        )
        price = reference * (1.0 - (cfg.slippage_bps + impact) / 10000.0)
        fills.append(
            Fill(
                code=str(position.get("code") or ""),
                side="SELL",
                event_time=event_time.isoformat(),
                quantity=quantity,
                price=round(price, 4),
                notional=round(price * quantity, 2),
                slippage_bps=cfg.slippage_bps,
                impact_bps=round(impact, 4),
                partial=quantity < remaining,
            )
        )
        remaining -= quantity
    if remaining == 0:
        status = "FILLED"
    elif fills:
        status = "PARTIAL"
    elif blocked_limit_down:
        status = "LIMIT_DOWN_BLOCKED"
    else:
        status = "UNFILLED"
    return {
        "fills": [asdict(fill) for fill in fills],
        "filled_quantity": int(position.get("quantity") or 0) - remaining,
        "unfilled_quantity": remaining,
        "status": status,
        "blocked_days": 1 if blocked_limit_down else 0,
    }


def holding_decision(
    position: dict[str, Any],
    state: dict[str, Any],
    *,
    holding_day: int,
) -> dict[str, Any]:
    invalidations = []
    if not bool(state.get("market_valid", True)):
        invalidations.append("market_invalidated")
    if not bool(state.get("industry_valid", True)):
        invalidations.append("industry_invalidated")
    if not bool(state.get("catalyst_valid", True)):
        invalidations.append("catalyst_invalidated")
    if not bool(state.get("structure_valid", True)):
        invalidations.append("structure_invalidated")
    if holding_day == 1 and bool(state.get("risk_exit")):
        return {"action": "EXIT", "reason": "d1_risk_exit"}
    if invalidations:
        return {"action": "EXIT", "reason": "|".join(invalidations)}
    if holding_day >= 5:
        return {"action": "EXIT", "reason": "d5_time_exit"}
    return {"action": "HOLD", "reason": "d2_d5_conditions_valid"}


class EventBacktestEngine:
    def __init__(self, config: ExecutionConfig | None = None) -> None:
        self.config = config or ExecutionConfig()

    def execute_entry(self, signal: dict[str, Any], minute_bars: Iterable[dict[str, Any]]) -> dict[str, Any]:
        return simulate_entry_fill(signal, minute_bars, self.config)

    def execute_exit(
        self,
        position: dict[str, Any],
        minute_bars: Iterable[dict[str, Any]],
        *,
        trade_date: str | date,
    ) -> dict[str, Any]:
        return simulate_exit_fill(position, minute_bars, trade_date=trade_date, config=self.config)


def _entry_block_reason(signal: dict[str, Any], bar: dict[str, Any], day: date) -> str:
    if bool(bar.get("suspended")):
        return "SUSPENDED"
    price = _execution_reference_price(bar)
    previous_close = float(signal.get("prev_close") or bar.get("prev_close") or 0)
    if previous_close <= 0:
        return ""
    limit = previous_close * (
        1.0
        + price_limit_pct(
            str(signal.get("code") or ""),
            day,
            is_st=bool(signal.get("is_st")),
        )
        / 100.0
    )
    if price >= limit - max(0.01, limit * 0.0001) and float(bar.get("ask_volume") or 0) <= 0:
        return "LIMIT_UP_BLOCKED"
    return ""


def _at_limit_down(position: dict[str, Any], bar: dict[str, Any], day: date) -> bool:
    previous_close = float(position.get("prev_close") or bar.get("prev_close") or 0)
    price = _execution_reference_price(bar)
    if previous_close <= 0 or price <= 0:
        return False
    limit = previous_close * (
        1.0
        - price_limit_pct(
            str(position.get("code") or ""),
            day,
            is_st=bool(position.get("is_st")),
        )
        / 100.0
    )
    return price <= limit + max(0.01, limit * 0.0001) and float(bar.get("bid_volume") or 0) <= 0


def _execution_reference_price(bar: dict[str, Any]) -> float:
    amount = float(bar.get("amount") or 0)
    volume = float(bar.get("volume") or bar.get("vol") or 0)
    if amount > 0 and volume > 0:
        return amount / volume
    return float(bar.get("vwap") or bar.get("open") or bar.get("price") or 0)


def _parse_clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":", 1))
    return time(hour=hour, minute=minute)


def _round_lot(quantity: int, lot_size: int) -> int:
    return max(0, int(quantity) // lot_size * lot_size)
