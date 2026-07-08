from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.scripts.prepare_backtest_data import run_prepare
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


class FakeHistoricalClient:
    def __init__(
        self,
        daily_by_code: dict[str, list[dict]] | None = None,
        metadata_by_code: dict[str, dict] | None = None,
        benchmark: list[dict] | None = None,
        failures: set[tuple[str, str]] | None = None,
    ):
        self.daily_by_code = daily_by_code or {}
        self.metadata_by_code = metadata_by_code or {}
        self.benchmark = benchmark or []
        self.failures = failures or set()
        self.calls: list[tuple] = []

    def fetch_daily_bars(self, code: str, start: str, end: str) -> list[dict]:
        self.calls.append(("daily", code, start, end))
        if ("daily", code) in self.failures:
            raise RuntimeError("mock daily failure")
        return list(self.daily_by_code.get(code, []))

    def fetch_stock_metadata(self, code: str) -> dict:
        self.calls.append(("metadata", code))
        if ("metadata", code) in self.failures:
            raise RuntimeError("mock metadata failure")
        return dict(self.metadata_by_code.get(code, {}))

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        self.calls.append(("benchmark", symbol, start, end))
        if ("benchmark", symbol) in self.failures:
            raise RuntimeError("mock benchmark failure")
        return list(self.benchmark)

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        self.calls.append(("fund_flow", code, start, end))
        raise AssertionError("fund flow remains unavailable in mocked skeleton")


def test_astock_dry_run_makes_zero_client_calls_and_reports_zero_network(tmp_path):
    client = FakeHistoricalClient()

    result = run_prepare(
        source="a-stock-data",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        dry_run=True,
        config=_config(tmp_path),
        historical_client=client,
    )

    assert result["status"] == "DRY_RUN"
    assert result["network_requests_made"] == 0
    assert client.calls == []
    assert not (tmp_path / "processed").exists()
    assert not (tmp_path / "cache").exists()


def test_astock_requires_codes_before_client_use(tmp_path):
    client = FakeHistoricalClient()

    result = run_prepare(
        source="a-stock-data",
        codes=[],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        config=_config(tmp_path),
        historical_client=client,
    )

    assert result["error"] == "CODES_REQUIRED"
    assert client.calls == []


def test_astock_hard_limit_dry_run_rejects_without_truncation_or_client_calls(tmp_path):
    client = FakeHistoricalClient()
    codes = [f"3002{index:02d}" for index in range(11)]

    result = run_prepare(
        source="a-stock-data",
        codes=codes,
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        max_codes=20,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
        historical_client=client,
    )

    assert result["error"] == "MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT"
    assert client.calls == []
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "requested_codes_count: 11" in report
    assert "effective_max_codes: 10" in report
    assert "network_requests_made: 0" in report
    assert "MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT" in errors
    assert not (tmp_path / "processed").exists()


def test_astock_lower_user_max_rejects_without_selecting_subset(tmp_path):
    client = FakeHistoricalClient()

    result = run_prepare(
        source="a-stock-data",
        codes=["300201", "600519", "000001"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        max_codes=2,
        config=_config(tmp_path),
        historical_client=client,
    )

    assert result["error"] == "MAX_CODES_EXCEEDS_LIVE_PREP_LIMIT"
    assert client.calls == []
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "requested_codes_count: 3" in report
    assert "effective_max_codes: 2" in report


def test_astock_sleep_minimum_is_checked_before_disabled_real_execution(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        sleep=0.19,
        config=_config(tmp_path),
    )

    assert result["error"] == "SLEEP_BELOW_MINIMUM"


def test_astock_cli_boundary_refuses_real_execution_without_injected_client(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        sleep=0.5,
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_NETWORK_NOT_ENABLED"
    assert not (tmp_path / "processed").exists()


def test_astock_cache_hit_avoids_repeating_mock_fetches(tmp_path):
    config = _config(tmp_path)
    client = _client()

    first = _prepare(tmp_path, client, config=config, out_name="processed_first")
    calls_after_first = list(client.calls)
    second = _prepare(tmp_path, client, config=config, out_name="processed_second")

    assert first["status"] == "PREPARE_COMPLETED"
    assert second["status"] == "PREPARE_COMPLETED"
    assert client.calls == calls_after_first
    assert list((tmp_path / "cache" / "a_stock_data").rglob("*.json"))


def test_astock_invalid_cache_records_failure_then_fetches_from_mock(tmp_path):
    from overnight_quant.backtest.astock_historical_source import (
        ASTOCK_ENDPOINT_VERSION,
        build_cache_path,
    )

    config = _config(tmp_path)
    cache_root = Path(config["backtest"]["historical_cache_dir"])
    invalid = build_cache_path(
        cache_root,
        "daily_bars",
        ASTOCK_ENDPOINT_VERSION,
        "300201",
        "2025-01-01",
        "2025-01-31",
        {"code": "300201", "start": "2025-01-01", "end": "2025-01-31"},
    )
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("{not-json", encoding="utf-8")
    client = _client()

    result = _prepare(tmp_path, client, config=config)

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "CACHE_READ_FAILED" in errors
    assert ("daily", "300201", "2025-01-01", "2025-01-31") in client.calls


def test_astock_manifest_uses_truth_levels_without_fixture_enrichment(tmp_path):
    client = _client(include_enhancement_inputs=True)

    result = _prepare(tmp_path, client)

    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    selection = Path(result["out_dir"], "selection_snapshots.csv").read_text(encoding="utf-8")
    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    assert "REAL_HISTORICAL" in manifest
    assert "DAILY_PROXY" in manifest
    assert "UNAVAILABLE" in manifest
    assert "SAMPLE_FIXTURE: prohibited_for_a_stock_data" in manifest
    assert "AI_SAMPLE" not in selection
    assert "sample_fixture" not in selection
    assert "selection_snapshots,theme_tags,0,7,0.0,UNAVAILABLE" in coverage
    assert "selection_snapshots,theme_source,0,7,0.0,UNAVAILABLE" in coverage
    assert "selection_snapshots,capital_source,0,7,0.0,UNAVAILABLE" in coverage
    assert "selection_snapshots,range_position,7,7,100.0,DAILY_PROXY" in coverage


def test_astock_missing_safety_fields_stay_unknown_and_are_rejected(tmp_path):
    config = _config(tmp_path)
    client = _client(include_safety=False, include_metadata=False)

    result = _prepare(tmp_path, client, config=config)
    processed = _read_csv(Path(result["out_dir"], "daily_bars.csv"))
    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    assert processed[-1]["limit_up"] == ""
    assert processed[-1]["limit_down"] == ""
    assert processed[-1]["is_st"] == ""
    assert processed[-1]["is_suspended"] == ""
    assert processed[-1]["list_date"] == ""
    assert "daily_bars,limit_up,0,7,0.0,UNKNOWN" in coverage
    assert "daily_bars,list_date,0,7,0.0,UNKNOWN" in coverage

    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="astock-missing-safety",
        config=config,
    )
    rejection_text = Path(backtest["output_dir"], "rejections.csv").read_text(encoding="utf-8")
    assert "limit_price_unknown" in rejection_text
    assert "st_status_unknown" in rejection_text
    assert "suspended_status_unknown" in rejection_text
    assert "list_date_missing" in rejection_text


def test_astock_partial_success_writes_processed_data_and_source_error(tmp_path):
    client = _client()
    client.failures.add(("daily", "600519"))

    result = _prepare(tmp_path, client, codes=["300201", "600519"])

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "600519,a-stock-data,daily_bars_primary,HISTORICAL_DAILY_BAR_SOURCE_FAILED" in errors
    assert Path(result["out_dir"], "daily_bars.csv").exists()


def test_astock_all_daily_bar_failures_return_no_dataset(tmp_path):
    client = _client()
    client.failures.add(("daily", "300201"))

    result = _prepare(tmp_path, client)

    assert result["error"] == "NO_DAILY_BARS_FETCHED"
    assert not Path(tmp_path / "processed", "daily_bars.csv").exists()
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "HISTORICAL_DAILY_BAR_SOURCE_FAILED" in errors


def test_astock_benchmark_rows_build_market_proxy(tmp_path):
    result = _prepare(tmp_path, _client())

    rows = _read_csv(Path(result["out_dir"], "market_snapshots.csv"))
    assert rows
    assert rows[-1]["market_proxy_used"] == "true"
    assert rows[-1]["market_reason"] == "benchmark_direction_proxy"


def test_astock_benchmark_failure_keeps_market_data_unavailable(tmp_path):
    config = _config(tmp_path)
    client = _client()
    client.failures.add(("benchmark", "sh000300"))

    result = _prepare(tmp_path, client, config=config)

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    assert _read_csv(Path(result["out_dir"], "market_snapshots.csv")) == []
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "BENCHMARK_UNAVAILABLE" in errors
    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="astock-benchmark-missing",
        config=config,
    )
    skipped = Path(backtest["output_dir"], "skipped_days.csv").read_text(encoding="utf-8")
    assert "market_data_unavailable" in skipped


def test_astock_processed_data_is_offline_daily_proxy_and_does_not_touch_trade_state(tmp_path):
    config = _config(tmp_path)

    result = _prepare(tmp_path, _client(), config=config)
    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="astock-provider-load",
        config=config,
    )

    assert "error" not in backtest
    summary = Path(backtest["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "Report Fidelity: DAILY_PROXY" in summary
    assert Path(backtest["output_dir"]).parent == Path(config["backtest"]["output_dir"])
    assert not Path(config["paths"]["records_dir"]).exists()
    assert not Path(config["paths"]["reports_dir"]).exists()
    assert not Path(config["paths"]["examples_dir"]).exists()


def test_astock_skeleton_contains_no_network_or_automatic_execution_code():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "backtest" / "astock_historical_source.py",
        root / "backtest" / "data_preparation.py",
        root / "scripts" / "prepare_backtest_data.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8", errors="ignore").lower() for path in paths)
    forbidden = [
        "import requests",
        "from requests",
        "urllib.request",
        "mootdx",
        "qt.gtimg.cn",
        "finance.pae.baidu.com",
        "push2.eastmoney.com",
        "astock_client",
        "pyautogui",
        "selenium",
        "broker api",
        "auto" + "_order",
        "place" + "_order",
    ]
    assert not any(value in text for value in forbidden)


def _config(tmp_path: Path) -> dict:
    config = load_config()
    config["backtest"]["local_data_dir"] = str(tmp_path / "processed")
    config["backtest"]["manifest_dir"] = str(tmp_path / "manifests")
    config["backtest"]["historical_cache_dir"] = str(tmp_path / "cache" / "a_stock_data")
    config["backtest"]["output_dir"] = str(tmp_path / "backtest_outputs")
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    config["paths"]["examples_dir"] = str(tmp_path / "examples")
    return config


def _client(include_safety: bool = True, include_metadata: bool = True, include_enhancement_inputs: bool = False) -> FakeHistoricalClient:
    metadata = {"name": "Mock Historic", "list_date": "2020-01-01"} if include_metadata else {}
    return FakeHistoricalClient(
        daily_by_code={
            "300201": _dated_rows("300201", include_safety, include_enhancement_inputs),
            "600519": _dated_rows("600519", include_safety, include_enhancement_inputs),
        },
        metadata_by_code={"300201": metadata, "600519": metadata},
        benchmark=_benchmark_rows(),
    )


def _prepare(
    tmp_path: Path,
    client: FakeHistoricalClient,
    config: dict | None = None,
    codes: list[str] | None = None,
    out_name: str = "processed",
) -> dict:
    return run_prepare(
        source="a-stock-data",
        codes=codes or ["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / out_name),
        overwrite=True,
        sleep=0.5,
        config=config or _config(tmp_path),
        historical_client=client,
    )


def _dated_rows(code: str, include_safety: bool, include_enhancement_inputs: bool) -> list[dict]:
    closes = ["9.50", "9.60", "9.70", "9.80", "9.90", "10.20", "10.50"]
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
    rows: list[dict] = []
    for trade_date, close in zip(dates, closes):
        special = trade_date == "2025-01-09"
        row = {
            "trade_date": trade_date,
            "code": code,
            "name": "Mock Historic",
            "open": str(round(float(close) - 0.1, 2)),
            "high": "10.30" if special else str(round(float(close) + 0.1, 2)),
            "low": "9.90" if special else str(round(float(close) - 0.1, 2)),
            "close": close,
            "volume": "180" if special else "100",
            "amount": "300000000",
            "turnover_pct": "8",
            "float_mcap_yi": "100",
            "is_limit_down": "false",
        }
        if include_safety:
            row.update(
                {
                    "limit_up": "11.22",
                    "limit_down": "9.18",
                    "is_st": "false",
                    "is_suspended": "false",
                }
            )
        if include_enhancement_inputs and special:
            row.update(
                {
                    "theme_tags": "AI_SAMPLE",
                    "theme_rank": "1",
                    "main_net": "3000",
                    "big_order_net": "2000",
                    "theme_source": "sample_fixture",
                    "capital_source": "sample_fixture",
                }
            )
        rows.append(row)
    return rows


def _benchmark_rows() -> list[dict]:
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
    return [
        {"trade_date": trade_date, "open": "4000", "high": "4040", "low": "3990", "close": "4020"}
        for trade_date in dates
    ]


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
