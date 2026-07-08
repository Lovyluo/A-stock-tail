from __future__ import annotations

from datetime import date


def calculate_metrics(result: dict) -> dict:
    trades = result.get("trades", [])
    curve = result.get("equity_curve", [])
    initial = float(result.get("initial_capital", 0) or 0)
    final = float(result.get("final_equity", initial) or initial)
    values = [float(row["equity"]) for row in curve]
    returns = [float(row["return_pct"]) for row in trades]
    gains = [float(row["net_pnl"]) for row in trades if float(row["net_pnl"]) > 0]
    losses = [float(row["net_pnl"]) for row in trades if float(row["net_pnl"]) < 0]
    total_return = round((final / initial - 1) * 100, 2) if initial else 0.0
    metrics = {
        "total_return_pct": total_return,
        "annualized_return_pct": _annualized_return(curve, initial, final),
        "max_drawdown_pct": calculate_max_drawdown(values),
        "win_rate": round(len(gains) / len(trades) * 100, 2) if trades else 0.0,
        "profit_loss_ratio": _profit_loss_ratio(gains, losses),
        "average_trade_return_pct": round(sum(returns) / len(returns), 2) if returns else 0.0,
        "average_holding_days": round(sum(float(row["holding_days"]) for row in trades) / len(trades), 2) if trades else 0.0,
        "trade_count": len(trades),
        "no_trade_days": len(result.get("skipped_days", [])),
        "max_consecutive_losses": _max_consecutive_losses(trades),
        "max_single_trade_loss_pct": round(min(returns), 2) if returns else 0.0,
        "monthly_returns": _period_returns(curve, initial, 7),
        "yearly_returns": _period_returns(curve, initial, 4),
        "benchmark_return_pct": _benchmark_return(result.get("benchmark_bars", [])),
    }
    return metrics


def calculate_max_drawdown(equity_values: list[float]) -> float:
    peak = 0.0
    maximum = 0.0
    for equity in equity_values:
        peak = max(peak, float(equity))
        if peak:
            maximum = max(maximum, (peak - float(equity)) / peak * 100)
    return round(maximum, 2)


def _annualized_return(curve: list[dict], initial: float, final: float) -> float:
    if len(curve) < 2 or initial <= 0:
        return 0.0
    start = date.fromisoformat(curve[0]["trade_date"])
    end = date.fromisoformat(curve[-1]["trade_date"])
    elapsed_days = (end - start).days
    if elapsed_days <= 0:
        return 0.0
    return round(((final / initial) ** (365 / elapsed_days) - 1) * 100, 2)


def _profit_loss_ratio(gains: list[float], losses: list[float]) -> float:
    if not gains or not losses:
        return 0.0
    average_gain = sum(gains) / len(gains)
    average_loss = abs(sum(losses) / len(losses))
    return round(average_gain / average_loss, 2) if average_loss else 0.0


def _max_consecutive_losses(trades: list[dict]) -> int:
    maximum = 0
    current = 0
    for row in trades:
        if float(row["net_pnl"]) < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def _period_returns(curve: list[dict], initial: float, prefix_length: int) -> list[dict]:
    grouped: dict[str, float] = {}
    for row in curve:
        grouped[row["trade_date"][:prefix_length]] = float(row["equity"])
    rows = []
    previous = initial
    for period in sorted(grouped):
        ending = grouped[period]
        rows.append({"period": period, "return_pct": round((ending / previous - 1) * 100, 2) if previous else 0.0})
        previous = ending
    return rows


def _benchmark_return(rows: list[dict]) -> float | None:
    if len(rows) < 2:
        return None
    first = float(rows[0]["close"])
    last = float(rows[-1]["close"])
    return round((last / first - 1) * 100, 2) if first else None
