from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any


class SimpleTable:
    """Small DataFrame-like fallback used when pandas is unavailable."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, columns: list[str] | None = None):
        self._rows = rows or []
        self.columns = columns or (list(self._rows[0]) if self._rows else [])

    @property
    def empty(self) -> bool:
        return len(self._rows) == 0

    def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
        if orient != "records":
            raise ValueError("SimpleTable only supports orient='records'")
        return list(self._rows)


def find_latest_file(pattern: str, directory: Path) -> Path | None:
    directory = Path(directory)
    if not directory.exists():
        return None
    matches = [path for path in directory.glob(pattern) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: (path.stat().st_mtime, path.name))


def parse_key_value_md(path: Path) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        return {"status": "MISSING", "path": str(path)}

    result: dict[str, str] = {"path": str(path)}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            break
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized = _normalize_key(key)
        if normalized:
            result[normalized] = value.strip()
    result.setdefault("status", "UNKNOWN")
    return result


def parse_preflight_report(path: Path) -> dict[str, str]:
    return _with_type(parse_key_value_md(path), "preflight")


def parse_dry_run_report(path: Path) -> dict[str, str]:
    return _with_type(parse_key_value_md(path), "dry_run")


def parse_live_quality_report(path: Path) -> dict[str, str]:
    return _with_type(parse_key_value_md(path), "live_quality")


def parse_after_close_report(path: Path) -> dict[str, str]:
    result = _with_type(parse_key_value_md(path), "after_close")
    if result.get("status") == "NOT_TAIL_OBSERVATION_WINDOW":
        result["source_status"] = result["status"]
        result["status"] = "NOT_AFTER_CLOSE"
    return result


def parse_intraday_report(path: Path) -> dict[str, str]:
    return _with_type(parse_key_value_md(path), "intraday")


def parse_auction_report(path: Path) -> dict[str, str]:
    return _with_type(parse_key_value_md(path), "auction")


def parse_news_briefing_report(path: Path) -> dict[str, str]:
    return _with_type(parse_key_value_md(path), "news_briefing")


def parse_sell_plan_table(path: Path):
    return _parse_markdown_table_sections(
        Path(path),
        [
            "持仓卖出计划明细",
            "Position Sell Plan Details",
        ],
    )


def parse_after_close_risk_table(path: Path):
    return _parse_markdown_table_sections(
        Path(path),
        [
            "C Class Risk Observation / Do Not Chase",
            "C类风险观察 / 不建议追",
            "C 类风险观察 / 不建议追",
        ],
    )


def parse_after_close_chip_volume_table(path: Path):
    return _parse_markdown_table_sections(
        Path(path),
        [
            "筹码与量价确认",
            "Chip Volume Confirmation",
        ],
    )


def parse_watchlist_csv(path: Path):
    return _read_csv_table(Path(path))


def parse_signals_csv(path: Path):
    return _read_csv_table(Path(path))


def _read_csv_table(path: Path):
    if not path.exists():
        return SimpleTable([], [])
    try:
        import pandas as pd

        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            columns = list(reader.fieldnames or [])
        return SimpleTable(rows, columns)


def _with_type(data: dict[str, str], report_type: str) -> dict[str, str]:
    data = dict(data)
    data["report_type"] = report_type
    return data


def _parse_markdown_table_section(path: Path, section_title: str):
    return _parse_markdown_table_sections(path, [section_title])


def _parse_markdown_table_sections(path: Path, section_titles: list[str]):
    if not path.exists():
        return SimpleTable([], [])
    lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    in_section = False
    table_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("## ") and in_section:
            break
        if line.startswith("## ") and any(section_title in line for section_title in section_titles):
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("|") and line.endswith("|"):
            table_lines.append(line)
    if len(table_lines) < 2:
        return SimpleTable([], [])

    header = _split_markdown_row(table_lines[0])
    rows: list[dict[str, Any]] = []
    for raw_row in table_lines[2:]:
        values = _split_markdown_row(raw_row)
        if not values or len(values) != len(header):
            continue
        rows.append(dict(zip(header, values)))
    return SimpleTable(rows, header)


def _split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _normalize_key(key: str) -> str:
    cleaned = key.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_\u4e00-\u9fff]+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned
