from __future__ import annotations

import json
from pathlib import Path

from overnight_quant.scripts.prepare_backtest_data import run_prepare
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


class FakeExpandedTransport:
    def __init__(
        self,
        failed_daily_codes: set[str] | None = None,
        failed_metadata_codes: set[str] | None = None,
    ):
        self.failed_daily_codes = failed_daily_codes or set()
        self.failed_metadata_codes = failed_metadata_codes or set()
        self.calls: list[tuple[str, str]] = []

    def get_text(self, url, params, headers, timeout):
        if "getstockquotation" in url:
            code = params["code"]
            self.calls.append(("daily", code))
            if code in self.failed_daily_codes:
                raise OSError(f"daily unavailable for {code}")
            return json.dumps(_baidu_payload(code))
        code = params["secid"].split(".", 1)[1]
        self.calls.append(("metadata", code))
        if code in self.failed_metadata_codes:
            raise OSError(f"metadata unavailable for {code}")
        return json.dumps(_metadata_payload(code))


class FailIfCalledTransport:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def get_text(self, url, params, headers, timeout):
        self.calls.append((url, str(params)))
        raise AssertionError("expanded raw-cache replay must not call transport")


def test_default_minimal_scope_still_rejects_two_codes(tmp_path):
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
    assert result["real_request_scope"] == "minimal"
    assert result["network_requests_made"] == 0


def test_default_minimal_scope_still_rejects_more_than_ten_days(tmp_path):
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
    assert result["real_request_scope"] == "minimal"
    assert result["network_requests_made"] == 0


def test_expanded_scope_allows_three_codes_and_31_days_in_enabled_dry_run(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_request_scope="expanded",
        codes=["600519", "300750", "510300"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        max_codes=3,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["status"] == "DRY_RUN"
    assert result["real_request_scope"] == "expanded"
    assert result["effective_real_request_max_codes"] == 3
    assert result["max_allowed_days"] == 31
    assert result["cache_enabled"] is True
    assert result["network_requests_made"] == 0


def test_expanded_scope_without_enable_is_rejected_even_for_dry_run(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        real_request_scope="expanded",
        codes=["600519"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "processed"),
        max_codes=1,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_NETWORK_NOT_ENABLED"
    assert result["network_requests_made"] == 0


def test_expanded_scope_hard_limits_reject_without_network(tmp_path):
    codes_result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_request_scope="expanded",
        codes=["600519", "300750", "510300", "000001"],
        start="2025-01-02",
        end="2025-01-10",
        out_dir=str(tmp_path / "codes"),
        max_codes=4,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )
    days_result = run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_request_scope="expanded",
        codes=["600519"],
        start="2025-01-01",
        end="2025-02-01",
        out_dir=str(tmp_path / "days"),
        max_codes=3,
        sleep=0.5,
        dry_run=True,
        config=_config(tmp_path),
    )

    assert codes_result["error"] == "MAX_CODES_EXCEEDS_REAL_REQUEST_LIMIT"
    assert days_result["error"] == "DATE_RANGE_EXCEEDS_REAL_REQUEST_LIMIT"
    assert codes_result["network_requests_made"] == 0
    assert days_result["network_requests_made"] == 0


def test_expanded_real_transport_partial_success_is_audited(tmp_path):
    result = _prepare_expanded(
        tmp_path,
        FakeExpandedTransport(failed_daily_codes={"300750"}),
    )

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    assert result["partial_success"] is True
    assert result["failed_codes"] == ["300750"]
    assert result["real_request_scope"] == "expanded"
    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "real_request_scope: expanded" in manifest
    assert "partial_success: true" in report
    assert "failed_codes: 300750" in report
    assert "600519: daily_bars=SUCCESS, metadata=SUCCESS" in report
    assert "300750: daily_bars=FAILED, metadata=NOT_REQUESTED" in report
    assert "510300: daily_bars=SUCCESS, metadata=SUCCESS" in report
    assert "not implemented in real historical preparation client" in errors
    assert "DAILY_PROXY" in manifest
    assert "SAMPLE_FIXTURE: prohibited_for_a_stock_data" in manifest


def test_expanded_metadata_failure_marks_partial_success_without_failed_daily_code(tmp_path):
    result = _prepare_expanded(
        tmp_path,
        FakeExpandedTransport(failed_metadata_codes={"300750"}),
    )

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    assert result["partial_success"] is True
    assert result["failed_codes"] == []
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    assert "partial_success: true" in report
    assert "failed_codes: none" in report
    assert "300750: daily_bars=SUCCESS, metadata=FAILED" in report


def test_expanded_exact_replay_uses_cache_without_transport_calls(tmp_path):
    first = _prepare_expanded(tmp_path, FakeExpandedTransport())
    replay_transport = FailIfCalledTransport()
    second = _prepare_expanded(tmp_path, replay_transport)

    assert first["network_requests_made"] == 6
    assert first["cache_writes"] == 6
    assert second["network_requests_made"] == 0
    assert second["cache_hits"] >= 6
    assert second["cache_writes"] == 0
    assert replay_transport.calls == []


def test_expanded_processed_data_is_consumed_by_offline_daily_proxy(tmp_path):
    config = _config(tmp_path)
    transport = FakeExpandedTransport()
    prepared = _prepare_expanded(tmp_path, transport, config=config)
    calls_after_prepare = list(transport.calls)

    result = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=prepared["out_dir"],
        run_id="expanded-offline-daily-proxy",
        config=config,
    )

    assert "error" not in result
    assert transport.calls == calls_after_prepare
    summary = Path(result["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "Report Fidelity: DAILY_PROXY" in summary
    assert "strict_historical" in summary
    assert not Path(config["paths"]["records_dir"]).exists()
    assert not Path(config["paths"]["reports_dir"]).exists()


def test_expanded_path_contains_no_live_current_or_execution_integration():
    root = Path(__file__).resolve().parents[1]
    production_paths = [
        root / "backtest" / "astock_real_historical_client.py",
        root / "backtest" / "astock_historical_source.py",
        root / "backtest" / "data_preparation.py",
        root / "scripts" / "prepare_backtest_data.py",
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


def _prepare_expanded(tmp_path, transport, config=None):
    return run_prepare(
        source="a-stock-data",
        enable_real_astock_request=True,
        real_request_scope="expanded",
        codes=["600519", "300750", "510300"],
        start="2025-01-02",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        max_codes=3,
        sleep=0.5,
        overwrite=True,
        config=config or _config(tmp_path),
        real_transport=transport,
    )


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


def _baidu_payload(code: str) -> dict:
    return {
        "Result": {
            "newMarketData": {
                "keys": ["time", "open", "close", "high", "low", "volume", "amount"],
                "marketData": (
                    f"20250102,10.00,10.20,10.30,9.90,100000,{code[-3:]}0000;"
                    f"20250103,10.20,10.40,10.50,10.10,110000,{code[-3:]}5000"
                ),
            }
        }
    }


def _metadata_payload(code: str) -> dict:
    return {
        "data": {
            "f58": f"Historical {code}",
            "f189": "20010827",
        }
    }
