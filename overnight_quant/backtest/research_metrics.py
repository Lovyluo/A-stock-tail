from __future__ import annotations

from math import erf, exp, log, sqrt
from statistics import mean, median, pstdev
from typing import Any, Iterable


HISTORICAL_THRESHOLDS = {
    "minimum_years": 5.0,
    "minimum_oos_windows": 8,
    "minimum_filled_trades": 300,
    "oos_sharpe": 0.8,
    "profit_factor": 1.20,
    "deflated_sharpe_probability": 0.95,
    "probability_of_backtest_overfit": 0.20,
    "max_drawdown": 0.15,
    "stressed_net_return": 0.0,
}


def calculate_research_metrics(
    trades: Iterable[dict[str, Any]],
    equity_curve: Iterable[dict[str, Any] | float],
    *,
    oos_window_scores: Iterable[dict[str, float]] | None = None,
    strata: dict[str, Any] | None = None,
    ablation: dict[str, Any] | None = None,
    extra_cost_bps_per_side: float = 10.0,
) -> dict[str, Any]:
    trade_rows = list(trades)
    equity = [_equity_value(item) for item in equity_curve]
    pnl = [float(row.get("net_pnl") or row.get("pnl") or 0.0) for row in trade_rows]
    returns = _daily_returns(equity)
    gains = sum(value for value in pnl if value > 0)
    losses = abs(sum(value for value in pnl if value < 0))
    total_turnover = sum(abs(float(row.get("entry_value") or 0)) + abs(float(row.get("exit_value") or 0)) for row in trade_rows)
    stressed_cost = total_turnover * extra_cost_bps_per_side / 10000.0
    net_return = (equity[-1] / equity[0] - 1.0) if len(equity) >= 2 and equity[0] else 0.0
    annualized = _annualized_return(equity)
    max_drawdown = _max_drawdown(equity)
    sharpe = _ratio(returns, downside_only=False)
    sortino = _ratio(returns, downside_only=True)
    calmar = annualized / max_drawdown if max_drawdown > 0 else 0.0
    windows = list(oos_window_scores or [])
    dsr = _deflated_sharpe_probability(sharpe, len(returns), len(windows))
    pbo = _probability_of_backtest_overfit(windows)
    fill_rates = [
        float(row.get("filled_quantity") or 0) / max(float(row.get("requested_quantity") or 0), 1.0)
        for row in trade_rows
        if float(row.get("requested_quantity") or 0) > 0
    ]
    slippage = [float(row.get("slippage_bps") or 0) for row in trade_rows]
    blocked_days = sum(int(row.get("blocked_days") or 0) for row in trade_rows)
    initial_equity = equity[0] if equity else 0.0
    stressed_net = ((equity[-1] - stressed_cost) / initial_equity - 1.0) if initial_equity else 0.0
    return {
        "net_return": net_return,
        "median_trade_return": median([float(row.get("return_pct") or 0) for row in trade_rows]) if trade_rows else 0.0,
        "profit_factor": gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0),
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_drawdown,
        "cvar_95": _cvar(returns, 0.05),
        "filled_trade_count": sum(1 for row in trade_rows if int(row.get("filled_quantity") or 0) > 0),
        "fill_rate": mean(fill_rates) if fill_rates else 0.0,
        "average_slippage_bps": mean(slippage) if slippage else 0.0,
        "blocked_days": blocked_days,
        "market_industry_strata": strata or {},
        "deflated_sharpe_probability": dsr,
        "probability_of_backtest_overfit": pbo,
        "factor_ablation": ablation or {},
        "stressed_net_return_10bps_each_side": stressed_net,
    }


def evaluate_historical_acceptance(
    metrics: dict[str, Any],
    *,
    history_years: float,
    oos_windows: int,
) -> dict[str, Any]:
    observed = {
        "minimum_years": history_years,
        "minimum_oos_windows": oos_windows,
        "minimum_filled_trades": int(metrics.get("filled_trade_count") or 0),
        "oos_sharpe": float(metrics.get("sharpe") or 0),
        "profit_factor": float(metrics.get("profit_factor") or 0),
        "deflated_sharpe_probability": float(metrics.get("deflated_sharpe_probability") or 0),
        "probability_of_backtest_overfit": float(metrics.get("probability_of_backtest_overfit") or 1),
        "max_drawdown": float(metrics.get("max_drawdown") or 1),
        "stressed_net_return": float(metrics.get("stressed_net_return_10bps_each_side") or 0),
    }
    reasons = []
    for key in ("minimum_years", "minimum_oos_windows", "minimum_filled_trades", "oos_sharpe", "profit_factor", "deflated_sharpe_probability"):
        if observed[key] < HISTORICAL_THRESHOLDS[key]:
            reasons.append(f"{key}_below_threshold")
    for key in ("probability_of_backtest_overfit", "max_drawdown"):
        if observed[key] > HISTORICAL_THRESHOLDS[key]:
            reasons.append(f"{key}_above_threshold")
    if observed["stressed_net_return"] <= HISTORICAL_THRESHOLDS["stressed_net_return"]:
        reasons.append("stressed_net_return_not_positive")
    return {"passed": not reasons, "reasons": reasons, "observed": observed, "thresholds": HISTORICAL_THRESHOLDS}


def evaluate_shadow_acceptance(*, trading_days: int, filled_trades: int) -> dict[str, Any]:
    required_days = 60 if filled_trades >= 50 else 90
    passed = trading_days >= required_days and filled_trades >= 50
    return {
        "passed": passed,
        "required_trading_days": required_days,
        "required_filled_trades": 50,
        "observed_trading_days": trading_days,
        "observed_filled_trades": filled_trades,
    }


def _equity_value(item: dict[str, Any] | float) -> float:
    if isinstance(item, dict):
        return float(item.get("equity") or item.get("value") or 0)
    return float(item)


def _daily_returns(equity: list[float]) -> list[float]:
    return [equity[index] / equity[index - 1] - 1.0 for index in range(1, len(equity)) if equity[index - 1]]


def _ratio(returns: list[float], *, downside_only: bool) -> float:
    if not returns:
        return 0.0
    sample = [min(value, 0.0) for value in returns] if downside_only else returns
    volatility = pstdev(sample) if len(sample) > 1 else 0.0
    return mean(returns) / volatility * sqrt(252.0) if volatility > 0 else 0.0


def _annualized_return(equity: list[float]) -> float:
    if len(equity) < 2 or equity[0] <= 0 or equity[-1] <= 0:
        return 0.0
    return (equity[-1] / equity[0]) ** (252.0 / (len(equity) - 1)) - 1.0


def _max_drawdown(equity: list[float]) -> float:
    peak = 0.0
    drawdown = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, (peak - value) / peak)
    return drawdown


def _cvar(values: list[float], tail_probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    count = max(1, int(len(ordered) * tail_probability))
    return mean(ordered[:count])


def _deflated_sharpe_probability(sharpe: float, observations: int, trials: int) -> float:
    if observations < 2:
        return 0.0
    expected_max = sqrt(max(0.0, 2.0 * log(max(trials, 1)))) / sqrt(observations)
    z_score = (sharpe - expected_max) * sqrt(max(observations - 1, 1)) / sqrt(252.0)
    return 0.5 * (1.0 + erf(z_score / sqrt(2.0)))


def _probability_of_backtest_overfit(windows: list[dict[str, float]]) -> float:
    if not windows:
        return 1.0
    overfit = 0
    usable = 0
    for row in windows:
        in_sample = float(row.get("in_sample_rank") or 0)
        out_sample = float(row.get("out_sample_rank") or 0)
        if in_sample <= 0 or out_sample <= 0:
            continue
        usable += 1
        if in_sample <= 0.5 and out_sample > 0.5:
            overfit += 1
    return overfit / usable if usable else 1.0
