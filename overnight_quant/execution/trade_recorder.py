from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.data.demo_data import demo_manual_order
from overnight_quant.execution.order_recorder import ORDER_FIELDS


def read_manual_orders(records_dir: str, mode: str = "demo") -> list[dict]:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / "manual_orders.csv"
    if not file_path.exists() and mode == "demo":
        write_manual_orders([demo_manual_order()], records_dir)
    if not file_path.exists():
        return []
    with file_path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_manual_orders(orders: list[dict], records_dir: str) -> str:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / "manual_orders.csv"
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ORDER_FIELDS)
        writer.writeheader()
        for order in orders:
            row = _normalize_order(order)
            writer.writerow({field: row.get(field, "") for field in ORDER_FIELDS})
    return str(file_path)


def save_sell_plan(rows: list[dict], reports_dir: str, trade_date: str, status: str = "SELL_PLAN_READY") -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    file_path = path / f"sell_plan_{trade_date}.md"
    lines = ["# Next-Day Sell Plan", "", f"status: {status}", f"trade_date: {trade_date}", ""]
    if not rows:
        lines.extend(["NO_OPEN_POSITION", "", "Risk warning: manual execution only; not investment advice.", ""])
        file_path.write_text("\n".join(lines), encoding="utf-8")
        return str(file_path)
    lines.extend(
        [
            "## 持仓卖出计划明细",
            "",
            "| code | name | qty | buy_price | current_price | pnl_pct | action | composite_action | realtime_alert | context_score | level | stop_loss | take_profit_1 | vwap | vwap_gap_pct | intraday_trend | minute_fund | market_context | theme_context | fund_context | today_main_fund | volume_context | realtime_trigger | plan | sell_trigger |",
            "|---|---|---:|---:|---:|---:|---|---|---|---:|---|---:|---:|---:|---:|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in [
                    row.get("code", ""),
                    row.get("name", ""),
                    row.get("qty", ""),
                    row.get("buy_price", ""),
                    row.get("current_price", ""),
                    row.get("pnl_pct", ""),
                    row.get("action_cn") or row.get("action", ""),
                    row.get("composite_action_cn", ""),
                    row.get("realtime_alert_cn", ""),
                    row.get("context_score_total", ""),
                    row.get("level", ""),
                    row.get("effective_stop_loss_price") or row.get("stop_loss_price", ""),
                    row.get("take_profit_price_1", ""),
                    row.get("vwap", ""),
                    row.get("vwap_gap_pct", ""),
                    row.get("intraday_trend_cn", ""),
                    row.get("minute_fund_cn", ""),
                    row.get("market_context_cn", ""),
                    row.get("theme_context_cn", ""),
                    row.get("fund_context_cn", ""),
                    row.get("today_main_fund_cn", ""),
                    row.get("volume_context_cn", ""),
                    row.get("realtime_trigger_cn", ""),
                    row.get("plan_cn", ""),
                    row.get("sell_trigger_cn", ""),
                ]
            )
            + " |"
        )
    lines.append("")
    for row in rows:
        lines.extend(
            [
                f"## {row['code']} {row['name']} 卖出预案",
                "",
                f"Code: {row['code']}",
                f"Name: {row['name']}",
                f"Qty: {row.get('qty', '')}",
                f"Buy Price: {row['buy_price']}",
                f"Current Price: {row['current_price']}",
                f"PnL Percent: {row['pnl_pct']}%",
                f"Action: {row['action']}",
                f"Action CN: {row.get('action_cn', '')}",
                f"Level: {row['level']}",
                f"Reason: {row['reason']}",
                f"Reason CN: {row.get('reason_cn', '')}",
                f"Open Price: {row.get('open_price', '')}",
                f"Open Change Percent: {row.get('open_change_pct', '')}%",
                f"Current Change Percent: {row.get('current_change_pct', '')}%",
                f"Day High: {row.get('day_high', '')}",
                f"Day Low: {row.get('day_low', '')}",
                f"Range Position Percent: {row.get('range_position_pct', '')}%",
                f"Pullback From High Percent: {row.get('pullback_from_high_pct', '')}%",
                f"Amount Wan: {row.get('amount_wan', '')}",
                f"Turnover Percent: {row.get('turnover_pct', '')}%",
                f"Volume Ratio: {row.get('vol_ratio', '')}",
                f"VWAP: {row.get('vwap', '')}",
                f"VWAP Source: {row.get('vwap_source', '')}",
                f"VWAP Gap Percent: {row.get('vwap_gap_pct', '')}%",
                f"Stop Loss: {row.get('effective_stop_loss_price') or row.get('stop_loss_price', '')}",
                f"Take Profit Price 1: {row.get('take_profit_price_1', '')}",
                f"Take Profit Price 2: {row.get('take_profit_price_2', '')}",
                f"Force Exit Before: {row.get('force_exit_before', '')}",
                f"Past Force Exit Time: {row.get('past_force_exit_time', '')}",
                f"Last Update Time: {row.get('last_update_time', '')}",
                f"Composite Action CN: {row.get('composite_action_cn', '')}",
                f"Realtime Alert CN: {row.get('realtime_alert_cn', '')}",
                f"Realtime Trigger CN: {row.get('realtime_trigger_cn', '')}",
                f"Context Score Total: {row.get('context_score_total', '')}",
                f"Intraday Trend Context: {row.get('intraday_trend_cn', '')}",
                f"Minute Fund Context: {row.get('minute_fund_cn', '')}",
                f"Market Context: {row.get('market_context_cn', '')}",
                f"Theme Context: {row.get('theme_context_cn', '')}",
                f"Multi-Day Fund Context: {row.get('fund_context_cn', '')}",
                f"Today Main Fund Context: {row.get('today_main_fund_cn', '')}",
                f"Volume Trend Context: {row.get('volume_context_cn', '')}",
                "",
                "### 执行步骤",
                "",
                f"- 实时提醒：{row.get('realtime_alert_cn', '')}",
                f"- 实时触发：{row.get('realtime_trigger_cn', '')}",
                f"- 操作建议：{row.get('plan_cn', '')}",
                f"- 综合执行建议：{row.get('composite_action_cn', '')}",
                f"- 综合环境评分：{row.get('context_score_total', '')}",
                f"- 大盘/题材/资金/量价：{row.get('context_summary_cn', '')}",
                f"- 可以继续拿的条件：{row.get('hold_condition_cn', '')}",
                f"- 必须卖出/减仓的触发：{row.get('sell_trigger_cn', '')}",
                "",
                "### 策略逻辑",
                "",
                f"{row.get('logic_cn', '')}",
                "",
                "### 数据提示",
                "",
                f"- 分时/VWAP 来源：{row.get('vwap_source', '')}",
                f"- 分时接口错误：{row.get('intraday_error', '') or '无'}",
                f"- 大盘来源：{row.get('market_source', '')}；错误：{row.get('market_error', '') or '无'}",
                f"- 题材来源：{row.get('theme_source', '')}；错误：{row.get('theme_error', '') or '无'}",
                f"- 多日资金来源：{row.get('fund_source', '')}；错误：{row.get('fund_error', '') or '无'}",
                f"- 分钟资金来源：{row.get('minute_fund_source', '')}；错误：{row.get('minute_fund_error', '') or '无'}",
                f"- 当日主力来源：{row.get('today_fund_source', '')}；错误：{row.get('today_fund_error', '') or '无'}",
                f"- 量价来源：{row.get('volume_source', '')}；错误：{row.get('volume_error', '') or '无'}",
                f"Risk Notice: {row.get('risk_notice', 'manual execution only; not investment advice.')}",
                "",
            ]
        )
    lines.extend(
        [
            "## 通用卖出规则说明",
            "",
            "- 盈利达到第一止盈线，优先考虑锁定利润；若强势站稳 VWAP，可留观察仓。",
            "- 浮亏达到止损线，或低开后反抽失败，优先控制回撤。",
            "- VWAP 是盘中承接强弱线：站上且低点抬高偏强，跌破且反抽不过偏弱。",
            "- 综合环境评分会参考大盘走势、题材/概念表现、多日主力资金、当日主力资金、成交量和均线趋势。",
            "- 到 10:30 仍未走强的弱势持仓，不把短线纪律拖成被动持仓；超过该时间后按实时 VWAP、分时低点和分钟资金执行纪律。",
            "- 本计划只生成观察和复盘依据，不会自动下单。",
            "",
        ]
    )
    lines.append("Risk warning: manual execution only; not investment advice.")
    file_path.write_text("\n".join(lines), encoding="utf-8")
    return str(file_path)


def _md_cell(value) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "/").replace("\n", " ").strip()


def _normalize_order(order: dict) -> dict:
    price = order.get("price") or order.get("buy_price") or ""
    qty = order.get("qty") or order.get("quantity") or ""
    return {
        "order_id": order.get("order_id", ""),
        "ticket_id": order.get("ticket_id", ""),
        "strategy_name": order.get("strategy_name") or order.get("strategy", ""),
        "trade_date": order.get("trade_date", ""),
        "trade_time": order.get("trade_time", ""),
        "code": order.get("code", ""),
        "name": order.get("name", ""),
        "side": order.get("side", "BUY"),
        "price": price,
        "qty": qty,
        "amount": order.get("amount", ""),
        "max_acceptable_price": order.get("max_acceptable_price", ""),
        "stop_loss_price": order.get("stop_loss_price") or order.get("stop_loss", ""),
        "source_ticket_path": order.get("source_ticket_path", ""),
        "recorded_at": order.get("recorded_at", ""),
        "status": order.get("status", "FILLED"),
        "notes": order.get("notes", ""),
    }
