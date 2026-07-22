from __future__ import annotations

import csv
from pathlib import Path


AUCTION_FIELDS = [
    "code", "name", "source_bucket", "source_buckets", "auction_price", "prev_close",
    "auction_gap_pct", "auction_amount_wan", "volume_ratio", "market_auction_bias",
    "action_bias", "reasons", "risk_flags",
]


def write_auction_report(result: dict, reports_dir: str) -> str:
    path = Path(reports_dir)
    path.mkdir(parents=True, exist_ok=True)
    output = path / f"auction_observation_{result['trade_date']}.md"
    lines = [
        "# 集合竞价观察报告", "",
        f"trade_date: {result.get('trade_date', '')}",
        f"run_time: {result.get('run_time', '')}",
        f"mode: {result.get('mode', '')}",
        f"status: {result.get('status', '')}",
        f"market_auction_bias: {result.get('market_auction_bias', '')}",
        f"valid_for_trading_observation: {result.get('valid_for_trading_observation', '')}", "",
        "## 竞价观察明细", "",
        "| 代码 | 名称 | 来源 | 竞价价 | 缺口% | 量比 | 市场方向 | 攻防倾向 | 理由 | 风险 |",
        "|---|---|---|---:|---:|---:|---|---|---|---|",
    ]
    for row in result.get("rows") or []:
        lines.append(
            "| " + " | ".join(_cell(value) for value in [
                row.get("code"), row.get("name"), row.get("source_buckets"), row.get("auction_price"),
                row.get("auction_gap_pct"), row.get("volume_ratio"), row.get("market_auction_bias"),
                row.get("action_bias"), _join(row.get("reasons")), _join(row.get("risk_flags")),
            ]) + " |"
        )
    if not result.get("rows"):
        lines.append("| - | - | - | - | - | - | - | observe | 暂无候选 | 数据不足 |")
    lines.extend(["", "## 数据降级", ""])
    errors = result.get("source_errors") or []
    lines.extend([f"- {item}" for item in errors] or ["- 无已记录的数据源错误。"])
    lines.extend(["", "## 风险提示", "", "本报告仅用于集合竞价观察和攻防准备，不构成投资建议，不执行任何交易。", ""])
    output.write_text("\n".join(lines), encoding="utf-8")
    return str(output)


def write_auction_csv(result: dict, records_dir: str) -> str:
    path = Path(records_dir)
    path.mkdir(parents=True, exist_ok=True)
    output = path / f"auction_observation_{result['trade_date']}.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUCTION_FIELDS)
        writer.writeheader()
        for raw in result.get("rows") or []:
            row = dict(raw)
            row["reasons"] = _join(row.get("reasons"))
            row["risk_flags"] = _join(row.get("risk_flags"))
            writer.writerow({field: row.get(field, "") for field in AUCTION_FIELDS})
    return str(output)


def _join(value) -> str:
    return "；".join(str(item) for item in value) if isinstance(value, list) else str(value or "")


def _cell(value) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")
