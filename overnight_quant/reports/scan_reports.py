from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path


RISK_WARNING = "Research assistant only; no automated orders; not investment advice."


def ticket_absence_reasons(result: dict, dry_run: bool = False) -> list[str]:
    if result.get("tickets"):
        return []

    reasons: list[str] = []
    market_gate = result.get("market_gate", {}) or {}
    candidate_source = str(result.get("candidate_source", "") or "")
    valid_observation = result.get("valid_for_trading_observation")

    if dry_run:
        reasons.append("dry_run_only: dry-run/rehearsal mode does not generate formal manual buy tickets.")
    if market_gate.get("reject_reasons"):
        reasons.append(f"market_gate_fail: {', '.join(market_gate.get('reject_reasons') or [])}")
    if int(result.get("candidate_count", 0) or 0) == 0:
        reasons.append("no_candidates: scan returned no candidates.")
    if candidate_source == "demo_fallback":
        reasons.append("candidate_source_demo_fallback: live data failed, so candidates came from demo fallback.")
    if valid_observation is False or str(valid_observation).upper() == "NO":
        reasons.append("not_valid_for_trading_observation: live data/session freshness is not valid for trading observation.")

    for stock in result.get("rejected") or []:
        stock_reasons = _stock_rejection_reasons(stock)
        if stock_reasons:
            code = stock.get("code", "UNKNOWN")
            reasons.append(f"{code}: {', '.join(stock_reasons)}")
        if len(reasons) >= 8:
            break

    if not reasons and not result.get("selected"):
        reasons.append("no_risk_approved_candidate: no candidate passed filters, score threshold, and risk gates.")

    return list(dict.fromkeys(reasons))


def write_scan_summary(result: dict, reports_dir: str, trade_date: str, dry_run: bool = False) -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    filename = f"dry_run_scan_{trade_date}.md" if dry_run else f"live_scan_summary_{trade_date}.md"
    report_path = path / filename
    market_gate = result.get("market_gate", {})
    selected = result.get("selected") or []
    dry_run_selected = result.get("dry_run_selected") or []
    advice = "NO_TRADE"
    if dry_run:
        advice = "DRY_RUN_ONLY"
    elif selected:
        advice = "BUY"
    absence_reasons = ticket_absence_reasons(result, dry_run=dry_run)
    lines = [
        "# Dry Run Scan Report" if dry_run else "# Live Scan Summary",
        "",
    ]
    if dry_run:
        lines.extend(
            [
                "DRY RUN ONLY: rehearsal output for process checks.",
                "No manual order ticket was generated and this is not a recommendation to place an order.",
                "",
            ]
        )
    lines.extend(
        [
            f"trade_date: {trade_date}",
            f"run_time: {datetime.now().isoformat(timespec='seconds')}",
            f"session_state: {market_gate.get('session_state', '')}",
            f"market_gate: {'PASS' if market_gate.get('pass') else 'FAIL'}",
            f"market_reasons: {', '.join(market_gate.get('reasons') or [])}",
            f"market_reject_reasons: {', '.join(market_gate.get('reject_reasons') or [])}",
            f"candidate_count: {result.get('candidate_count', 0)}",
            f"candidate_source: {result.get('candidate_source', '')}",
            f"live_candidate_count: {result.get('live_candidate_count', 0)}",
            f"demo_candidate_count: {result.get('demo_candidate_count', 0)}",
            f"valid_for_trading_observation: {'YES' if result.get('valid_for_trading_observation') else 'NO'}",
            f"rejected_count: {len(result.get('rejected') or [])}",
            f"reject_reason_top_list: {_format_reason_counts(result)}",
            f"final_advice: {advice}",
            f"ticket_generated: {'YES' if result.get('tickets') else 'NO'}",
            f"ticket_absence_reasons: {_format_inline_reasons(absence_reasons)}",
            f"quality_report_path: {result.get('quality_report_path', '')}",
            "",
            "## Selected",
            "",
        ]
    )
    rows = dry_run_selected if dry_run else selected
    if rows:
        for stock in rows:
            estimated = "YES" if stock.get("estimated_capital_flow") else "NO"
            lines.append(
                f"- {stock.get('code', '')} {stock.get('name', '')}: score={stock.get('total_score', '')}, "
                f"price={stock.get('price', '')}, estimated_capital_flow={estimated}, "
                f"capital_score_source={stock.get('capital_score_source', '')}, "
                f"chip_peak_type={stock.get('chip_peak_type', '')}, volume_signal={stock.get('volume_signal', '')}, "
                f"confidence_delta={stock.get('confidence_delta', '')}"
            )
            chip_copy = _chip_volume_reason_copy(stock)
            if chip_copy:
                lines.append(f"  - chip_volume_reasons: {chip_copy}")
            if stock.get("estimated_capital_flow"):
                lines.append("  - 资金项为估算，仅用于辅助评分。")
    else:
        lines.append("- None.")
    watch_only_rows = _watch_only_limit_up_rows(result)
    if watch_only_rows:
        lines.extend(["", "## Watch Only / Do Not Chase", ""])
        for stock in watch_only_rows:
            reasons = ", ".join(_stock_rejection_reasons(stock))
            lines.append(
                f"- {stock.get('code', '')} {stock.get('name', '')}: "
                f"score={stock.get('total_score', '')}, reason={reasons}, "
                f"chip_peak_type={stock.get('chip_peak_type', '')}, volume_signal={stock.get('volume_signal', '')}, "
                f"confidence_delta={stock.get('confidence_delta', '')}; watch only, do not chase."
            )
            chip_copy = _chip_volume_reason_copy(stock)
            if chip_copy:
                lines.append(f"  - chip_volume_reasons: {chip_copy}")
    if dry_run and result.get("rejected"):
        lines.extend(["", "## Rejected Chip/Volume Context", ""])
        for stock in (result.get("rejected") or [])[:10]:
            lines.append(
                f"- {stock.get('code', '')} {stock.get('name', '')}: "
                f"score={stock.get('total_score', '')}, chip_peak_type={stock.get('chip_peak_type', '')}, "
                f"volume_signal={stock.get('volume_signal', '')}, confidence_delta={stock.get('confidence_delta', '')}, "
                f"chip_volume_reasons={_chip_volume_reason_copy(stock) or 'None'}"
            )
    if absence_reasons:
        lines.extend(["", "## Buy Ticket Not Generated Reasons", ""])
        lines.extend(f"- {reason}" for reason in absence_reasons)
    lines.extend(["", f"Risk warning: {RISK_WARNING}", ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


def write_preflight_report(result: dict, reports_dir: str, trade_date: str) -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    report_path = path / f"preflight_{trade_date}.md"
    lines = [
        "# 项目通路检测",
        "",
        f"status: {result.get('status', '')}",
        f"trade_date: {trade_date}",
        f"run_time: {result.get('run_time', '')}",
        f"session_state: {result.get('session_state', '')}",
        f"is_trade_day: {'YES' if result.get('is_trade_day') else 'NO'}",
        f"config_ok: {'YES' if result.get('config_ok') else 'NO'}",
        f"records_writable: {'YES' if result.get('records_writable') else 'NO'}",
        f"reports_writable: {'YES' if result.get('reports_writable') else 'NO'}",
        f"backtest_outputs_writable: {'YES' if result.get('backtest_outputs_writable') else 'NO'}",
        f"cache_writable: {'YES' if result.get('cache_writable') else 'NO'}",
        f"network_check_enabled: {'YES' if result.get('network_check_enabled') else 'NO'}",
        "",
        "## Workflow Checks",
        "",
    ]
    for item in result.get("workflow_checks", []):
        lines.append(f"- {item.get('name', '')}: {'OK' if item.get('ok') else 'FAIL'}, returncode={item.get('returncode', '')}, error={item.get('error', '')}")
    parser_check = result.get("dashboard_parser") or {}
    lines.extend(["", "## Dashboard Parser", "", f"- status: {'OK' if parser_check.get('ok') else 'FAIL'}, parsed_reports={parser_check.get('parsed_reports', 0)}, error={parser_check.get('error', '')}", "", "## Optional Data Sources", ""])
    for item in result.get("sources", []):
        status = "OK" if item.get("ok") else "FAIL"
        lines.append(f"- {item.get('source', '')}: {status}, rows={item.get('rows', 0)}, error={item.get('error', '')}")
    lines.extend(["", f"Risk warning: {RISK_WARNING}", ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


def _format_reason_counts(result: dict) -> str:
    counter: Counter[str] = Counter()
    for reason in result.get("market_gate", {}).get("reject_reasons", []):
        counter[reason] += 1
    for stock in result.get("rejected") or []:
        for reason in stock.get("filter_reject_reasons") or stock.get("risk_gate", {}).get("reasons", []) or stock.get("risk_flags", []):
            counter[reason] += 1
    if not counter:
        return "None"
    return ", ".join(f"{reason}:{count}" for reason, count in counter.most_common(8))


def _watch_only_limit_up_rows(result: dict, limit: int = 8) -> list[dict]:
    rows = []
    for stock in result.get("rejected") or []:
        reasons = _stock_rejection_reasons(stock)
        if "limit_up_unavailable" in reasons or stock.get("is_limit_up"):
            rows.append(stock)
        if len(rows) >= limit:
            break
    return rows


def _stock_rejection_reasons(stock: dict) -> list[str]:
    candidates = (
        stock.get("risk_gate", {}).get("reasons"),
        stock.get("filter_reject_reasons"),
        stock.get("risk_flags"),
        stock.get("_freshness_reasons"),
    )
    for reasons in candidates:
        if reasons:
            return [str(reason) for reason in reasons]
    return []


def _format_inline_reasons(reasons: list[str]) -> str:
    return "; ".join(reasons) if reasons else "None"


def _chip_volume_reason_copy(stock: dict) -> str:
    reasons = stock.get("chip_volume_reasons") or ""
    if isinstance(reasons, list):
        tokens = [str(item) for item in reasons]
    else:
        tokens = [token.strip() for token in str(reasons).replace(";", "|").split("|") if token.strip()]
    return "; ".join(tokens[:8])
