from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.backtest.backtest_engine import TRADE_FIELDS


def write_backtest_outputs(result: dict, metrics: dict, output_dir: str | Path) -> dict:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    files = {
        "trades": _write_csv(path / "trades.csv", result.get("trades", []), TRADE_FIELDS),
        "equity_curve": _write_csv(path / "equity_curve.csv", result.get("equity_curve", []), ["trade_date", "equity"]),
        "monthly_returns": _write_csv(path / "monthly_returns.csv", metrics["monthly_returns"], ["period", "return_pct"]),
        "yearly_returns": _write_csv(path / "yearly_returns.csv", metrics["yearly_returns"], ["period", "return_pct"]),
        "skipped_days": _write_csv(path / "skipped_days.csv", result.get("skipped_days", []), ["trade_date", "reason", "code"]),
        "rejections": _write_csv(
            path / "rejections.csv",
            _serialize_rejections(result.get("rejections", [])),
            ["trade_date", "code", "reasons"],
        ),
        "field_coverage": _write_csv(
            path / "field_coverage.csv",
            result.get("data_quality", {}).get("field_coverage", []),
            ["field", "present", "total", "coverage_pct", "status"],
        ),
    }
    files["data_quality"] = _write_data_quality(path / "data_quality.md", result.get("data_quality", {}))
    files["summary"] = _write_summary(path / "backtest_summary.md", result, metrics, files)
    return files


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> str:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def _write_data_quality(path: Path, quality: dict) -> str:
    manifest = quality.get("manifest", {})
    unavailable = quality.get("unavailable_fields", [])
    lines = [
        "# Backtest Data Quality",
        "",
        f"data_fidelity: {quality.get('data_fidelity', '')}",
        f"dataset: {manifest.get('dataset', '')}",
        f"selection_as_of: {manifest.get('selection_as_of', '')}",
        f"source_selection_as_of: {manifest.get('source_selection_as_of', '')}",
        f"benchmark: {manifest.get('benchmark', '')}",
        f"unavailable_fields: {', '.join(unavailable) if unavailable else 'None'}",
        f"historical_true_fields: {_field_list(manifest.get('historical_true_fields', []))}",
        f"simulated_or_fixture_fields: {_field_list(manifest.get('simulated_or_fixture_fields', []))}",
        f"proxy_fields: {_field_list(quality.get('proxy_fields', manifest.get('proxy_fields', [])))}",
        f"safety_unknown_candidate_count: {quality.get('safety_unknown_candidate_count', 0)}",
        f"market_proxy_used_count: {quality.get('market_proxy_used_count', 0)}",
        f"data_dir: {quality.get('data_dir', '')}",
        f"trade_date_start: {quality.get('trade_date_start', '')}",
        f"trade_date_end: {quality.get('trade_date_end', '')}",
        f"candidate_count: {quality.get('candidate_count', '')}",
        "",
        "## Source Files",
        "",
        *_source_lines(quality.get("source_files", [])),
        "",
        "## Field Coverage",
        "",
        *_coverage_lines(quality.get("field_coverage", [])),
        "",
        "## Disclosure",
        "",
    ]
    if quality.get("data_fidelity") == "daily_proxy":
        lines.extend(
            [
                "Report Fidelity: DAILY_PROXY",
                "Daily-bar proxies cannot reproduce the original strategy's point-in-time tail-session state.",
                "Missing historical fields remain unavailable; no current/live values are used to backfill them.",
                "",
            ]
        )
        lines.extend(_positive_profile_disclosure(manifest))
    else:
        lines.extend(
            [
                "This sample_exact dataset is deterministic fixture data. It validates the backtest engine and event order only; it cannot prove strategy profitability or real historical performance.",
                "Missing theme or capital fields are disclosed as unavailable and are never backfilled from current/live data.",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _field_list(value: list[str] | str) -> str:
    return ", ".join(value) if isinstance(value, list) else str(value)


def _source_lines(rows: list[dict]) -> list[str]:
    return [f"- {row.get('file', '')} ({row.get('format', '')})" for row in rows] or ["- Not reported"]


def _coverage_lines(rows: list[dict]) -> list[str]:
    return [
        f"- {row.get('field', '')}: {row.get('present', 0)}/{row.get('total', 0)} ({row.get('coverage_pct', 0)}%)"
        for row in rows
    ] or ["- Not reported"]


def _write_summary(path: Path, result: dict, metrics: dict, files: dict) -> str:
    fidelity = result.get("data_quality", {}).get("data_fidelity", "")
    manifest = result.get("data_quality", {}).get("manifest", {})
    if fidelity == "daily_proxy":
        scope_lines = [
            "# Backtest Summary - DAILY_PROXY",
            "",
            "Report Fidelity: DAILY_PROXY",
            "本报告不等同于原策略完整历史验证。",
            "题材、资金、尾盘字段缺失可能显著影响结果。",
            "结果仅用于研究参考。",
            "strict_historical 尚未实现。",
        ]
        scope_lines.extend(_positive_profile_disclosure(manifest))
    else:
        scope_lines = [
            "# Backtest Summary - SAMPLE_EXACT",
            "",
            "Result scope: engine and event-sequence validation only. This is not evidence that yang_yongxing_overnight_v1 is profitable.",
        ]
    lines = [
        *scope_lines,
        "",
        f"data_fidelity: {fidelity}",
        f"total_return_pct: {metrics['total_return_pct']}",
        f"annualized_return_pct: {metrics['annualized_return_pct']}",
        f"max_drawdown_pct: {metrics['max_drawdown_pct']}",
        f"win_rate: {metrics['win_rate']}",
        f"profit_loss_ratio: {metrics['profit_loss_ratio']}",
        f"average_trade_return_pct: {metrics['average_trade_return_pct']}",
        f"average_holding_days: {metrics['average_holding_days']}",
        f"trade_count: {metrics['trade_count']}",
        f"no_trade_days: {metrics['no_trade_days']}",
        f"max_consecutive_losses: {metrics['max_consecutive_losses']}",
        f"max_single_trade_loss_pct: {metrics['max_single_trade_loss_pct']}",
        f"benchmark_return_pct: {metrics['benchmark_return_pct']}",
        "",
        "## Output Files",
        "",
    ]
    for label, value in files.items():
        lines.append(f"- {label}: {value}")
    lines.extend(["", "Risk warning: research backtest only; no automated trading; not investment advice.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _positive_profile_disclosure(manifest: dict) -> list[str]:
    if manifest.get("sample_profile") != "positive":
        return []
    return [
        "positive profile uses deterministic sample_fixture theme/capital fields for pipeline validation",
        "not live-filled",
        "not strict historical",
        "not evidence of strategy profitability",
        "DAILY_PROXY only",
        "",
    ]


def _serialize_rejections(rows: list[dict]) -> list[dict]:
    return [
        {
            **row,
            "reasons": "|".join(row.get("reasons", []))
            if isinstance(row.get("reasons"), list)
            else row.get("reasons", ""),
        }
        for row in rows
    ]
