import hashlib
import json
from pathlib import Path

from overnight_quant.backtest.astock_historical_source import (
    ASTOCK_REAL_ENDPOINT_VERSION,
    build_cache_path,
)
from overnight_quant.backtest.astock_real_historical_client import AStockRealHistoricalClient
from overnight_quant.backtest.historical_data import LocalCsvHistoricalDataProvider
from overnight_quant.scripts.prepare_backtest_data import run_prepare
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def test_real_first_run_rejects_two_codes_before_client_construction(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519", "300750"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=3,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_FIRST_RUN_SCOPE_TOO_LARGE"
    assert result["network_requests_made"] == 0
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "real_first_run_max_codes: 1" in report


def test_real_first_run_rejects_eleven_days_before_client_construction(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519"],
        start="2025-01-01",
        end="2025-01-11",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_FIRST_RUN_SCOPE_TOO_LARGE"
    assert result["network_requests_made"] == 0


def test_real_first_run_valid_dry_run_makes_zero_requests(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        codes=["600519"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["status"] == "DRY_RUN"
    assert result["request_contract"] == "real_first_run"
    assert result["network_requests_made"] == 0


def test_real_client_accepts_only_requested_dated_daily_rows(tmp_path):
    client = _real_client(
        tmp_path,
        FakeJsonTransport(
            responses={"baidu_daily_kline": _baidu_payload(include_out_of_range=True)}
        ),
    )

    rows = client.fetch_daily_bars("600519", "2025-01-02", "2025-01-10")

    assert [row["trade_date"] for row in rows] == ["2025-01-02", "2025-01-03"]
    assert any(
        error.error_code == "HISTORICAL_ROW_OUT_OF_REQUEST_RANGE"
        for error in client.drain_errors()
    )


def test_real_client_parses_stable_metadata_only(tmp_path):
    client = _real_client(
        tmp_path,
        FakeJsonTransport(responses={"eastmoney_stock_metadata": _metadata_payload()}),
    )

    metadata = client.fetch_stock_metadata("600519")

    assert metadata == {"name": "Kweichow Moutai", "list_date": "2001-08-27"}


def test_real_client_raw_cache_hit_avoids_transport_call(tmp_path):
    transport = FakeJsonTransport(responses={"baidu_daily_kline": _baidu_payload()})
    client = _real_client(tmp_path, transport)

    first = client.fetch_daily_bars("600519", "2025-01-02", "2025-01-10")
    calls_after_first = list(transport.calls)
    second = client.fetch_daily_bars("600519", "2025-01-02", "2025-01-10")

    assert second == first
    assert transport.calls == calls_after_first
    assert client.audit_snapshot()["cache_hits"] == 1
    assert client.audit_snapshot()["network_requests_made"] == 1


def test_real_client_http_and_json_failures_are_audited(tmp_path):
    http_client = _real_client(
        tmp_path / "http",
        FakeJsonTransport(failures={"baidu_daily_kline": OSError("HTTP 503")}),
    )
    json_client = _real_client(
        tmp_path / "json",
        FakeJsonTransport(raw_overrides={"baidu_daily_kline": "{not-json"}),
    )

    assert http_client.fetch_daily_bars("600519", "2025-01-02", "2025-01-10") == []
    assert json_client.fetch_daily_bars("600519", "2025-01-02", "2025-01-10") == []
    assert any(error.error_code == "HTTP_REQUEST_FAILED" for error in http_client.drain_errors())
    assert any(error.error_code == "JSON_PARSE_FAILED" for error in json_client.drain_errors())


def test_real_client_benchmark_and_fund_flow_remain_unavailable_without_requests(tmp_path):
    transport = FakeJsonTransport()
    client = _real_client(tmp_path, transport)

    assert client.fetch_benchmark_bars("sh000300", "2025-01-02", "2025-01-10") == []
    assert client.fetch_fund_flow("600519", "2025-01-02", "2025-01-10") == []

    assert transport.calls == []
    assert any(error.error_code == "BENCHMARK_UNAVAILABLE" for error in client.drain_errors())


def test_real_first_run_fake_transport_writes_processed_and_audit(tmp_path):
    transport = _successful_transport()

    result = _run_real_first_prepare(tmp_path, transport)

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    assert result["network_requests_made"] == 2
    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "request_contract: real_first_run" in manifest
    assert "network_prepared: true" in manifest
    assert "real_first_run_max_codes: 1" in report
    assert "real_first_run_max_days: 10" in report
    assert "backtest_network_access: false" in report
    assert "BENCHMARK_UNAVAILABLE" in errors


def test_real_first_run_metadata_failure_keeps_bars_and_blank_list_date(tmp_path):
    transport = FakeJsonTransport(
        responses={"baidu_daily_kline": _baidu_payload()},
        failures={"eastmoney_stock_metadata": OSError("metadata offline")},
    )

    result = _run_real_first_prepare(tmp_path, transport)

    rows = Path(result["out_dir"], "daily_bars.csv").read_text(encoding="utf-8")
    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    assert result["status"] == "PARTIAL_DATA_PREPARED"
    assert "2001-08-27" not in rows
    assert "2025-01-02,600519,600519," in rows
    assert "daily_bars,list_date,0,2,0.0,UNKNOWN" in coverage


def test_real_first_run_malformed_raw_cache_is_audited_then_refetched(tmp_path):
    cache_path = build_cache_path(
        tmp_path / "cache" / "a_stock_data",
        "baidu_daily_kline",
        ASTOCK_REAL_ENDPOINT_VERSION,
        "600519",
        "2025-01-02",
        "2025-01-10",
        _baidu_params("600519", "2025-01-02"),
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("{bad-json", encoding="utf-8")
    transport = _successful_transport()

    result = _run_real_first_prepare(tmp_path, transport)

    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "CACHE_READ_FAILED" in errors
    assert "cache_read_failures: 1" in report
    assert result["network_requests_made"] == 2
    assert any(call[0] == "baidu_daily_kline" for call in transport.calls)


def test_real_first_run_source_failures_are_written_to_audit_files(tmp_path):
    http_result = _run_real_first_prepare(
        tmp_path / "http",
        FakeJsonTransport(failures={"baidu_daily_kline": OSError("HTTP 503")}),
    )
    json_result = _run_real_first_prepare(
        tmp_path / "json",
        FakeJsonTransport(raw_overrides={"baidu_daily_kline": "{not-json"}),
    )

    assert http_result["error"] == "NO_DAILY_BARS_FETCHED"
    assert json_result["error"] == "NO_DAILY_BARS_FETCHED"
    assert "HTTP_REQUEST_FAILED" in Path(http_result["audit_files"]["source_errors"]).read_text(
        encoding="utf-8"
    )
    assert "HISTORICAL_DAILY_BAR_SOURCE_FAILED" in Path(
        http_result["audit_files"]["source_errors"]
    ).read_text(encoding="utf-8")
    assert "JSON_PARSE_FAILED" in Path(json_result["audit_files"]["source_errors"]).read_text(
        encoding="utf-8"
    )
    assert "HISTORICAL_DAILY_BAR_SOURCE_FAILED" in Path(
        json_result["audit_files"]["source_errors"]
    ).read_text(encoding="utf-8")


def test_real_first_run_metadata_failure_keeps_stable_source_error_code(tmp_path):
    result = _run_real_first_prepare(
        tmp_path,
        FakeJsonTransport(
            responses={"baidu_daily_kline": _baidu_payload()},
            failures={"eastmoney_stock_metadata": OSError("metadata offline")},
        ),
    )
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")

    assert "HTTP_REQUEST_FAILED" in errors
    assert "HISTORICAL_METADATA_FAILED" in errors


def test_real_first_run_replay_uses_raw_cache_without_transport_calls(tmp_path):
    first = _run_real_first_prepare(tmp_path, _successful_transport())
    replay_transport = FailIfCalledTransport()

    second = _run_real_first_prepare(tmp_path, replay_transport)

    assert first["network_requests_made"] == 2
    assert first["cache_writes"] == 2
    assert second["network_requests_made"] == 0
    assert second["cache_hits"] >= 2
    assert second["cache_writes"] == 0
    assert replay_transport.calls == []


def test_real_first_run_report_discloses_cache_replay_counters(tmp_path):
    _run_real_first_prepare(tmp_path, _successful_transport())

    result = _run_real_first_prepare(tmp_path, FailIfCalledTransport())
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")

    assert result["cache_enabled"] is True
    assert result["cache_read_failures"] == 0
    assert "cache_enabled: true" in report
    assert "cache_hits: 2" in report
    assert "cache_writes: 0" in report
    assert "cache_read_failures: 0" in report
    assert "network_requests_made: 0" in report
    assert "backtest_network_access: false" in report
    assert "request_contract: real_first_run" in report


def test_real_first_run_cache_replay_preserves_processed_inputs(tmp_path):
    first = _run_real_first_prepare(tmp_path, _successful_transport())
    out_dir = Path(first["out_dir"])
    csv_names = (
        "daily_bars.csv",
        "selection_snapshots.csv",
        "market_snapshots.csv",
        "benchmark_bars.csv",
    )
    first_hashes = {name: _sha256(out_dir / name) for name in csv_names}
    first_manifest = _normalized_manifest(out_dir / "dataset_manifest.yaml")

    second = _run_real_first_prepare(tmp_path, FailIfCalledTransport())
    replay_out_dir = Path(second["out_dir"])

    assert {name: _sha256(replay_out_dir / name) for name in csv_names} == first_hashes
    assert _normalized_manifest(replay_out_dir / "dataset_manifest.yaml") == first_manifest


def test_real_first_run_prepared_data_is_consumed_offline_without_buy(tmp_path):
    config = _config(tmp_path)
    transport = _successful_transport()
    prepared = _run_real_first_prepare(tmp_path, transport, config=config)
    calls_after_prepare = list(transport.calls)

    result = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=prepared["out_dir"],
        run_id="real-first-run-offline",
        config=config,
    )

    assert "error" not in result
    assert transport.calls == calls_after_prepare
    skipped = Path(result["output_dir"], "skipped_days.csv").read_text(encoding="utf-8")
    summary = Path(result["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "market_data_unavailable" in skipped
    assert "Report Fidelity: DAILY_PROXY" in summary
    assert not Path(config["paths"]["records_dir"]).exists()
    assert not Path(config["paths"]["reports_dir"]).exists()
    assert not Path(config["paths"]["examples_dir"]).exists()


def test_daily_proxy_after_real_cache_replay_remains_offline(tmp_path):
    config = _config(tmp_path)
    _run_real_first_prepare(tmp_path, _successful_transport(), config=config)
    replay_transport = FailIfCalledTransport()
    prepared = _run_real_first_prepare(tmp_path, replay_transport, config=config)

    result = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=prepared["out_dir"],
        run_id="real-cache-replay-offline",
        config=config,
    )

    assert "error" not in result
    assert replay_transport.calls == []
    summary = Path(result["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "Report Fidelity: DAILY_PROXY" in summary


def test_real_first_run_path_contains_no_live_current_or_execution_integration():
    root = Path(__file__).resolve().parents[1]
    production_paths = [
        root / "backtest" / "astock_real_historical_client.py",
        root / "backtest" / "astock_historical_source.py",
        root / "backtest" / "data_preparation.py",
        root / "scripts" / "prepare_backtest_data.py",
        root / "backtest" / "backtest_engine.py",
        root / "scripts" / "run_backtest.py",
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore").lower()
        for path in production_paths
    )
    forbidden = [
        "overnight_quant.data.astock_client",
        "qt.gtimg.cn",
        "10jqka",
        "/fflow/",
        "mootdx",
        "pyautogui",
        "selenium",
        "place_order",
        "auto_order",
    ]

    assert all(value not in text for value in forbidden)


class FakeJsonTransport:
    def __init__(
        self,
        responses: dict[str, dict] | None = None,
        failures: dict[str, Exception] | None = None,
        raw_overrides: dict[str, str] | None = None,
    ):
        self.responses = responses or {}
        self.failures = failures or {}
        self.raw_overrides = raw_overrides or {}
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get_text(self, url, params, headers, timeout):
        endpoint = "baidu_daily_kline" if "baidu" in url else "eastmoney_stock_metadata"
        self.calls.append((endpoint, dict(params), timeout))
        if endpoint in self.failures:
            raise self.failures[endpoint]
        if endpoint in self.raw_overrides:
            return self.raw_overrides[endpoint]
        return json.dumps(self.responses.get(endpoint, {}))


class FailIfCalledTransport:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get_text(self, url, params, headers, timeout):
        self.calls.append((url, dict(params), timeout))
        raise AssertionError("cache replay must not call transport")


def _real_client(tmp_path: Path, transport: FakeJsonTransport) -> AStockRealHistoricalClient:
    return AStockRealHistoricalClient(
        transport=transport,
        cache_dir=tmp_path / "cache",
        sleep_seconds=0.5,
        sleep_fn=lambda _: None,
    )


def _run_real_first_prepare(
    tmp_path: Path,
    transport: FakeJsonTransport,
    config: dict | None = None,
) -> dict:
    return run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_transport=transport,
        codes=["600519"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        overwrite=True,
        config=config or _config(tmp_path),
    )


def _successful_transport() -> FakeJsonTransport:
    return FakeJsonTransport(
        responses={
            "baidu_daily_kline": _baidu_payload(),
            "eastmoney_stock_metadata": _metadata_payload(),
        }
    )


def _baidu_params(code: str, start: str) -> dict[str, str]:
    return {
        "all": "1",
        "isIndex": "false",
        "isBk": "false",
        "isBlock": "false",
        "isFutures": "false",
        "isStock": "true",
        "newFormat": "1",
        "group": "quotation_kline_ab",
        "finClientType": "pc",
        "code": code,
        "start_time": start.replace("-", ""),
        "ktype": "1",
    }


def _baidu_payload(include_out_of_range: bool = False) -> dict:
    rows = [
        "20250102,10.00,10.10,10.20,9.90,100,1010000",
        "20250103,10.10,10.20,10.30,10.00,110,1120000",
    ]
    if include_out_of_range:
        rows.append("20241231,9.50,9.60,9.70,9.40,90,900000")
    return {
        "Result": {
            "newMarketData": {
                "keys": ["time", "open", "close", "high", "low", "volume", "amount"],
                "marketData": ";".join(rows),
            }
        }
    }


def _metadata_payload() -> dict:
    return {"data": {"f58": "Kweichow Moutai", "f189": "20010827"}}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_manifest(path: Path) -> dict:
    value = LocalCsvHistoricalDataProvider(path.parent).quality_manifest()
    for key in {
        "created_at",
        "cache_enabled",
        "network_requests_made",
        "cache_hits",
        "cache_writes",
        "cache_read_failures",
        "endpoint_attempts",
        "endpoint_successes",
        "endpoint_failures",
        "prepare_report",
        "source_errors",
        "field_coverage",
        "audit_files",
        "generated_at",
        "run_time",
        "updated_at",
    }:
        value.pop(key, None)
    return value


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
