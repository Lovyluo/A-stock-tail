from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


WATCHLIST_FIELDS = [
    "trade_date",
    "next_trade_date",
    "code",
    "name",
    "category",
    "score",
    "theme_tags",
    "theme_market_state",
    "theme_block_change_pct",
    "close_price",
    "change_pct",
    "turnover_pct",
    "amount_wan",
    "vol_ratio",
    "main_net",
    "main_net_source",
    "estimated_capital_flow",
    "chip_peak_type",
    "chip_avg_cost_20d",
    "chip_avg_cost_60d",
    "current_vs_chip_cost_pct",
    "overhead_pressure_ratio",
    "downside_support_ratio",
    "main_force_chip_proxy",
    "volume_signal",
    "confidence_delta",
    "chip_volume_reasons",
    "reason",
    "positive_reasons",
    "info_gap_reasons",
    "missing_reasons",
    "risk_reasons",
    "risk_flags",
    "tomorrow_watch_plan",
    "invalid_conditions",
    "data_quality_flags",
]

MORNING_REPLAY_FIELDS = [
    "analysis_mode",
    "observation_date",
    "replay_as_of_date",
    "freshness_basis",
    *WATCHLIST_FIELDS,
]

RISK_WARNING = (
    "本报告仅用于观察计划，不构成投资建议，也不会下单或自动执行任何交易。"
)

THEME_MARKET_STATE_LABELS = {
    "mainline_pullback_relative_strength": "主线回调中个股逆势走强",
    "mainline_pullback": "主线题材短线回调",
    "rotation_risk": "板块弱势且主线延续性不足",
}


def write_watchlist_csv(result: dict, records_dir: str) -> str:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    is_replay = result.get("analysis_mode") == "previous_close_replay"
    output = (
        path / f"morning_replay_watchlist_{result['observation_date']}.csv"
        if is_replay
        else path / f"next_morning_watchlist_{result['trade_date']}.csv"
    )
    rows = _watchlist_rows(result)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MORNING_REPLAY_FIELDS if is_replay else WATCHLIST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_row(row, result))
    return str(output)


def write_after_close_report(result: dict, reports_dir: str) -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    is_replay = result.get("analysis_mode") == "previous_close_replay"
    output = (
        path / f"morning_replay_analysis_{result['observation_date']}.md"
        if is_replay
        else path / f"after_close_analysis_{result['trade_date']}.md"
    )
    lines = [
        "# 早盘前收盘回放观察报告" if is_replay else "# 盘后观察池报告",
        "",
        f"date: {result['trade_date']}",
        f"analysis_mode: {result.get('analysis_mode', 'after_close')}",
        f"analysis_context: {result.get('analysis_context', '')}",
        f"mode: {result['mode']}",
        f"status: {result['status']}",
        f"session_state: {result['session_state']}",
        f"candidate_source: {result['candidate_source']}",
        f"valid_for_trading_observation: {result['valid_for_trading_observation']}",
        f"final_view: {result['final_view']}",
        f"next_trade_date_calendar: {result['next_trade_date_calendar']}",
        f"next_trade_date: {result['next_trade_date']}",
    ]
    if is_replay:
        lines.extend(
            [
                f"observation_date: {result['observation_date']}",
                f"replay_as_of_date: {result['replay_as_of_date']}",
                f"replay_calendar: {result['replay_calendar']}",
                f"freshness_basis: {result['freshness_basis']}",
                "",
                "本观察池基于前一交易日收盘数据在早盘前重建，"
                "不是盘中实时确认结果，不构成投资建议，也不会自动下单。",
            ]
        )
    elif result.get("after_close_carryover") == "YES":
        lines.extend(
            [
                f"after_close_carryover: {result['after_close_carryover']}",
                f"observation_date: {result.get('observation_date', '')}",
                f"freshness_basis: {result.get('freshness_basis', '')}",
                "",
                "当前为开盘前时段，本观察池按上一交易日盘后延续窗口生成。",
            ]
        )
    lines.extend(["", "## 1. 市场环境", "", f"- 市场评分: {result.get('market_score', 0)}"])
    market = result.get("market") or {}
    indices = market.get("indices") or {}
    if indices:
        for code, item in indices.items():
            lines.append(f"- {code} {item.get('name', '')}: {item.get('change_pct', '')}%")
    else:
        lines.append("- 市场快照不可用")
    lines.extend(["", "## 2. 主线题材", ""])
    recent_themes = result.get("recent_hot_themes") or []
    if recent_themes:
        lines.extend(["| 近几日题材 | 活跃天数 | 出现次数 | 最新日期 | 趋势 |", "|---|---:|---:|---|---|"])
        for item in recent_themes[:10]:
            lines.append(
                f"| {_escape(item.get('theme', ''))} | {item.get('active_days', 0)} | "
                f"{item.get('count', 0)} | {_escape(item.get('latest_date', ''))} | {_escape(item.get('trend', ''))} |"
            )
        lines.append("")
    themes = result.get("themes") or []
    if themes:
        lines.extend(["| 排名 | 题材 | 强度 | 代表个股 | 说明 |", "|---:|---|---:|---|---|"])
        for index, item in enumerate(themes, start=1):
            lines.append(
                f"| {index} | {_escape(item['theme'])} | {item['strength']} | "
                f"{_escape(', '.join(item['representative_stocks']))} | 候选池题材出现频次 |"
            )
    else:
        lines.append("- 暂无可确认的主线题材。")
    if not result.get("industry_rank_available", False):
        lines.append("- 行业涨幅排名暂不可用")

    _add_category_section(lines, result, "A", "## 3. A类重点观察")
    _add_category_section(lines, result, "B", "## 4. B类备选观察")
    _add_category_section(lines, result, "C", "## 5. C类风险观察 / 不建议追")
    _add_chip_volume_section(lines, result)
    lines.extend(
        [
            "",
            "## 7. 次日早盘总策略",
            "",
            "- 指数若高开偏强，仅继续观察仍满足条件的个股。",
            "- 指数若明显偏弱，应缩小观察范围，优先取消失守昨收的个股。",
            "- 题材若出现明显分歧，不要依赖孤立个股强势。",
            "- 对高开过多或快速拉升但缺少承接的个股，不追高。",
            "",
            "## 8. 数据质量",
            "",
            f"- fallback_status: {'YES' if result.get('quality', {}).get('fallback_to_demo') else 'NO'}",
        ]
    )
    status_rows = result.get("quality", {}).get("source_status") or []
    if status_rows:
        lines.append("- source_status:")
        for item in status_rows:
            lines.append(f"  - {item.get('source', '')}: {'OK' if item.get('ok') else 'FAIL'}")
    else:
        lines.append("- source_status: none recorded")
    flags = Counter(flag for row in result.get("evaluated_rows", []) for flag in row.get("data_quality_flags", []))
    lines.append(f"- data_quality_flags: {dict(flags) if flags else 'none'}")
    warnings = result.get("quality", {}).get("warnings") or []
    lines.append(f"- warnings: {', '.join(warnings) if warnings else 'none'}")
    if flags.get("estimated_capital_flow"):
        lines.append("- 标记为 estimated 的资金字段仅用于辅助观察评分，不代表资金流入已被确认。")
    lines.extend(["", "## 风险提示", "", RISK_WARNING, ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return str(output)


def _add_category_section(lines: list[str], result: dict, category: str, title: str) -> None:
    lines.extend(["", title, ""])
    rows = result.get("categories", {}).get(category, [])
    if not rows:
        if result["status"] in {
            "NOT_TRADING_DAY",
            "NOT_AFTER_CLOSE",
            "DATA_FALLBACK_DEMO",
            "DATA_QUALITY_BLOCKED",
            "NOT_REPLAY_WINDOW",
            "REPLAY_DATA_FALLBACK_DEMO",
            "REPLAY_DATA_QUALITY_BLOCKED",
        }:
            lines.append(f"- 当前状态为 {result['status']}，因此没有生成正式观察行。")
        else:
            lines.append("- 无。")
        return
    if category == "C":
        lines.extend(
            [
                "| 代码 | 名称 | 评分 | 题材状态 | 峰型 proxy | 20日成本 proxy | 偏离成本% | 量能信号 | 置信度变化 | 风险原因 | 失效条件 |",
                "|---|---|---:|---|---|---:|---:|---|---:|---|---|",
            ]
        )
        for row in rows:
            risk_copy = _join_reason_copy(row.get("missing_reasons", ""), row.get("risk_reasons", ""))
            lines.append(
                f"| {row['code']} | {_escape(row['name'])} | {row['score']} | "
                f"{_escape(_theme_state_copy(row.get('theme_market_state', '')))} | "
                f"{_escape(_chip_peak_label(row.get('chip_peak_type', 'neutral')))} | "
                f"{_fmt(row.get('chip_avg_cost_20d'))} | {_fmt(row.get('current_vs_chip_cost_pct'))} | "
                f"{_escape(_volume_signal_label(row.get('volume_signal', '')))} | {_fmt(row.get('confidence_delta'), digits=0)} | "
                f"{_escape(risk_copy)} | {_escape(row['invalid_conditions'])} |"
            )
        return
    lines.extend(
        [
            "| 代码 | 名称 | 评分 | 题材 | 题材状态 | 观察理由 | 次日观察条件 | 失效条件 |",
            "|---|---|---:|---|---|---|---|---|",
        ]
    )
    for row in rows:
        reason_copy = row.get("positive_reasons") or row.get("reason", "")
        lines.append(
            f"| {row['code']} | {_escape(row['name'])} | {row['score']} | "
            f"{_escape('|'.join(row.get('theme_tags') or []))} | {_escape(_theme_state_copy(row.get('theme_market_state', '')))} | {_escape(reason_copy)} | "
            f"{_escape(row['tomorrow_watch_plan'])} | {_escape(row['invalid_conditions'])} |"
        )


def _add_chip_volume_section(lines: list[str], result: dict) -> None:
    lines.extend(["", "## 6. 筹码与量价确认", ""])
    rows = [
        row
        for category in ("A", "B", "C")
        for row in result.get("categories", {}).get(category, [])
    ]
    if not rows:
        lines.append("- 暂无可展示的筹码/量价 proxy。")
        return
    lines.extend(
        [
            "| 代码 | 名称 | 分类 | 峰型 proxy | 20日成本 proxy | 偏离成本% | 量能信号 | 置信度变化 | 说明 |",
            "|---|---|---|---|---:|---:|---|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.get('code', '')} | {_escape(row.get('name', ''))} | {row.get('category', '')} | "
            f"{_escape(_chip_peak_label(row.get('chip_peak_type', 'neutral')))} | "
            f"{_fmt(row.get('chip_avg_cost_20d'))} | {_fmt(row.get('current_vs_chip_cost_pct'))} | "
            f"{_escape(_volume_signal_label(row.get('volume_signal', '')))} | {_fmt(row.get('confidence_delta'), digits=0)} | "
            f"{_escape(_chip_reason_copy(row))} |"
        )


def _watchlist_rows(result: dict) -> list[dict]:
    return [
        row
        for category in ("A", "B")
        for row in result.get("categories", {}).get(category, [])
    ]


def _csv_row(row: dict, result: dict) -> dict:
    fields = MORNING_REPLAY_FIELDS if result.get("analysis_mode") == "previous_close_replay" else WATCHLIST_FIELDS
    output = {field: row.get(field, "") for field in fields}
    if result.get("analysis_mode") == "previous_close_replay":
        output["analysis_mode"] = result["analysis_mode"]
        output["observation_date"] = result["observation_date"]
        output["replay_as_of_date"] = result["replay_as_of_date"]
        output["freshness_basis"] = result["freshness_basis"]
    output["trade_date"] = result["trade_date"]
    output["next_trade_date"] = result["next_trade_date"]
    output["theme_tags"] = "|".join(row.get("theme_tags") or [])
    output["risk_flags"] = "|".join(row.get("risk_flags") or [])
    output["data_quality_flags"] = "|".join(row.get("data_quality_flags") or [])
    output["estimated_capital_flow"] = "true" if row.get("estimated_capital_flow") else "false"
    return output


def _join_reason_copy(*values: str) -> str:
    return "；".join(str(value) for value in values if value)


def _chip_reason_copy(row: dict) -> str:
    reasons = str(row.get("chip_volume_reasons") or "")
    if not reasons:
        return "无明确筹码/量价确认"
    labels = {
        "prev_day_high_volume": "前一日高量",
        "today_volume_confirm": "当日放量确认",
        "volume_not_confirmed": "量能未确认",
        "chip_peak_accumulation": "建仓峰 proxy",
        "chip_peak_washout": "洗盘峰 proxy",
        "chip_peak_markup": "拉升峰 proxy",
        "chip_peak_distribution": "出货峰 proxy",
        "chip_peak_neutral": "中性 proxy",
        "chip_volume_data_missing": "筹码/量价数据缺失",
        "overhead_pressure_high": "上方压力偏高",
        "downside_support_visible": "下方支撑可见",
        "main_force_chip_proxy_positive": "主力 proxy 偏正",
        "main_force_chip_proxy_negative": "主力 proxy 偏负",
        "main_force_chip_proxy_neutral": "主力 proxy 中性",
        "main_force_from_candidate_fields": "使用候选行资金字段估算",
        "main_force_from_fund_flow": "使用资金流估算",
        "main_force_proxy_missing": "主力 proxy 缺失",
        "chip_history_short": "历史窗口不足",
    }
    tokens = [token.strip() for token in reasons.replace(";", "|").split("|") if token.strip()]
    return "；".join(labels.get(token, token.replace("_", " ")) for token in tokens[:6])


def _chip_peak_label(value: object) -> str:
    return {
        "accumulation": "建仓峰",
        "washout": "洗盘峰",
        "markup": "拉升峰",
        "distribution": "出货峰",
        "neutral": "中性",
    }.get(str(value), str(value))


def _volume_signal_label(value: object) -> str:
    return {
        "today_confirmed": "当日放量确认",
        "prev_high_volume": "前日高量待确认",
        "weak_volume": "量能未确认",
        "volume_data_missing": "量能数据缺失",
        "chip_volume_disabled": "未启用",
    }.get(str(value), str(value))


def _fmt(value: object, digits: int = 3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:.{digits}f}"


def _theme_state_copy(value: str) -> str:
    return THEME_MARKET_STATE_LABELS.get(str(value), str(value))


def _escape(value: str) -> str:
    return str(value).replace("|", "/").replace("\n", " ")
