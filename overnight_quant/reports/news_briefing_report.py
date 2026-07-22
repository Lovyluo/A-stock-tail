from __future__ import annotations

from pathlib import Path


def write_news_briefing_report(result: dict, reports_dir: str) -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    output = path / f"news_briefing_{result['trade_date']}.md"
    lines = [
        "# 盘前消息面汇总", "",
        f"trade_date: {result.get('trade_date', '')}",
        f"run_time: {result.get('run_time', '')}",
        f"mode: {result.get('mode', '')}",
        f"status: {result.get('status', '')}",
        f"window_start: {result.get('window_start', '')}",
        f"window_end: {result.get('window_end', '')}", "",
        "## 数据源清单和抓取时间", "",
    ]
    for item in result.get("sources") or []:
        lines.append(f"- {item.get('source')}: {'OK' if item.get('ok') else 'MISSING'}, rows={item.get('rows', 0)}, fetched_at={item.get('fetched_at', '')}, error={item.get('error', '')}")
    if not result.get("sources"):
        lines.append("- 暂无可用来源。")
    _section(lines, "宏观消息", result.get("macro_news"))
    _section(lines, "政策/监管消息", result.get("policy_news"))
    _section(lines, "产业/题材消息", result.get("theme_news"))
    _section(lines, "个股公告/新闻", result.get("stock_news"))
    _text_section(lines, "今日关注方向", result.get("focus_directions"))
    _text_section(lines, "分歧后的进攻方案", result.get("attack_plan"))
    _text_section(lines, "分歧后的防御方案", result.get("defence_plan"))
    _text_section(lines, "风险提示", result.get("risk_notes"))
    lines.extend(["", "本汇总采用关键词和抽取式规则，仅用于信息整理与观察，不构成投资建议，也不执行交易。", ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return str(output)


def _section(lines: list[str], title: str, rows) -> None:
    lines.extend(["", f"## {title}", ""])
    for row in rows or []:
        prefix = f"{row.get('code', '')} {row.get('name', '')}".strip()
        lines.append(f"- {prefix + ': ' if prefix else ''}{row.get('title') or row.get('summary') or '未命名消息'} [{row.get('source', '')}]")
    if not rows:
        lines.append("- 暂无已提取条目。")


def _text_section(lines: list[str], title: str, rows) -> None:
    lines.extend(["", f"## {title}", ""])
    lines.extend([f"- {item}" for item in rows or []] or ["- 暂无。"])
