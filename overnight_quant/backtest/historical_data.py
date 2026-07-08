from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.backtest.fidelity_policy import DailyProxyPolicy

BOOL_FIELDS = {"is_st", "is_suspended", "is_new_stock", "is_limit_up", "is_bj", "is_bj_stock", "is_limit_down", "tail_30m_stable"}
INT_FIELDS = {"listed_days", "theme_rank", "same_theme_strong_count", "hot_theme_count", "limit_down_count", "limit_up_count"}
FLOAT_FIELDS = {
    "price",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "turnover_pct",
    "change_pct",
    "vol_ratio",
    "amount_wan",
    "float_mcap_yi",
    "limit_up",
    "limit_down",
    "tail_pullback_pct",
    "upper_shadow_ratio",
    "range_position",
    "big_order_net",
    "main_net",
    "ma5",
    "ma10",
    "ma20",
    "sh_change_pct",
    "sz_change_pct",
    "cyb_change_pct",
    "csi300_change_pct",
    "northbound_net_yi",
}


class SampleHistoricalDataProvider:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._selections = self._read_csv("selection_snapshots.csv")
        self._markets = self._read_csv("market_snapshots.csv")
        self._bars = self._read_csv("daily_bars.csv")
        self._benchmark = self._read_csv("benchmark_bars.csv")

    def trading_dates(self) -> list[str]:
        return sorted({row["trade_date"] for row in self._markets} | {row["trade_date"] for row in self._bars})

    def market_snapshot_asof(self, trade_date: str, as_of: str = "14:50") -> dict:
        row = next((item for item in self._markets if item["trade_date"] == trade_date and item["selection_as_of"] == as_of), None)
        if not row:
            return {}
        return {
            "date": trade_date,
            "indices": {
                "sh000001": {"name": "SSE Composite", "change_pct": row["sh_change_pct"]},
                "sz399001": {"name": "SZSE Component", "change_pct": row["sz_change_pct"]},
                "sz399006": {"name": "ChiNext", "change_pct": row["cyb_change_pct"]},
                "sh000300": {"name": "CSI 300", "change_pct": row["csi300_change_pct"]},
            },
            "tail_30m_stable": row["tail_30m_stable"],
            "hot_theme_count": row["hot_theme_count"],
            "northbound_net_yi": row["northbound_net_yi"],
            "limit_down_count": row["limit_down_count"],
            "limit_up_count": row["limit_up_count"],
            "selection_as_of": as_of,
        }

    def candidates_asof(self, trade_date: str, as_of: str = "14:50") -> list[dict]:
        rows = []
        for item in self._selections:
            if item["selection_date"] != trade_date or item["selection_as_of"] != as_of:
                continue
            rows.append({key: value for key, value in item.items() if not key.startswith("next_day_")})
        return rows

    def bars_until(self, code: str, trade_date: str) -> list[dict]:
        return [dict(row) for row in self._bars if row["code"] == code and row["trade_date"] <= trade_date]

    def exit_bar(self, code: str, trade_date: str) -> dict | None:
        row = next((item for item in self._bars if item["code"] == code and item["trade_date"] == trade_date), None)
        return dict(row) if row else None

    def benchmark_bars(self) -> list[dict]:
        return [dict(row) for row in self._benchmark]

    def quality_manifest(self) -> dict:
        manifest_path = self.data_dir / "dataset_manifest.yaml"
        text = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
        try:
            import yaml  # type: ignore

            return yaml.safe_load(text) or {}
        except Exception:
            return _read_top_level_manifest_values(text)

    def unavailable_fields(self) -> list[str]:
        optional_fields = ("theme_tags", "main_net", "big_order_net")
        return [
            field
            for field in optional_fields
            if any(row.get(field) in ("", None, []) for row in self._selections)
        ]

    def _read_csv(self, filename: str) -> list[dict]:
        path = self.data_dir / filename
        with path.open(newline="", encoding="utf-8") as handle:
            return [self._convert(row) for row in csv.DictReader(handle)]

    @staticmethod
    def _convert(row: dict) -> dict:
        converted = {}
        for field, value in row.items():
            if field == "theme_tags":
                converted[field] = value.split("|") if value else []
            elif field in BOOL_FIELDS:
                converted[field] = None if value in ("", None) else str(value).lower() == "true"
            elif field in INT_FIELDS:
                converted[field] = int(float(value)) if value else None
            elif field in FLOAT_FIELDS:
                converted[field] = float(value) if value else None
            else:
                converted[field] = value
        return converted


class HistoricalDataError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


class LocalCsvHistoricalDataProvider:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise HistoricalDataError("BACKTEST_DATA_DIR_NOT_FOUND", str(self.data_dir))
        manifest_path = self.data_dir / "dataset_manifest.yaml"
        if not manifest_path.exists():
            raise HistoricalDataError("DATASET_MANIFEST_REQUIRED", str(manifest_path))
        self.policy = DailyProxyPolicy()
        self._source_files = [{"table": "dataset_manifest", "file": manifest_path.name, "format": "yaml"}]
        self._bars = self._load_table("daily_bars", required=True)
        self._selections = self._load_table("selection_snapshots")
        self._markets = self._load_table("market_snapshots")
        self._benchmark = self._load_table("benchmark_bars")
        self._validate_bars()

    def trading_dates(self) -> list[str]:
        return sorted({row["trade_date"] for row in self._bars})

    def market_snapshot_asof(self, trade_date: str, as_of: str = "daily_close_proxy") -> dict:
        market = next((item for item in self._markets if item.get("trade_date") == trade_date), None)
        benchmark = next((item for item in self._benchmark if item.get("trade_date") == trade_date), None)
        return self.policy.market_view(trade_date, market, benchmark)

    def candidates_asof(self, trade_date: str, as_of: str = "daily_close_proxy") -> list[dict]:
        selection_by_code = {
            item["code"]: item
            for item in self._selections
            if item.get("trade_date") == trade_date
        }
        return [
            self.policy.candidate_view(row, selection_by_code.get(row["code"]), trade_date)
            for row in self._bars
            if row.get("trade_date") == trade_date
        ]

    def bars_until(self, code: str, trade_date: str) -> list[dict]:
        return [dict(row) for row in self._bars if row.get("code") == code and row.get("trade_date", "") <= trade_date]

    def exit_bar(self, code: str, trade_date: str) -> dict | None:
        row = next((item for item in self._bars if item.get("code") == code and item.get("trade_date") == trade_date), None)
        return dict(row) if row else None

    def benchmark_bars(self) -> list[dict]:
        return [dict(row) for row in self._benchmark]

    def quality_manifest(self) -> dict:
        text = (self.data_dir / "dataset_manifest.yaml").read_text(encoding="utf-8")
        raw_manifest = _read_top_level_manifest_values(text)
        try:
            import yaml  # type: ignore

            manifest = yaml.safe_load(text) or {}
        except Exception:
            manifest = raw_manifest
        source_as_of = _manifest_scalar(
            raw_manifest.get("source_selection_as_of")
            or raw_manifest.get("selection_as_of")
            or manifest.get("source_selection_as_of")
            or manifest.get("selection_as_of")
        )
        if source_as_of and source_as_of != self.policy.selection_as_of:
            manifest["source_selection_as_of"] = source_as_of
        manifest["selection_as_of"] = self.policy.selection_as_of
        manifest["data_fidelity"] = "daily_proxy"
        return manifest

    def quality_summary(self) -> dict:
        candidates = [candidate for trade_date in self.trading_dates() for candidate in self.candidates_asof(trade_date)]
        fields = [
            "limit_up",
            "limit_down",
            "is_st",
            "is_suspended",
            "list_date",
            "is_bj_stock",
            "vol_ratio",
            "range_position",
            "tail_pullback_pct",
            "theme_tags",
            "main_net",
            "big_order_net",
        ]
        coverage: list[dict] = []
        for field in fields:
            present = sum(1 for row in candidates if field not in row.get("_missing_input_fields", []) and row.get(field) not in (None, "", []))
            total = len(candidates)
            coverage.append(
                {
                    "field": field,
                    "present": present,
                    "total": total,
                    "coverage_pct": round(present / total * 100, 2) if total else 0.0,
                    "status": "complete" if present == total else "missing",
                }
            )
        proxy_fields = sorted({value for row in candidates for value in row.get("proxy_fields", [])})
        unavailable = sorted({value for row in candidates for value in row.get("_unavailable_reasons", [])})
        safety_rejections = sum(1 for row in candidates if row.get("_risk_unknown_reasons"))
        market_proxy_count = sum(1 for trade_date in self.trading_dates() if self.market_snapshot_asof(trade_date).get("_market_proxy_used"))
        return {
            "data_dir": str(self.data_dir),
            "source_files": list(self._source_files),
            "field_coverage": coverage,
            "proxy_fields": proxy_fields,
            "unavailable_fields": unavailable,
            "safety_unknown_candidate_count": safety_rejections,
            "market_proxy_used_count": market_proxy_count,
            "trade_date_start": self.trading_dates()[0] if self.trading_dates() else "",
            "trade_date_end": self.trading_dates()[-1] if self.trading_dates() else "",
            "candidate_count": len(candidates),
        }

    def unavailable_fields(self) -> list[str]:
        return self.quality_summary()["unavailable_fields"]

    def _load_table(self, stem: str, required: bool = False) -> list[dict]:
        csv_path = self.data_dir / f"{stem}.csv"
        parquet_path = self.data_dir / f"{stem}.parquet"
        if csv_path.exists():
            try:
                with csv_path.open(newline="", encoding="utf-8") as handle:
                    rows = [SampleHistoricalDataProvider._convert(row) for row in csv.DictReader(handle)]
                self._source_files.append({"table": stem, "file": csv_path.name, "format": "csv"})
                return rows
            except (OSError, ValueError) as exc:
                raise HistoricalDataError("BACKTEST_DATA_READ_ERROR", str(exc)) from exc
        if parquet_path.exists():
            try:
                import pandas as pd  # type: ignore

                rows = [SampleHistoricalDataProvider._convert(row) for row in pd.read_parquet(parquet_path).fillna("").to_dict("records")]
                self._source_files.append({"table": stem, "file": parquet_path.name, "format": "parquet"})
                return rows
            except ImportError as exc:
                raise HistoricalDataError("PARQUET_ENGINE_UNAVAILABLE", str(exc)) from exc
            except Exception as exc:
                if "parquet" in str(exc).lower() or "pyarrow" in str(exc).lower() or "fastparquet" in str(exc).lower():
                    raise HistoricalDataError("PARQUET_ENGINE_UNAVAILABLE", str(exc)) from exc
                raise HistoricalDataError("BACKTEST_DATA_READ_ERROR", str(exc)) from exc
        if required:
            raise HistoricalDataError("DAILY_BARS_REQUIRED", stem)
        return []

    def _validate_bars(self) -> None:
        required = ("trade_date", "code", "name", "open", "high", "low", "close")
        keys: set[tuple[str, str]] = set()
        for row in self._bars:
            if any(row.get(field) in (None, "") for field in required):
                raise HistoricalDataError("BACKTEST_DATA_VALIDATION_FAILED", "daily_bars required value missing")
            key = (str(row["trade_date"]), str(row["code"]))
            if key in keys:
                raise HistoricalDataError("BACKTEST_DATA_VALIDATION_FAILED", f"duplicate daily bar: {key}")
            keys.add(key)


def _read_top_level_manifest_values(text: str) -> dict:
    values: dict[str, str | list[str]] = {}
    active_list: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("  - ") and active_list:
            value = stripped[2:].strip().strip('"').strip("'")
            assert isinstance(values[active_list], list)
            values[active_list].append(value)
            continue
        if line.startswith(" ") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")
        if value:
            values[key] = value
            active_list = None
        else:
            values[key] = []
            active_list = key
    return values


def _manifest_scalar(value) -> str:
    if value in (None, "") or isinstance(value, list):
        return ""
    return str(value)
