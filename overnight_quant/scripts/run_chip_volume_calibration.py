from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.ui.result_parser import (  # noqa: E402
    SimpleTable,
    find_latest_file,
    parse_after_close_chip_volume_table,
    parse_after_close_report,
    parse_watchlist_csv,
)

PEAK_TYPES = ["accumulation", "washout", "markup", "distribution", "neutral"]
DETERMINISTIC_WORDS = ["\u5fc5\u6da8", "\u4e70\u5165", "\u7a33\u8d5a"]

CH_CATEGORY = "\u5206\u7c7b"
CH_PEAK = "\u5cf0\u578b"
CH_PEAK_PROXY = "\u5cf0\u578b proxy"
CH_CONFIDENCE_DELTA = "\u7f6e\u4fe1\u5ea6\u53d8\u5316"
CH_CHIP_PEAK = "\u7b79\u7801\u5cf0\u578b"

PEAK_ALIASES = {
    "\u5efa\u4ed3\u5cf0": "accumulation",
    "accumulation": "accumulation",
    "\u6d17\u76d8\u5cf0": "washout",
    "washout": "washout",
    "\u62c9\u5347\u5cf0": "markup",
    "markup": "markup",
    "\u51fa\u8d27\u5cf0": "distribution",
    "distribution": "distribution",
    "\u4e2d\u6027": "neutral",
    "neutral": "neutral",
}


def load_latest_chip_volume_rows(
    root: Path | None = None, mode: str = "live"
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    base = Path(root) if root else ROOT
    package = base / "overnight_quant"
    reports = package / ("examples/reports" if mode == "demo" else "reports")
    records = package / ("examples/records" if mode == "demo" else "records")

    report_path = find_latest_file("after_close_analysis_*.md", reports)
    if report_path:
        table = parse_after_close_chip_volume_table(report_path)
        rows = _table_records(table)
        metadata = parse_after_close_report(report_path)
        if rows:
            metadata["source_path"] = str(report_path)
            metadata["source_type"] = "after_close_report"
            return rows, metadata

    watchlist_path = find_latest_file("next_morning_watchlist_*.csv", records)
    if watchlist_path:
        table = parse_watchlist_csv(watchlist_path)
        rows = _table_records(table)
        metadata = {"source_path": str(watchlist_path), "source_type": "watchlist_csv"}
        return rows, metadata

    return [], {"source_path": "", "source_type": "none", "date": date.today().isoformat()}


def summarize_chip_volume(
    rows: list[dict[str, Any]], metadata: dict[str, str] | None = None
) -> dict[str, Any]:
    metadata = metadata or {}
    normalized = [_normalize_row(row) for row in rows]
    peak_counts = Counter(row["peak_type"] for row in normalized)
    deltas = [row["confidence_delta"] for row in normalized]
    category_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in normalized:
        category_counts[row["category"] or "UNKNOWN"][row["peak_type"]] += 1

    return {
        "date": metadata.get("date") or metadata.get("trade_date") or date.today().isoformat(),
        "source_path": metadata.get("source_path", ""),
        "source_type": metadata.get("source_type", ""),
        "total_rows": len(normalized),
        "peak_counts": {peak: int(peak_counts.get(peak, 0)) for peak in PEAK_TYPES},
        "confidence_delta": {
            "count": len(deltas),
            "average": round(mean(deltas), 3) if deltas else 0.0,
            "min": min(deltas) if deltas else 0,
            "max": max(deltas) if deltas else 0,
            "distribution": dict(sorted(Counter(deltas).items())),
        },
        "category_peak_counts": {
            category: {peak: int(counter.get(peak, 0)) for peak in PEAK_TYPES}
            for category, counter in sorted(category_counts.items())
        },
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Chip Volume Observation Calibration Summary",
        "",
        f"date: {summary.get('date', '')}",
        f"source_type: {summary.get('source_type', '')}",
        f"source_path: {_sanitize_source_path(summary.get('source_path', ''))}",
        f"total_rows: {summary.get('total_rows', 0)}",
        "",
        "## Safety Boundary",
        "",
        "- This file contains aggregate proxy statistics only.",
        "- It does not include personal trading records, full live reports, or full records CSV data.",
        "- Chip metrics are proxy indicators for observation confidence, not real holder-cost data.",
        "- The summary is not investment advice and is not a standalone trade decision basis.",
        "",
        "## Peak Counts",
        "",
        "| peak_type | count |",
        "|---|---:|",
    ]
    peak_counts = summary.get("peak_counts") or {}
    for peak in PEAK_TYPES:
        lines.append(f"| {peak} | {int(peak_counts.get(peak, 0))} |")

    delta = summary.get("confidence_delta") or {}
    lines.extend(
        [
            "",
            "## Confidence Delta",
            "",
            f"- count: {delta.get('count', 0)}",
            f"- average: {delta.get('average', 0.0)}",
            f"- min: {delta.get('min', 0)}",
            f"- max: {delta.get('max', 0)}",
            "",
            "| confidence_delta | count |",
            "|---:|---:|",
        ]
    )
    for value, count in (delta.get("distribution") or {}).items():
        lines.append(f"| {value} | {count} |")

    lines.extend(
        [
            "",
            "## Category / Peak Mix",
            "",
            "| category | accumulation | washout | markup | distribution | neutral |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for category, counts in (summary.get("category_peak_counts") or {}).items():
        lines.append(
            f"| {category} | {counts.get('accumulation', 0)} | {counts.get('washout', 0)} | "
            f"{counts.get('markup', 0)} | {counts.get('distribution', 0)} | {counts.get('neutral', 0)} |"
        )
    lines.append("")
    text = "\n".join(lines)
    for word in DETERMINISTIC_WORDS:
        text = text.replace(word, "")
    return text


def write_summary(summary: dict[str, Any], root: Path | None = None) -> Path:
    base = Path(root) if root else ROOT
    output_dir = base / "overnight_quant" / "calibration"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_value = str(summary.get("date") or date.today().isoformat())
    path = output_dir / f"chip_volume_summary_{date_value}.md"
    path.write_text(render_summary_markdown(summary), encoding="utf-8")
    return path


def run_calibration(mode: str = "live", root: Path | None = None) -> dict[str, Any]:
    rows, metadata = load_latest_chip_volume_rows(root=root, mode=mode)
    summary = summarize_chip_volume(rows, metadata)
    output_path = write_summary(summary, root=root)
    return {**summary, "output_path": str(output_path)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize chip-volume proxy observations without committing raw live outputs."
    )
    parser.add_argument("--mode", choices=["live", "demo"], default="live")
    args = parser.parse_args()

    result = run_calibration(mode=args.mode)
    print(f"Mode: {args.mode}")
    print(f"Source Type: {result.get('source_type', '')}")
    print(f"Total Rows: {result.get('total_rows', 0)}")
    print(f"Output: {result['output_path']}")
    print("Risk Notice: proxy calibration only; not investment advice; no automated trading.")
    return 0


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    peak_type = _normalize_peak(
        _first_value(row, ["peak_type", "chip_peak_type", CH_PEAK_PROXY, CH_PEAK, CH_CHIP_PEAK])
    )
    return {
        "category": str(_first_value(row, ["category", CH_CATEGORY]) or "UNKNOWN").strip() or "UNKNOWN",
        "peak_type": peak_type,
        "confidence_delta": _to_int(_first_value(row, ["confidence_delta", CH_CONFIDENCE_DELTA])),
    }


def _normalize_peak(value: Any) -> str:
    text = str(value or "").strip().lower()
    return PEAK_ALIASES.get(text, "neutral")


def _first_value(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    for key, value in row.items():
        normalized = str(key).replace(" ", "").replace("_", "").lower()
        if "peak" in normalized and value not in (None, ""):
            return value
        if "confidence" in normalized and "delta" in normalized and value not in (None, ""):
            return value
    return ""


def _to_int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _table_records(table: Any) -> list[dict[str, Any]]:
    if table is None:
        return []
    try:
        if getattr(table, "empty", False):
            return []
        return list(table.to_dict("records"))
    except Exception:
        if isinstance(table, SimpleTable):
            return table.to_dict("records")
        return []


def _sanitize_source_path(value: Any) -> str:
    path = Path(str(value or ""))
    parts = list(path.parts)
    if "overnight_quant" in parts:
        return str(Path(*parts[parts.index("overnight_quant") :]))
    return path.name


if __name__ == "__main__":
    raise SystemExit(main())
