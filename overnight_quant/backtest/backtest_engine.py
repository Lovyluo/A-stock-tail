from __future__ import annotations

from overnight_quant.risk.risk_manager import RiskManager
from overnight_quant.strategy.filters import evaluate_market_gate, evaluate_tail_stability, initial_filter
from overnight_quant.strategy.scoring import rank_scored, score_stock


TRADE_FIELDS = [
    "selection_date",
    "buy_date",
    "sell_date",
    "code",
    "name",
    "score",
    "buy_price",
    "sell_price",
    "buy_price_proxy",
    "sell_price_proxy",
    "exit_reason",
    "holding_days",
    "gross_pnl",
    "buy_commission",
    "sell_commission",
    "stamp_tax",
    "slippage_cost",
    "net_pnl",
    "return_pct",
    "data_fidelity",
    "selection_as_of",
    "unavailable_fields",
    "proxy_fields",
    "market_proxy_used",
]


class BacktestEngine:
    def __init__(self, provider, config: dict, fidelity: str = "sample_exact", intraday_assumption: str | None = None):
        self.provider = provider
        self.config = config
        self.fidelity = fidelity
        self.assumption = intraday_assumption or config.get("backtest", {}).get("intraday_assumption", "conservative")
        self.risk_manager = RiskManager(config)

    def run(self) -> dict:
        initial_capital = float(self.config.get("backtest", {}).get("initial_capital", 100000))
        equity = initial_capital
        position: dict | None = None
        trades: list[dict] = []
        skipped_days: list[dict] = []
        rejections: list[dict] = []
        equity_curve: list[dict] = []

        for trade_date in self.provider.trading_dates():
            if position and trade_date > position["buy_date"]:
                exit_bar = self.provider.exit_bar(position["code"], trade_date)
                if exit_bar:
                    position["holding_days"] += 1
                    outcome = simulate_exit(position, exit_bar, self.config, self.assumption)
                    if outcome.get("carry"):
                        position["risk_events"].append(outcome["risk_event"])
                        skipped_days.append({"trade_date": trade_date, "reason": outcome["risk_event"], "code": position["code"]})
                    else:
                        outcome["sell_date"] = trade_date
                        trade = _close_trade(position, outcome, self.config, self.fidelity)
                        trades.append(trade)
                        equity = round(equity + trade["net_pnl"], 2)
                        position = None

            if position:
                equity_curve.append({"trade_date": trade_date, "equity": equity})
                continue

            market = self.provider.market_snapshot_asof(trade_date)
            market_gate = _market_gate(market)
            if not market_gate["pass"]:
                reason = "market_data_unavailable" if "market_data_unavailable" in market_gate.get("reject_reasons", []) else "market_gate_fail"
                skipped_days.append({"trade_date": trade_date, "reason": reason, "code": ""})
                equity_curve.append({"trade_date": trade_date, "equity": equity})
                continue

            eligible: list[dict] = []
            for candidate in self.provider.candidates_asof(trade_date):
                initial = initial_filter(candidate, self.config)
                tail = evaluate_tail_stability(candidate, self.config)
                scored = score_stock(candidate, self.provider.bars_until(candidate["code"], trade_date), market_gate["score"], self.config)
                scored["market_proxy_used"] = bool(market.get("_market_proxy_used", False))
                if not initial["pass"] or not tail["pass"]:
                    reject_reasons = (
                        initial["reject_reasons"]
                        + tail["reject_reasons"]
                        + list(candidate.get("_risk_unknown_reasons", []))
                    )
                    rejections.append(
                        {
                            "trade_date": trade_date,
                            "code": candidate["code"],
                            "reasons": list(dict.fromkeys(reject_reasons)),
                        }
                    )
                    continue
                eligible.append(scored)

            selected = None
            max_order = float(self.config.get("risk", {}).get("max_order_value", 5000))
            max_position = equity * float(self.config.get("risk", {}).get("max_position_ratio_per_stock", 0.2))
            planned_amount = min(max_order, max_position)
            for candidate in rank_scored(eligible):
                risk = self.risk_manager.evaluate_buy(candidate, market_gate, planned_amount, 0)
                if not risk["allow"]:
                    rejections.append({"trade_date": trade_date, "code": candidate["code"], "reasons": risk["reasons"]})
                    continue
                selected = candidate
                break

            if not selected:
                skipped_days.append({"trade_date": trade_date, "reason": "no_buy_candidate", "code": ""})
                equity_curve.append({"trade_date": trade_date, "equity": equity})
                continue

            bars = self.provider.bars_until(selected["code"], trade_date)
            buy_price = float(bars[-1]["close"])
            qty = int(planned_amount // buy_price // 100) * 100
            if qty <= 0:
                skipped_days.append({"trade_date": trade_date, "reason": "board_lot_not_affordable", "code": selected["code"]})
            else:
                position = {
                    "selection_date": trade_date,
                    "buy_date": trade_date,
                    "code": selected["code"],
                    "name": selected["name"],
                    "score": selected["total_score"],
                    "buy_price": buy_price,
                    "qty": qty,
                    "holding_days": 0,
                    "risk_events": [],
                    "selection_as_of": selected.get("selection_as_of", "14:50"),
                    "unavailable_fields": _unavailable_fields(selected),
                    "proxy_fields": selected.get("proxy_fields", []),
                    "market_proxy_used": selected.get("market_proxy_used", False),
                }
            equity_curve.append({"trade_date": trade_date, "equity": equity})

        quality = {
            "manifest": self.provider.quality_manifest(),
            "unavailable_fields": self.provider.unavailable_fields(),
            "data_fidelity": self.fidelity,
        }
        if hasattr(self.provider, "quality_summary"):
            quality.update(self.provider.quality_summary())
        return {
            "initial_capital": initial_capital,
            "final_equity": equity,
            "trades": trades,
            "skipped_days": skipped_days,
            "rejections": rejections,
            "equity_curve": equity_curve,
            "benchmark_bars": self.provider.benchmark_bars(),
            "data_quality": quality,
        }


def simulate_exit(position: dict, bar: dict, config: dict, intraday_assumption: str = "conservative") -> dict:
    buy_price = float(position["buy_price"])
    profit_price = round(buy_price * (1 + float(config.get("sell", {}).get("take_profit_pct_1", 3)) / 100), 4)
    stop_price = round(buy_price * (1 + float(config.get("sell", {}).get("stop_loss_pct", -3)) / 100), 4)
    if bar.get("is_limit_down"):
        return {"carry": True, "risk_event": "limit_down_exit_risk"}

    open_price = float(bar["open"])
    if open_price >= profit_price:
        return {"carry": False, "sell_price": open_price, "exit_reason": "gap_take_profit_open", "sell_price_proxy": "next_day_open"}
    if open_price <= stop_price:
        return {"carry": False, "sell_price": open_price, "exit_reason": "gap_stop_loss_open", "sell_price_proxy": "next_day_open"}

    hit_profit = float(bar["high"]) >= profit_price
    hit_stop = float(bar["low"]) <= stop_price
    if hit_profit and hit_stop:
        if intraday_assumption == "optimistic":
            return {"carry": False, "sell_price": profit_price, "exit_reason": "both_hit_optimistic_take_profit", "sell_price_proxy": "trigger_price"}
        if intraday_assumption == "close_based":
            return {"carry": False, "sell_price": float(bar["close"]), "exit_reason": "both_hit_close_based", "sell_price_proxy": "next_day_close"}
        return {"carry": False, "sell_price": stop_price, "exit_reason": "both_hit_conservative_stop_loss", "sell_price_proxy": "trigger_price"}
    if hit_stop:
        return {"carry": False, "sell_price": stop_price, "exit_reason": "stop_loss_intraday", "sell_price_proxy": "trigger_price"}
    if hit_profit:
        return {"carry": False, "sell_price": profit_price, "exit_reason": "take_profit_intraday", "sell_price_proxy": "trigger_price"}
    return {"carry": False, "sell_price": float(bar["close"]), "exit_reason": "forced_next_day_close_proxy", "sell_price_proxy": "next_day_close"}


def calculate_trade_costs(buy_price: float, sell_price: float, qty: int, config: dict) -> dict:
    cost = config.get("cost", {})
    commission_rate = float(cost.get("commission_rate", 0.0003))
    min_commission = float(cost.get("min_commission", 5))
    stamp_tax_rate = float(cost.get("stamp_tax_rate", 0.0005))
    slippage_rate = float(cost.get("slippage_pct", 0)) / 100
    buy_amount = round(buy_price * qty, 2)
    sell_amount = round(sell_price * qty, 2)
    gross_pnl = round(sell_amount - buy_amount, 2)
    buy_commission = round(max(buy_amount * commission_rate, min_commission), 2)
    sell_commission = round(max(sell_amount * commission_rate, min_commission), 2)
    stamp_tax = round(sell_amount * stamp_tax_rate, 2)
    slippage_cost = round((buy_amount + sell_amount) * slippage_rate, 2)
    net_pnl = round(gross_pnl - buy_commission - sell_commission - stamp_tax - slippage_cost, 2)
    return {
        "gross_pnl": gross_pnl,
        "buy_commission": buy_commission,
        "sell_commission": sell_commission,
        "stamp_tax": stamp_tax,
        "slippage_cost": slippage_cost,
        "net_pnl": net_pnl,
        "return_pct": round(net_pnl / buy_amount * 100, 2) if buy_amount else 0.0,
    }


def _close_trade(position: dict, outcome: dict, config: dict, fidelity: str) -> dict:
    costs = calculate_trade_costs(position["buy_price"], outcome["sell_price"], position["qty"], config)
    reasons = list(position["risk_events"]) + [outcome["exit_reason"]]
    return {
        "selection_date": position["selection_date"],
        "buy_date": position["buy_date"],
        "sell_date": outcome.get("sell_date", ""),
        "code": position["code"],
        "name": position["name"],
        "score": position["score"],
        "buy_price": position["buy_price"],
        "sell_price": outcome["sell_price"],
        "buy_price_proxy": "selection_day_close",
        "sell_price_proxy": outcome["sell_price_proxy"],
        "exit_reason": ";".join(reasons),
        "holding_days": position["holding_days"],
        **costs,
        "data_fidelity": fidelity,
        "selection_as_of": position["selection_as_of"],
        "unavailable_fields": "|".join(position["unavailable_fields"]),
        "proxy_fields": "|".join(position.get("proxy_fields", [])),
        "market_proxy_used": position.get("market_proxy_used", False),
    }


def _unavailable_fields(stock: dict) -> list[str]:
    if stock.get("_unavailable_reasons"):
        return list(stock["_unavailable_reasons"])
    return [field for field in ("theme_tags", "main_net", "big_order_net") if stock.get(field) in ("", None, [])]


def _market_gate(market: dict) -> dict:
    if not market:
        return {"pass": False, "score": 0, "reasons": [], "reject_reasons": ["market_data_missing"]}
    if market.get("_gate_override"):
        return dict(market["_gate_override"])
    return evaluate_market_gate(market)
