from __future__ import annotations

import csv
from pathlib import Path


INTRADAY_SIGNAL_FIELDS = [
    "trade_date",
    "run_time",
    "code",
    "name",
    "source_category",
    "source_bucket",
    "source_buckets",
    "after_close_score",
    "signal",
    "action_bias",
    "signal_score",
    "price",
    "vwap",
    "distance_to_vwap_pct",
    "change_pct",
    "open_change_pct",
    "range_position",
    "limit_up_gap_pct",
    "volume_confirmation",
    "intraday_source",
    "buy_zone",
    "reasons",
    "invalid_conditions",
    "risk_flags",
    "defence_conditions",
    "observation_conditions",
]

RISK_WARNING = "Observation only; no automated orders; not investment advice."


def write_intraday_signals_csv(result: dict, records_dir: str) -> str:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    output = path / f"intraday_buy_signals_{result['trade_date']}.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INTRADAY_SIGNAL_FIELDS)
        writer.writeheader()
        for row in result.get("rows") or []:
            writer.writerow(_csv_row(row, result))
    return str(output)


def write_intraday_report(result: dict, reports_dir: str) -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    output = path / f"intraday_observation_{result['trade_date']}.md"
    market_gate = result.get("market_gate") or {}
    lines = [
        "# Intraday Observation Report",
        "",
        f"trade_date: {result.get('trade_date', '')}",
        f"run_time: {result.get('run_time', '')}",
        f"mode: {result.get('mode', '')}",
        f"status: {result.get('status', '')}",
        f"session_state: {result.get('session_state', '')}",
        f"intraday_window: {result.get('intraday_window', '')}",
        f"candidate_source: {result.get('candidate_source', '')}",
        f"valid_for_trading_observation: {result.get('valid_for_trading_observation', '')}",
        f"market_gate: {'PASS' if market_gate.get('pass') else 'FAIL'}",
        f"market_reasons: {', '.join(market_gate.get('reasons') or [])}",
        f"market_reject_reasons: {', '.join(market_gate.get('reject_reasons') or [])}",
        f"signal_count: {result.get('signal_count', 0)}",
        f"buy_point_a_count: {result.get('buy_point_a_count', 0)}",
        f"buy_point_b_count: {result.get('buy_point_b_count', 0)}",
        f"buy_watch_count: {result.get('buy_watch_count', 0)}",
        "",
        "## Signals",
        "",
    ]
    signal_rows = [row for row in result.get("rows") or [] if row.get("signal") != "NO_BUY"]
    if signal_rows:
        lines.extend(
            [
                "| code | name | source | action_bias | signal | score | price | vwap | vwap_gap_pct | observation | defence | reasons | invalid_conditions |",
                "|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|",
            ]
        )
        for row in signal_rows:
            lines.append(
                f"| {row.get('code', '')} | {_escape(row.get('name', ''))} | {_escape(row.get('source_buckets', ''))} | "
                f"{row.get('action_bias', '')} | {row.get('signal', '')} | "
                f"{row.get('signal_score', '')} | {row.get('price', '')} | {row.get('vwap', '')} | "
                f"{row.get('distance_to_vwap_pct', '')} | {_escape(_join(row.get('observation_conditions')))} | "
                f"{_escape(_join(row.get('defence_conditions')))} | "
                f"{_escape(_join(row.get('reasons')))} | {_escape(_join(row.get('invalid_conditions')))} |"
            )
    else:
        lines.append("- No intraday buy-point reminders.")
    no_buy_rows = [row for row in result.get("rows") or [] if row.get("signal") == "NO_BUY"]
    if no_buy_rows:
        lines.extend(["", "## No-Buy Audit", ""])
        lines.extend(
            [
                "| code | name | score | price | vwap | invalid_conditions | risk_flags |",
                "|---|---|---:|---:|---:|---|---|",
            ]
        )
        for row in no_buy_rows[:20]:
            lines.append(
                f"| {row.get('code', '')} | {_escape(row.get('name', ''))} | {row.get('signal_score', '')} | "
                f"{row.get('price', '')} | {row.get('vwap', '')} | "
                f"{_escape(_join(row.get('invalid_conditions')))} | {_escape(_join(row.get('risk_flags')))} |"
            )
    lines.extend(["", "## Data Quality", ""])
    quality = result.get("quality") or {}
    status_rows = quality.get("source_status") or []
    if status_rows:
        for item in status_rows:
            lines.append(f"- {item.get('source', '')}: {'OK' if item.get('ok') else 'FAIL'}, rows={item.get('rows', 0)}, error={item.get('error', '')}")
    else:
        lines.append("- source_status: none recorded")
    warnings = quality.get("warnings") or []
    lines.append(f"- warnings: {', '.join(warnings) if warnings else 'none'}")
    lines.extend(["", f"Risk warning: {RISK_WARNING}", ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return str(output)


def _csv_row(row: dict, result: dict) -> dict:
    output = {field: row.get(field, "") for field in INTRADAY_SIGNAL_FIELDS}
    output["trade_date"] = result.get("trade_date", "")
    output["run_time"] = result.get("run_time", "")
    output["reasons"] = _join(row.get("reasons"))
    output["invalid_conditions"] = _join(row.get("invalid_conditions"))
    output["risk_flags"] = _join(row.get("risk_flags"))
    output["defence_conditions"] = _join(row.get("defence_conditions"))
    output["observation_conditions"] = _join(row.get("observation_conditions"))
    return output


def _join(value) -> str:
    if isinstance(value, list):
        return "|".join(str(item) for item in value if item)
    return str(value or "")


def _escape(value) -> str:
    return str(value).replace("|", "/").replace("\n", " ")
