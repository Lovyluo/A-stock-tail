from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_close_confirmation_report(
    result: dict[str, Any],
    reports_dir: str | Path,
    trade_date: str,
) -> str:
    target = Path(reports_dir) / f"close_confirmation_shadow_{trade_date}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 行业共振尾盘确认策略影子报告",
        "",
        f"status: {result.get('status', 'UNKNOWN')}",
        f"execution_ok: {str(bool(result.get('execution_ok'))).lower()}",
        f"data_ready: {str(bool(result.get('data_ready'))).lower()}",
        f"coverage_by_type: {json.dumps(result.get('coverage_by_type') or {}, ensure_ascii=False, sort_keys=True)}",
        f"readiness_errors: {json.dumps(result.get('readiness_errors') or [], ensure_ascii=False)}",
        f"critical_source_status: {json.dumps(result.get('critical_source_status') or {}, ensure_ascii=False, sort_keys=True)}",
        f"strategy_name: {result.get('strategy_name', 'close_confirmation_v1')}",
        f"strategy_phase: {result.get('strategy_phase', 'research_shadow')}",
        f"decision_time: {result.get('decision_time', '')}",
        f"feature_event_cutoff: {result.get('feature_event_cutoff', '')}",
        f"collection_deadline: {result.get('collection_deadline', '')}",
        f"execution_not_before: {result.get('execution_not_before', '')}",
        f"fund_flow_proxy_only: {str(bool(result.get('fund_flow_proxy_only'))).lower()}",
        f"fund_flow_gate_notice: {result.get('fund_flow_gate_notice', '')}",
        f"demo_field_count: {result.get('demo_field_count', 0)}",
        f"formal_signal_enabled: {str(bool(result.get('formal_signal_enabled'))).lower()}",
        f"ticket_enabled: {str(bool(result.get('ticket_enabled'))).lower()}",
        f"shadow_candidate_count: {len(result.get('shadow_candidates') or [])}",
        "",
        "> 策略研发/影子模拟中。结果仅用于研究观察，不生成正式信号或人工票据。",
        "",
        "## 影子候选",
        "",
        "| 代码 | 名称 | 评分 | 决策 | 硬门禁 |",
        "|---|---|---:|---|---|",
    ]
    for row in result.get("scored") or []:
        gates = row.get("hard_gates") or {}
        gate_text = "通过" if gates.get("all_pass") else "；".join(gates.get("reject_reasons") or [])
        lines.append(
            f"| {row.get('code', '')} | {row.get('name', '')} | "
            f"{float(row.get('total_score') or 0):.2f} | {row.get('decision', '')} | {gate_text} |"
        )
    lines.extend(
        [
            "",
            "## 代理指标声明",
            "",
            "- 主力资金字段仅作资金行为代理。",
            "- 筹码成本为价格与成交量分布代理，不代表真实持仓成本。",
            "- 当前阶段不允许生成正式交易票据。",
            "",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return str(target)
