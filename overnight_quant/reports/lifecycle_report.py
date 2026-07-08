from __future__ import annotations

from datetime import date
from pathlib import Path

from overnight_quant.execution.position_tracker import get_open_positions, get_position_summaries, read_order_rows


def write_trade_lifecycle_report(
    config: dict,
    trade_date: str | None = None,
    sell_plan_path: str = "",
    trade_review_report_path: str = "",
) -> str:
    trade_date = trade_date or date.today().isoformat()
    records_dir = config.get("paths", {}).get("records_dir", "overnight_quant/records")
    reports_dir = config.get("paths", {}).get("reports_dir", "overnight_quant/reports")
    rows = read_order_rows(records_dir)
    open_positions = get_open_positions(records_dir)
    summaries = get_position_summaries(records_dir)
    buy_rows = [row for row in rows if str(row.get("side") or "BUY").upper() == "BUY"]
    sell_rows = [row for row in rows if str(row.get("side") or "").upper() == "SELL"]
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    if sell_rows and not sell_plan_path:
        sell_plan_path = _latest_sell_plan_path(path)
    status = _status(buy_rows, sell_rows, open_positions, sell_plan_path)
    report = path / f"trade_lifecycle_{trade_date}.md"
    latest_buy = buy_rows[-1] if buy_rows else {}
    latest_summary = summaries[-1] if summaries else {}
    if not trade_review_report_path and latest_summary.get("code"):
        review_candidate = path / f"trade_review_{trade_date}_{latest_summary['code']}.md"
        if review_candidate.exists():
            trade_review_report_path = str(review_candidate)
    lines = [
        "# Trade Lifecycle",
        "",
        f"status: {status}",
        f"buy ticket path: {latest_buy.get('source_ticket_path', '')}",
        f"manual BUY: {'YES' if buy_rows else 'NO'}",
        f"BUY price: {latest_buy.get('price', latest_buy.get('buy_price', ''))}",
        f"BUY quantity: {latest_buy.get('qty', latest_buy.get('quantity', ''))}",
        f"BUY amount: {latest_buy.get('amount', '')}",
        f"open positions: {len(open_positions)}",
        _report_field("sell plan path", sell_plan_path),
        f"manual SELL: {'YES' if sell_rows else 'NO'}",
        _report_field("realized pnl", _realized_pnl(summaries)),
        _report_field("return pct", _return_pct(summaries)),
        _report_field("trade_review_report_path", trade_review_report_path),
        "",
        "Risk warning: manual execution only; not investment advice.",
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    return str(report)


def _latest_sell_plan_path(reports_dir: Path) -> str:
    plans = sorted(reports_dir.glob("sell_plan_*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    return str(plans[0]) if plans else ""


def _report_field(label: str, value) -> str:
    return f"{label}: {value}" if value != "" else f"{label}:"


def _status(buy_rows: list[dict], sell_rows: list[dict], open_positions: list[dict], sell_plan_path: str) -> str:
    if not buy_rows and not sell_rows:
        return "TICKET_ONLY"
    if sell_rows and not open_positions:
        return "CLOSED"
    if open_positions and sell_plan_path:
        return "SELL_PLAN_READY"
    if open_positions:
        return "BOUGHT_OPEN"
    return "ERROR"


def _realized_pnl(summaries: list[dict]) -> str:
    if not summaries or not any(row.get("sell_qty", 0) for row in summaries):
        return ""
    return str(round(sum(_as_float(row.get("realized_pnl")) for row in summaries), 2))


def _return_pct(summaries: list[dict]) -> str:
    if not summaries:
        return ""
    buy_amount = sum(_as_float(row.get("buy_amount")) for row in summaries)
    realized = sum(_as_float(row.get("realized_pnl")) for row in summaries)
    if not buy_amount or not any(row.get("sell_qty", 0) for row in summaries):
        return ""
    return str(round(realized / buy_amount * 100, 2))


def _as_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
