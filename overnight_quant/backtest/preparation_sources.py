from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


REQUIRED_DAILY_VALUES = ("trade_date", "code", "name", "open", "high", "low", "close", "volume", "amount")
REQUIRED_BENCHMARK_VALUES = ("trade_date", "open", "high", "low", "close")


@dataclass
class SourceError:
    source: str
    code: str
    trade_date: str = ""
    error_code: str = ""
    detail: str = ""
    recoverable: bool = True
    stage: str = ""
    error_message: str = ""
    retry_count: int = 0


@dataclass
class SourceBatch:
    daily_rows: list[dict] = field(default_factory=list)
    benchmark_rows: list[dict] = field(default_factory=list)
    errors: list[SourceError] = field(default_factory=list)
    audit: dict = field(default_factory=dict)


class LocalRawSource:
    name = "local-raw"

    def __init__(self, raw_dir: str | Path):
        self.raw_dir = Path(raw_dir)

    def load(self, codes: list[str], start: str, end: str) -> SourceBatch:
        requested = set(codes)
        daily_rows, errors = _read_daily_rows(
            self.raw_dir / "daily_bars.csv", requested, start, end, self.name
        )
        benchmark_rows, benchmark_errors = _read_benchmark_rows(
            self.raw_dir / "benchmark_bars.csv", start, end, self.name
        )
        return SourceBatch(
            daily_rows=daily_rows,
            benchmark_rows=benchmark_rows,
            errors=errors + benchmark_errors,
        )


class SamplePreparationSource(LocalRawSource):
    name = "sample"

    def load(self, codes: list[str], start: str, end: str) -> SourceBatch:
        batch = super().load(codes, start, end)
        selection_rows = _read_sample_selection_rows(
            self.raw_dir / "selection_snapshots.csv", set(codes), start, end
        )
        values_by_key = {
            (str(row["trade_date"]), str(row["code"]).zfill(6)): row
            for row in selection_rows
        }
        for daily_row in batch.daily_rows:
            key = (str(daily_row["trade_date"]), str(daily_row["code"]).zfill(6))
            daily_row.update(values_by_key.get(key, {}))
        return batch


def _read_daily_rows(
    path: Path, codes: set[str], start: str, end: str, source: str
) -> tuple[list[dict], list[SourceError]]:
    if not path.exists():
        return [], [SourceError(source, "", "", "RAW_DAILY_BARS_MISSING", str(path), False)]
    rows: list[dict] = []
    errors: list[SourceError] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                code = str(raw.get("code") or "").zfill(6)
                trade_date = str(raw.get("trade_date") or "")
                if code not in codes or trade_date < start or trade_date > end:
                    continue
                if not _valid_values(raw, REQUIRED_DAILY_VALUES):
                    errors.append(
                        SourceError(
                            source,
                            code,
                            trade_date,
                            "RAW_ROW_INVALID",
                            "daily_bars required value missing or invalid",
                        )
                    )
                    continue
                row = dict(raw)
                row["code"] = code
                rows.append(row)
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(SourceError(source, "", "", "RAW_READ_FAILED", str(exc), False))
    return rows, errors


def _read_benchmark_rows(
    path: Path, start: str, end: str, source: str
) -> tuple[list[dict], list[SourceError]]:
    if not path.exists():
        return [], []
    rows: list[dict] = []
    errors: list[SourceError] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                trade_date = str(raw.get("trade_date") or "")
                if trade_date < start or trade_date > end:
                    continue
                if not _valid_values(raw, REQUIRED_BENCHMARK_VALUES):
                    errors.append(
                        SourceError(
                            source,
                            "BENCHMARK",
                            trade_date,
                            "RAW_BENCHMARK_ROW_INVALID",
                            "benchmark required value missing or invalid",
                        )
                    )
                    continue
                rows.append(dict(raw))
    except (OSError, UnicodeError, csv.Error) as exc:
        errors.append(SourceError(source, "BENCHMARK", "", "RAW_READ_FAILED", str(exc), False))
    return rows, errors


def _read_sample_selection_rows(path: Path, codes: set[str], start: str, end: str) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            code = str(raw.get("code") or "").zfill(6)
            trade_date = str(raw.get("trade_date") or "")
            if code in codes and start <= trade_date <= end:
                row = dict(raw)
                row["code"] = code
                rows.append(row)
    return rows


def _valid_values(row: dict, fields: tuple[str, ...]) -> bool:
    for field in fields:
        value = row.get(field)
        if value in (None, "", "-"):
            return False
        if field not in {"trade_date", "code", "name"}:
            try:
                float(value)
            except (TypeError, ValueError):
                return False
    return True
