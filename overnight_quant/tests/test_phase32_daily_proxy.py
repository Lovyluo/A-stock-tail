import csv
from pathlib import Path

from overnight_quant.backtest.historical_data import LocalCsvHistoricalDataProvider
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def test_local_csv_provider_loads_deterministic_data(tmp_path):
    data_dir = _write_local_dataset(tmp_path)

    provider = LocalCsvHistoricalDataProvider(data_dir)
    candidate = provider.candidates_asof("2026-04-01")[0]

    assert provider.trading_dates() == ["2026-04-01", "2026-04-02"]
    assert candidate["code"] == "300201"
    assert candidate["selection_as_of"] == "daily_close_proxy"
    assert "next_day_high" not in candidate


def test_daily_proxy_does_not_merge_future_selection_snapshot(tmp_path):
    data_dir = _write_local_dataset(tmp_path)
    with (data_dir / "selection_snapshots.csv").open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(
            ["2026-04-02", "300201", "9.9", "1.0", "0.0", "FutureTheme", "1", "9", "99999", "99999", "future_row", "999.0"]
        )

    candidate = LocalCsvHistoricalDataProvider(data_dir).candidates_asof("2026-04-01")[0]

    assert candidate["theme_tags"] == ["Robotics", "AI"]
    assert candidate["main_net"] == 3200.0


def test_missing_data_dir_returns_clear_error(tmp_path):
    result = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=str(tmp_path / "missing"),
        config=_config(tmp_path),
    )

    assert result["error"] == "BACKTEST_DATA_DIR_NOT_FOUND"


def test_missing_manifest_returns_clear_error(tmp_path):
    data_dir = _write_local_dataset(tmp_path, include_manifest=False)

    result = run_backtest(dataset="local", fidelity="daily_proxy", data_dir=str(data_dir), config=_config(tmp_path))

    assert result["error"] == "DATASET_MANIFEST_REQUIRED"


def test_unavailable_parquet_engine_returns_clear_error(tmp_path):
    data_dir = tmp_path / "parquet_data"
    data_dir.mkdir()
    (data_dir / "dataset_manifest.yaml").write_text("dataset: parquet_fixture\n", encoding="utf-8")
    (data_dir / "daily_bars.parquet").write_bytes(b"not-a-parquet-file")

    result = run_backtest(dataset="local", fidelity="daily_proxy", data_dir=str(data_dir), config=_config(tmp_path))

    assert result["error"] == "PARQUET_ENGINE_UNAVAILABLE"


def test_daily_proxy_trade_records_proxy_fidelity_and_asof(tmp_path):
    data_dir = _write_local_dataset(tmp_path)

    result = _run_local(tmp_path, data_dir, "proxy-trade")
    trade = result["trades"][0]

    assert trade["code"] == "300201"
    assert trade["data_fidelity"] == "daily_proxy"
    assert trade["selection_as_of"] == "daily_close_proxy"
    assert trade["sell_price"] == 10.3


def test_missing_safety_fields_reject_buy(tmp_path):
    data_dir = _write_local_dataset(tmp_path, missing_safety=True)

    result = _run_local(tmp_path, data_dir, "missing-safety")
    reasons = next(row["reasons"] for row in result["rejections"] if row["code"] == "300201")

    assert not result["trades"]
    assert {
        "limit_price_unknown",
        "st_status_unknown",
        "suspended_status_unknown",
        "list_date_missing",
        "bj_status_unknown",
    } <= set(reasons)
    rejection_text = Path(result["output_dir"], "rejections.csv").read_text(encoding="utf-8")
    assert "limit_price_unknown" in rejection_text
    assert "st_status_unknown" in rejection_text
    assert "suspended_status_unknown" in rejection_text
    assert "list_date_missing" in rejection_text
    assert "bj_status_unknown" in rejection_text


def test_safety_unknown_is_reported_even_when_basic_filter_also_fails(tmp_path):
    data_dir = _write_local_dataset(tmp_path, missing_safety=True, missing_mcap=True)

    result = _run_local(tmp_path, data_dir, "missing-safety-and-mcap")
    reasons = next(row["reasons"] for row in result["rejections"] if row["code"] == "300201")

    assert "float_mcap_yi_below_min" in reasons
    assert "limit_price_unknown" in reasons
    assert "st_status_unknown" in reasons
    assert "suspended_status_unknown" in reasons
    assert "list_date_missing" in reasons
    assert "bj_status_unknown" in reasons


def test_missing_theme_and_fund_fields_are_disclosed_without_crashing(tmp_path):
    data_dir = _write_local_dataset(tmp_path, include_selection=False)

    result = _run_local(tmp_path, data_dir, "missing-enhancements")
    quality = Path(result["output_dir"], "data_quality.md").read_text(encoding="utf-8")

    assert "theme_unavailable" in quality
    assert "capital_unavailable" in quality
    assert "vol_ratio_unavailable" in quality
    assert "range_position_unavailable" in quality
    assert "tail_pullback_unavailable" in quality


def test_missing_optional_fields_are_not_safety_gate_rejections(tmp_path):
    data_dir = _write_local_dataset(tmp_path, include_selection=False)

    result = _run_local(tmp_path, data_dir, "optional-not-safety")
    reasons = [
        reason
        for row in result["rejections"]
        if row["code"] == "300201"
        for reason in row["reasons"]
    ]

    assert "theme_missing" not in reasons
    assert "capital_outflow" not in reasons


def test_missing_market_snapshot_uses_benchmark_proxy(tmp_path):
    data_dir = _write_local_dataset(tmp_path, include_market=False)

    result = _run_local(tmp_path, data_dir, "market-proxy")

    assert result["trades"]
    assert result["data_quality"]["market_proxy_used_count"] >= 1
    assert result["trades"][0]["market_proxy_used"] is True


def test_missing_market_and_benchmark_creates_cash_day(tmp_path):
    data_dir = _write_local_dataset(tmp_path, include_market=False, include_benchmark=False)

    result = _run_local(tmp_path, data_dir, "market-unavailable")

    assert not result["trades"]
    assert any(row["reason"] == "market_data_unavailable" for row in result["skipped_days"])


def test_daily_proxy_report_contains_research_warning_and_quality_files(tmp_path):
    data_dir = _write_local_dataset(tmp_path, include_selection=False)

    result = _run_local(tmp_path, data_dir, "report")
    output_dir = Path(result["output_dir"])
    summary = (output_dir / "backtest_summary.md").read_text(encoding="utf-8")

    assert "Report Fidelity: DAILY_PROXY" in summary
    assert "本报告不等同于原策略完整历史验证。" in summary
    assert "题材、资金、尾盘字段缺失可能显著影响结果。" in summary
    assert "结果仅用于研究参考。" in summary
    assert "strict_historical 尚未实现。" in summary
    assert (output_dir / "rejections.csv").exists()
    assert (output_dir / "field_coverage.csv").exists()


def test_daily_proxy_data_quality_contains_dataset_and_coverage_audit(tmp_path):
    data_dir = _write_local_dataset(tmp_path, include_selection=False)

    result = _run_local(tmp_path, data_dir, "quality-audit")
    quality = Path(result["output_dir"], "data_quality.md").read_text(encoding="utf-8")

    assert f"data_dir: {data_dir}" in quality
    assert "trade_date_start: 2026-04-01" in quality
    assert "trade_date_end: 2026-04-02" in quality
    assert "candidate_count: 2" in quality
    assert "daily_bars.csv (csv)" in quality
    assert "theme_tags: 0/2 (0.0%)" in quality


def test_daily_proxy_quality_reports_effective_asof_not_source_tail_claim(tmp_path):
    data_dir = _write_local_dataset(tmp_path, manifest_asof="14:50")

    result = _run_local(tmp_path, data_dir, "source-asof")
    quality = Path(result["output_dir"], "data_quality.md").read_text(encoding="utf-8")

    assert "selection_as_of: daily_close_proxy" in quality
    assert "source_selection_as_of: 14:50" in quality


def test_daily_proxy_writes_only_backtest_outputs(tmp_path):
    data_dir = _write_local_dataset(tmp_path)

    result = _run_local(tmp_path, data_dir, "isolation")
    output_dir = Path(result["output_dir"])

    assert output_dir.parent == tmp_path / "backtest_outputs"
    assert not (tmp_path / "records").exists()
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "examples").exists()


def test_sample_exact_remains_deterministic_after_daily_proxy_extension(tmp_path):
    config = _config(tmp_path)

    first = run_backtest(dataset="sample", fidelity="sample_exact", config=config, run_id="sample-first")
    second = run_backtest(dataset="sample", fidelity="sample_exact", config=config, run_id="sample-second")

    assert first["trades"] == second["trades"]
    assert first["trades"][0]["selection_as_of"] == "14:50"


def test_no_automatic_trading_or_clicking_code_exists_phase32():
    root = Path(__file__).resolve().parents[1]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*.py")
        if "tests" not in path.parts
    )
    forbidden = ["pyautogui", "selenium", "broker api", "auto" + "_order", "place" + "_order"]
    assert not any(token in text.lower() for token in forbidden)


def _run_local(tmp_path, data_dir, run_id):
    return run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=str(data_dir),
        config=_config(tmp_path),
        run_id=run_id,
    )


def _config(tmp_path):
    config = load_config()
    config["backtest"]["output_dir"] = str(tmp_path / "backtest_outputs")
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    config["paths"]["examples_dir"] = str(tmp_path / "examples")
    return config


def _write_local_dataset(
    tmp_path,
    include_manifest=True,
    missing_safety=False,
    missing_mcap=False,
    include_selection=True,
    include_market=True,
    include_benchmark=True,
    manifest_asof="daily_close_proxy",
):
    data_dir = tmp_path / "local_data"
    data_dir.mkdir()
    safety = ["", "", "", "", "", ""] if missing_safety else ["11.00", "9.00", "false", "false", "2020-01-01", "false"]
    float_mcap = "" if missing_mcap else "100"
    rows = [
        ["2026-04-01", "300201", "Local Winner", "9.70", "10.10", "9.65", "10.00", "1200000", "300000000", "8.0", "4.2", float_mcap, *safety, "9.80", "9.60", "9.40", "false"],
        ["2026-04-02", "300201", "Local Winner", "10.10", "10.40", "9.90", "10.32", "1300000", "320000000", "1.0", "0.5", "100", "11.00", "9.00", "false", "false", "2020-01-01", "false", "9.95", "9.75", "9.55", "false"],
    ]
    _write_csv(
        data_dir / "daily_bars.csv",
        [
            "trade_date",
            "code",
            "name",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover_pct",
            "change_pct",
            "float_mcap_yi",
            "limit_up",
            "limit_down",
            "is_st",
            "is_suspended",
            "list_date",
            "is_bj_stock",
            "ma5",
            "ma10",
            "ma20",
            "is_limit_down",
        ],
        rows,
    )
    if include_selection:
        _write_csv(
            data_dir / "selection_snapshots.csv",
            [
                "trade_date",
                "code",
                "vol_ratio",
                "range_position",
                "tail_pullback_pct",
                "theme_tags",
                "theme_rank",
                "same_theme_strong_count",
                "main_net",
                "big_order_net",
                "source_quality",
                "next_day_high",
            ],
            [["2026-04-01", "300201", "1.6", "0.84", "0.2", "Robotics|AI", "1", "4", "3200", "1800", "historical_fixture", "999.0"]],
        )
    if include_market:
        _write_csv(
            data_dir / "market_snapshots.csv",
            ["trade_date", "market_gate", "index_change_pct", "market_reason"],
            [["2026-04-01", "PASS", "0.5", "index_positive"], ["2026-04-02", "PASS", "0.2", "index_positive"]],
        )
    if include_benchmark:
        _write_csv(
            data_dir / "benchmark_bars.csv",
            ["trade_date", "open", "high", "low", "close"],
            [["2026-04-01", "4000", "4030", "3990", "4020"], ["2026-04-02", "4020", "4050", "4010", "4040"]],
        )
    if include_manifest:
        (data_dir / "dataset_manifest.yaml").write_text(
            "dataset: local_daily_proxy_fixture\n"
            f"selection_as_of: {manifest_asof}\n"
            "benchmark: CSI 300\n"
            "data_fidelity: daily_proxy\n"
            "strict_historical_supported: false\n"
            "proxy_fields:\n"
            "  - daily_close_proxy\n",
            encoding="utf-8",
        )
    return data_dir


def _write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)
