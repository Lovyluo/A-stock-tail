from __future__ import annotations

import csv
from pathlib import Path

from overnight_quant.scripts.prepare_backtest_data import run_prepare
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


class FakeAStockRealHistoricalClient:
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
            raise RuntimeError("fake-real daily failure")
        return list(self.daily_by_code.get(code, []))

    def fetch_stock_metadata(self, code: str) -> dict:
        self.calls.append(("metadata", code))
        if ("metadata", code) in self.failures:
            raise RuntimeError("fake-real metadata failure")
        return dict(self.metadata_by_code.get(code, {}))

    def fetch_benchmark_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        self.calls.append(("benchmark", symbol, start, end))
        if ("benchmark", symbol) in self.failures:
            raise RuntimeError("fake-real benchmark failure")
        return list(self.benchmark)

    def fetch_fund_flow(self, code: str, start: str, end: str) -> list[dict]:
        self.calls.append(("fund_flow", code, start, end))
        raise AssertionError("fund flow remains unavailable in fake-real validation")


def test_non_dry_without_enable_flag_returns_real_network_not_enabled(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        codes=["600519"],
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        overwrite=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_NETWORK_NOT_ENABLED"
    assert result["network_requests_made"] == 0
    assert not (tmp_path / "processed").exists()


def test_enabled_dry_run_makes_zero_fake_real_client_calls(tmp_path):
    client = FakeAStockRealHistoricalClient()
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_historical_client=client,
        codes=["600519"],
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["status"] == "DRY_RUN"
    assert result["network_requests_made"] == 0
    assert result["effective_real_request_max_codes"] == 1
    assert result["requested_date_range_days"] == 10
    assert client.calls == []
    assert not (tmp_path / "processed").exists()


def test_enabled_real_request_rejects_more_than_three_codes_without_calls(tmp_path):
    client = FakeAStockRealHistoricalClient()
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_historical_client=client,
        codes=["600519", "300750", "510300", "000001"],
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=10,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT"
    assert result["network_requests_made"] == 0
    assert client.calls == []
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "requested_codes_count: 4" in report
    assert "effective_real_request_max_codes: 3" in report


def test_enabled_real_request_rejects_more_than_31_days_without_calls(tmp_path):
    client = FakeAStockRealHistoricalClient()
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_historical_client=client,
        codes=["600519"],
        start="2025-01-01",
        end="2025-02-01",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT"
    assert result["network_requests_made"] == 0
    assert client.calls == []
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "requested_date_range_days: 32" in report
    assert "max_allowed_days: 31" in report


def test_fake_real_client_prepares_processed_data_with_audited_truth_levels(tmp_path):
    client = _client(include_enhancement_inputs=True)
    result = _prepare(tmp_path, client)

    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    selection = Path(result["out_dir"], "selection_snapshots.csv").read_text(encoding="utf-8")
    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert result["status"] == "PREPARE_COMPLETED"
    assert result["network_requests_made"] == 0
    assert client.calls
    assert "real_request_enabled: true" in manifest
    assert "request_contract: fake_real_validation" in manifest
    assert "fake_real_client_prepared: true" in manifest
    assert "network_prepared: false" in manifest
    assert "SAMPLE_FIXTURE: prohibited_for_a_stock_data" in manifest
    assert "SHOULD_NOT_SURVIVE" not in selection
    assert "current_hot" not in selection
    assert "selection_snapshots,theme_tags,0,7,0.0,UNAVAILABLE" in coverage
    assert "selection_snapshots,main_net,0,7,0.0,UNAVAILABLE" in coverage
    assert "selection_snapshots,tail_pullback_pct,0,7,0.0,UNAVAILABLE" in coverage
    assert "effective_real_request_max_codes: 1" in report
    assert "requested_date_range_days: 10" in report


def test_fake_real_cache_hit_uses_separate_namespace_and_avoids_second_call(tmp_path):
    config = _config(tmp_path)
    client = _client()

    first = _prepare(tmp_path, client, config=config, out_name="first")
    calls_after_first = list(client.calls)
    second = _prepare(tmp_path, client, config=config, out_name="second")

    assert first["status"] == "PREPARE_COMPLETED"
    assert second["status"] == "PREPARE_COMPLETED"
    assert client.calls == calls_after_first
    cache_files = list((tmp_path / "cache" / "a_stock_data").rglob("*.json"))
    assert any("fake_real_contract_v1" in str(path) for path in cache_files)


def test_fake_real_invalid_cache_records_failure_then_refetches_injected_client(tmp_path):
    from overnight_quant.backtest.astock_historical_source import (
        ASTOCK_FAKE_REAL_ENDPOINT_VERSION,
        build_cache_path,
    )

    config = _config(tmp_path)
    invalid = build_cache_path(
        Path(config["backtest"]["historical_cache_dir"]),
        "daily_bars",
        ASTOCK_FAKE_REAL_ENDPOINT_VERSION,
        "600519",
        "2025-01-01",
        "2025-01-10",
        {"code": "600519", "start": "2025-01-01", "end": "2025-01-10"},
    )
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("{invalid-json", encoding="utf-8")
    client = _client()

    result = _prepare(tmp_path, client, config=config)

    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert result["status"] == "PARTIAL_DATA_PREPARED"
    assert "CACHE_READ_FAILED" in errors
    assert ("daily", "600519", "2025-01-01", "2025-01-10") in client.calls


def test_fake_real_missing_safety_fields_are_rejected_offline(tmp_path):
    config = _config(tmp_path)
    client = _client(include_safety=False, include_metadata=False)
    result = _prepare(tmp_path, client, config=config)

    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    assert "daily_bars,limit_up,0,7,0.0,UNKNOWN" in coverage
    assert "daily_bars,list_date,0,7,0.0,UNKNOWN" in coverage

    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="fake-real-missing-safety",
        config=config,
    )
    rejected = Path(backtest["output_dir"], "rejections.csv").read_text(encoding="utf-8")
    assert "limit_price_unknown" in rejected
    assert "st_status_unknown" in rejected
    assert "suspended_status_unknown" in rejected
    assert "list_date_missing" in rejected


def test_fake_real_partial_success_writes_source_error(tmp_path):
    client = _client()
    client.failures.add(("daily", "300750"))

    result = _prepare(tmp_path, client, codes=["600519", "300750"])

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "300750,a-stock-data,daily_bars_primary,HISTORICAL_DAILY_BAR_SOURCE_FAILED" in errors


def test_fake_real_all_daily_failures_return_no_dataset(tmp_path):
    client = _client()
    client.failures.add(("daily", "600519"))

    result = _prepare(tmp_path, client)

    assert result["error"] == "NO_DAILY_BARS_FETCHED"
    assert not Path(result["out_dir"], "daily_bars.csv").exists()
    assert result["network_requests_made"] == 0


def test_fake_real_benchmark_rows_create_daily_market_proxy(tmp_path):
    result = _prepare(tmp_path, _client())

    rows = _read_csv(Path(result["out_dir"], "market_snapshots.csv"))
    assert rows
    assert rows[-1]["market_proxy_used"] == "true"
    assert rows[-1]["market_reason"] == "benchmark_direction_proxy"


def test_fake_real_benchmark_failure_leads_to_market_data_unavailable(tmp_path):
    config = _config(tmp_path)
    client = _client()
    client.failures.add(("benchmark", "sh000300"))
    result = _prepare(tmp_path, client, config=config)

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="fake-real-no-benchmark",
        config=config,
    )
    skipped = Path(backtest["output_dir"], "skipped_days.csv").read_text(encoding="utf-8")
    assert "market_data_unavailable" in skipped


def test_fake_real_processed_dataset_is_consumed_offline_only(tmp_path):
    config = _config(tmp_path)
    client = _client()
    prepared = _prepare(tmp_path, client, config=config)
    calls_after_prepare = list(client.calls)

    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=prepared["out_dir"],
        run_id="fake-real-provider-load",
        config=config,
    )

    assert "error" not in backtest
    assert client.calls == calls_after_prepare
    summary = Path(backtest["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "Report Fidelity: DAILY_PROXY" in summary
    assert not Path(config["paths"]["records_dir"]).exists()
    assert not Path(config["paths"]["reports_dir"]).exists()
    assert not Path(config["paths"]["examples_dir"]).exists()


def test_fake_real_phase_contains_no_real_network_or_execution_implementation():
    root = Path(__file__).resolve().parents[1]
    production_paths = [
        root / "backtest" / "astock_historical_source.py",
        root / "backtest" / "data_preparation.py",
        root / "scripts" / "prepare_backtest_data.py",
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore").lower()
        for path in production_paths
    )
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
    assert not any(token in text for token in forbidden)


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


def _client(
    include_safety: bool = True,
    include_metadata: bool = True,
    include_enhancement_inputs: bool = False,
) -> FakeAStockRealHistoricalClient:
    metadata = {"name": "Fake Real History", "list_date": "2001-08-27"} if include_metadata else {}
    return FakeAStockRealHistoricalClient(
        daily_by_code={
            "600519": _daily_rows("600519", include_safety, include_enhancement_inputs),
            "300750": _daily_rows("300750", include_safety, include_enhancement_inputs),
        },
        metadata_by_code={"600519": metadata, "300750": metadata},
        benchmark=_benchmark_rows(),
    )


def _prepare(
    tmp_path: Path,
    client: FakeAStockRealHistoricalClient,
    config: dict | None = None,
    codes: list[str] | None = None,
    out_name: str = "processed",
) -> dict:
    requested = codes or ["600519"]
    return run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_historical_client=client,
        codes=requested,
        start="2025-01-01",
        end="2025-01-10",
        out_dir=str(tmp_path / out_name),
        max_codes=len(requested),
        sleep=0.5,
        overwrite=True,
        config=config or _config(tmp_path),
    )


def _daily_rows(code: str, include_safety: bool, include_enhancement_inputs: bool) -> list[dict]:
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
    closes = ["9.50", "9.60", "9.70", "9.80", "9.90", "10.20", "10.50"]
    rows: list[dict] = []
    for trade_date, close in zip(dates, closes):
        special = trade_date == "2025-01-09"
        row = {
            "trade_date": trade_date,
            "code": code,
            "name": "Fake Real History",
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
                    "theme_tags": "SHOULD_NOT_SURVIVE",
                    "theme_rank": "1",
                    "main_net": "999",
                    "big_order_net": "888",
                    "tail_pullback_pct": "0.1",
                    "theme_source": "current_hot",
                    "capital_source": "current_flow",
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
