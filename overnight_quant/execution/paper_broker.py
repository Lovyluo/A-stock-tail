from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class PaperRiskLimits:
    initial_cash: float = 100000.0
    max_position_ratio: float = 0.10
    max_total_exposure_ratio: float = 0.30
    max_positions: int = 3
    per_position_risk_ratio: float = 0.005
    lot_size: int = 100


@dataclass
class PaperPosition:
    code: str
    quantity: int
    average_price: float
    entry_date: str
    stop_price: float
    blocked_exit_days: int = 0


@dataclass
class PaperBroker:
    limits: PaperRiskLimits = field(default_factory=PaperRiskLimits)
    cash: float | None = None
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    ledger: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cash is None:
            self.cash = float(self.limits.initial_cash)

    def entry_intent(
        self,
        code: str,
        price: float,
        stop_price: float,
        *,
        mark_prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        if code in self.positions:
            return {"status": "EXISTING_POSITION", "code": code, "quantity": 0}
        if len(self.positions) >= self.limits.max_positions:
            return {"status": "POSITION_COUNT_LIMIT", "code": code, "quantity": 0}
        equity = self.equity(mark_prices or {})
        exposure_room = max(
            0.0,
            equity * self.limits.max_total_exposure_ratio - self.exposure(mark_prices or {}),
        )
        stock_cap = equity * self.limits.max_position_ratio
        loss_per_share = max(price - stop_price, 0.01)
        risk_cap = equity * self.limits.per_position_risk_ratio / loss_per_share * price
        value = min(stock_cap, exposure_room, risk_cap, float(self.cash or 0.0))
        quantity = int(value / max(price, 0.01)) // self.limits.lot_size * self.limits.lot_size
        return {
            "status": "READY" if quantity > 0 else "RISK_BUDGET_TOO_SMALL",
            "code": code,
            "quantity": quantity,
            "limit_price": float(price),
            "stop_price": float(stop_price),
        }

    def apply_entry_fill(
        self,
        code: str,
        quantity: int,
        price: float,
        *,
        trade_date: str | date,
        stop_price: float,
    ) -> dict[str, Any]:
        quantity = quantity // self.limits.lot_size * self.limits.lot_size
        cost = quantity * price
        if quantity <= 0 or cost > float(self.cash or 0.0):
            return {"status": "REJECTED", "reason": "cash_or_lot_constraint"}
        day = trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date)
        self.cash = float(self.cash or 0.0) - cost
        self.positions[code] = PaperPosition(code, quantity, price, day, stop_price)
        entry = {"event": "ENTRY_FILL", "code": code, "quantity": quantity, "price": price, "trade_date": day}
        self.ledger.append(entry)
        return {"status": "FILLED", **entry}

    def exit_intent(self, code: str, *, trade_date: str | date, reason: str) -> dict[str, Any]:
        position = self.positions.get(code)
        if position is None:
            return {"status": "POSITION_NOT_FOUND", "code": code, "quantity": 0}
        day = trade_date if isinstance(trade_date, date) else date.fromisoformat(str(trade_date))
        if day <= date.fromisoformat(position.entry_date):
            return {"status": "T_PLUS_ONE_BLOCKED", "code": code, "quantity": 0, "reason": reason}
        return {"status": "READY", "code": code, "quantity": position.quantity, "reason": reason}

    def apply_exit_fill(
        self,
        code: str,
        quantity: int,
        price: float,
        *,
        trade_date: str | date,
        reason: str,
    ) -> dict[str, Any]:
        position = self.positions.get(code)
        if position is None:
            return {"status": "POSITION_NOT_FOUND"}
        quantity = min(quantity // self.limits.lot_size * self.limits.lot_size, position.quantity)
        if quantity <= 0:
            return {"status": "REJECTED", "reason": "lot_constraint"}
        self.cash = float(self.cash or 0.0) + quantity * price
        position.quantity -= quantity
        if position.quantity == 0:
            del self.positions[code]
        day = trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date)
        entry = {
            "event": "EXIT_FILL",
            "code": code,
            "quantity": quantity,
            "price": price,
            "trade_date": day,
            "reason": reason,
        }
        self.ledger.append(entry)
        return {"status": "FILLED", **entry}

    def record_blocked_exit(self, code: str, reason: str) -> None:
        position = self.positions.get(code)
        if position is not None:
            position.blocked_exit_days += 1
            self.ledger.append(
                {
                    "event": "EXIT_BLOCKED",
                    "code": code,
                    "reason": reason,
                    "blocked_days": position.blocked_exit_days,
                }
            )

    def exposure(self, mark_prices: dict[str, float]) -> float:
        return sum(
            position.quantity * float(mark_prices.get(code, position.average_price))
            for code, position in self.positions.items()
        )

    def equity(self, mark_prices: dict[str, float]) -> float:
        return float(self.cash or 0.0) + self.exposure(mark_prices)

    def snapshot(self, mark_prices: dict[str, float] | None = None) -> dict[str, Any]:
        marks = mark_prices or {}
        return {
            "cash": round(float(self.cash or 0.0), 2),
            "equity": round(self.equity(marks), 2),
            "exposure": round(self.exposure(marks), 2),
            "positions": {
                code: {
                    "quantity": position.quantity,
                    "average_price": position.average_price,
                    "entry_date": position.entry_date,
                    "stop_price": position.stop_price,
                    "blocked_exit_days": position.blocked_exit_days,
                }
                for code, position in self.positions.items()
            },
        }
