import csv
from datetime import datetime
from pathlib import Path

from overnight_quant.backtest.data_preparation import PreparationRequest, prepare_dataset
from overnight_quant.scripts.prepare_backtest_data import run_prepare
from overnight_quant.scripts.run_backtest import run_backtest
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def test_prepare_dry_run_writes_no_outputs(tmp_path):
    config = _config(tmp_path)
    _write_raw_inputs(Path(config["backtest"]["raw_data_dir"]))

    result = run_prepare(
        source="local-raw",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        raw_dir=config["backtest"]["raw_data_dir"],
        dry_run=True,
        config=config,
    )

    assert result["status"] == "DRY_RUN"
    assert not Path(config["backtest"]["local_data_dir"]).exists()
    assert not Path(config["backtest"]["manifest_dir"]).exists()


def test_prepare_requires_codes_and_writes_failure_report(tmp_path):
    config = _config(tmp_path)

    result = run_prepare(
        source="sample",
        codes=[],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        config=config,
    )

    assert result["error"] == "CODES_REQUIRED"
    report = Path(result["audit_files"]["prepare_report"])
    assert report.exists()
    assert "CODES_REQUIRED" in report.read_text(encoding="utf-8")
    assert not Path(config["backtest"]["local_data_dir"]).exists()


def test_prepare_requires_date_range(tmp_path):
    config = _config(tmp_path)

    result = run_prepare(
        source="sample",
        codes=["300201"],
        start="",
        end="",
        out_dir=config["backtest"]["local_data_dir"],
        config=config,
    )

    assert result["error"] == "DATE_RANGE_REQUIRED"


def test_prepare_does_not_overwrite_existing_processed_output(tmp_path):
    config = _config(tmp_path)
    out_dir = Path(config["backtest"]["local_data_dir"])
    out_dir.mkdir(parents=True)
    sentinel = out_dir / "daily_bars.csv"
    sentinel.write_text("sentinel\n", encoding="utf-8")

    result = run_prepare(
        source="sample",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(out_dir),
        overwrite=False,
        config=config,
    )

    assert result["error"] == "DATA_DIR_EXISTS_WITHOUT_OVERWRITE"
    assert sentinel.read_text(encoding="utf-8") == "sentinel\n"


def test_sample_source_writes_processed_dataset_loadable_by_daily_proxy(tmp_path):
    config = _config(tmp_path)

    result = run_prepare(
        source="sample",
        sample_profile="neutral",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        overwrite=True,
        config=config,
    )

    assert result["status"] == "PREPARE_COMPLETED"
    for filename in _processed_names():
        assert (Path(result["out_dir"]) / filename).exists()
    neutral_selections = _read_csv(Path(result["out_dir"], "selection_snapshots.csv"))
    assert all(row["theme_tags"] == "" and row["main_net"] == "" for row in neutral_selections)
    assert "sample_profile: neutral" in Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")

    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="prepared-sample",
        config=config,
    )
    summary = Path(backtest["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    assert "error" not in backtest
    assert "Report Fidelity: DAILY_PROXY" in summary
    assert backtest["data_quality"]["market_proxy_used_count"] >= 1
    assert backtest["metrics"]["trade_count"] == 0
    assert Path(backtest["output_dir"]).parent == Path(config["backtest"]["output_dir"])


def test_positive_sample_profile_produces_trade_and_fixture_disclosure(tmp_path):
    config = _config(tmp_path)
    config["backtest"]["preparation_positive_sample_dir"] = str(
        Path(__file__).resolve().parents[1] / "examples" / "historical_prepare_positive_raw"
    )

    result = run_prepare(
        source="sample",
        sample_profile="positive",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        overwrite=True,
        config=config,
    )

    assert result["status"] == "PREPARE_COMPLETED"
    selections = _read_csv(Path(result["out_dir"], "selection_snapshots.csv"))
    fixture_rows = [row for row in selections if row["theme_source"] == "sample_fixture"]
    assert fixture_rows
    assert all(row["capital_source"] == "sample_fixture" for row in fixture_rows)
    assert all(row["tail_pullback_pct"] == "" for row in selections)

    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    for text in (
        "sample_profile: positive",
        "positive profile uses deterministic sample_fixture theme/capital fields for pipeline validation",
        "not live-filled",
        "not strict historical",
        "not evidence of strategy profitability",
        "DAILY_PROXY only",
    ):
        assert text in manifest
        assert text in report
    assert "simulated_or_fixture_fields:" in manifest
    assert "theme_tags" in manifest
    assert "selection_snapshots,theme_tags,1,7,14.29,sample_fixture,sample_fixture" in coverage
    assert "selection_snapshots,main_net,1,7,14.29,sample_fixture,sample_fixture" in coverage

    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="prepared-positive",
        config=config,
    )
    assert backtest["metrics"]["trade_count"] >= 1
    trade = _read_csv(Path(backtest["output_dir"], "trades.csv"))[0]
    for field in (
        "buy_commission",
        "sell_commission",
        "stamp_tax",
        "net_pnl",
        "return_pct",
    ):
        assert trade[field] != ""
    assert trade["selection_as_of"] == "daily_close_proxy"
    assert trade["data_fidelity"] == "daily_proxy"

    summary = Path(backtest["output_dir"], "backtest_summary.md").read_text(encoding="utf-8")
    quality = Path(backtest["output_dir"], "data_quality.md").read_text(encoding="utf-8")
    for text in (
        "Report Fidelity: DAILY_PROXY",
        "positive profile uses deterministic sample_fixture theme/capital fields for pipeline validation",
        "not live-filled",
        "not strict historical",
        "not evidence of strategy profitability",
        "DAILY_PROXY only",
    ):
        assert text in summary
        assert text in quality
    assert "simulated_or_fixture_fields: theme_tags, theme_rank, main_net, big_order_net" in quality


def test_local_raw_source_writes_daily_proxies_and_unavailable_disclosures(tmp_path):
    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_inputs(raw_dir)

    result = run_prepare(
        source="local-raw",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        raw_dir=str(raw_dir),
        overwrite=True,
        config=config,
    )

    assert result["status"] == "PREPARE_COMPLETED"
    selections = _read_csv(Path(result["out_dir"], "selection_snapshots.csv"))
    candidate = next(row for row in selections if row["trade_date"] == "2025-01-09")
    assert candidate["range_position"] == "0.75"
    assert candidate["vol_ratio"] == "1.8"
    assert candidate["tail_pullback_pct"] == ""
    assert candidate["theme_tags"] == ""
    daily = _read_csv(Path(result["out_dir"], "daily_bars.csv"))
    candidate_bar = next(row for row in daily if row["trade_date"] == "2025-01-09")
    assert candidate_bar["change_pct"] == "3.0303"
    assert candidate_bar["ma5"] == "9.84"
    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    assert "fidelity: daily_proxy" in manifest
    assert "volume_ratio_proxy" in manifest
    assert "daily_range_position_proxy" in manifest
    assert "tail_pullback_pct" in manifest
    assert "theme_tags" in manifest
    assert "main_net" in manifest
    assert "DAILY_PROXY_ONLY" in manifest
    assert "NOT_STRICT_HISTORICAL" in manifest


def test_local_raw_missing_safety_values_are_not_invented(tmp_path):
    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_inputs(raw_dir, omit_safety=True)

    result = run_prepare(
        source="local-raw",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        raw_dir=str(raw_dir),
        overwrite=True,
        config=config,
    )
    backtest = run_backtest(
        dataset="local",
        fidelity="daily_proxy",
        data_dir=result["out_dir"],
        run_id="missing-safety",
        config=config,
    )

    processed = _read_csv(Path(result["out_dir"], "daily_bars.csv"))
    assert processed[-1]["limit_up"] == ""
    assert processed[-1]["is_st"] == ""
    rejection_text = Path(backtest["output_dir"], "rejections.csv").read_text(encoding="utf-8")
    assert "limit_price_unknown" in rejection_text
    assert "st_status_unknown" in rejection_text
    assert "suspended_status_unknown" in rejection_text
    assert "list_date_missing" in rejection_text


def test_prepare_audit_files_include_coverage_and_recoverable_source_error(tmp_path):
    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_inputs(raw_dir)
    with (raw_dir / "daily_bars.csv").open("a", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(
            ["2025-01-09", "600999", "Bad Row", "", "10", "9", "9.5", "100", "200000000", "7", "100", "", "", "", "", "", ""]
        )

    result = run_prepare(
        source="local-raw",
        codes=["300201", "600999"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        raw_dir=str(raw_dir),
        overwrite=True,
        config=config,
    )

    assert result["status"] == "PARTIAL_DATA_PREPARED"
    report = Path(result["audit_files"]["prepare_report"]).read_text(encoding="utf-8")
    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    errors = Path(result["audit_files"]["source_errors"]).read_text(encoding="utf-8")
    assert "not suitable for strict_historical" in report
    assert "selection_snapshots,tail_pullback_pct,0" in coverage
    assert "local-raw,600999,2025-01-09,RAW_ROW_INVALID" in errors


def test_known_safety_coverage_is_not_labeled_unknown(tmp_path):
    config = _config(tmp_path)

    result = run_prepare(
        source="sample",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        overwrite=True,
        config=config,
    )

    coverage = Path(result["audit_files"]["field_coverage"]).read_text(encoding="utf-8")
    assert "daily_bars,limit_up,7,7,100.0,safety_source,raw_source_only" in coverage
    assert "daily_bars,limit_up,7,7,100.0,safety_unknown" not in coverage


def test_manifest_serializes_empty_unavailable_lists_as_lists(tmp_path):
    config = _config(tmp_path)

    result = run_prepare(
        source="sample",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        overwrite=True,
        config=config,
    )

    manifest = Path(result["out_dir"], "dataset_manifest.yaml").read_text(encoding="utf-8")
    assert "    unavailable_fields: []" in manifest


def test_two_preparations_in_same_second_keep_separate_audit_reports(tmp_path):
    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_inputs(raw_dir)
    fixed_now = datetime(2026, 5, 27, 14, 20, 0)
    first = prepare_dataset(
        PreparationRequest(
            source="local-raw",
            codes=["300201"],
            start="2025-01-01",
            end="2025-01-31",
            out_dir=tmp_path / "processed_first",
            raw_dir=raw_dir,
            manifest_dir=Path(config["backtest"]["manifest_dir"]),
            overwrite=True,
        ),
        now=fixed_now,
    )
    second = prepare_dataset(
        PreparationRequest(
            source="local-raw",
            codes=["300201"],
            start="2025-01-01",
            end="2025-01-31",
            out_dir=tmp_path / "processed_second",
            raw_dir=raw_dir,
            manifest_dir=Path(config["backtest"]["manifest_dir"]),
            overwrite=True,
        ),
        now=fixed_now,
    )

    assert first.audit_files["prepare_report"] != second.audit_files["prepare_report"]
    assert Path(first.audit_files["prepare_report"]).exists()
    assert Path(second.audit_files["prepare_report"]).exists()


def test_prepare_ignores_next_day_raw_columns_and_does_not_touch_trade_state(tmp_path):
    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_inputs(raw_dir, include_future_column=True)

    result = run_prepare(
        source="local-raw",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        raw_dir=str(raw_dir),
        overwrite=True,
        config=config,
    )

    selections = Path(result["out_dir"], "selection_snapshots.csv").read_text(encoding="utf-8")
    assert "next_day_theme" not in selections
    assert "FutureValue" not in selections
    assert not Path(config["paths"]["records_dir"]).exists()
    assert not Path(config["paths"]["reports_dir"]).exists()
    assert not Path(config["paths"]["examples_dir"]).exists()


def test_a_stock_data_source_rejects_real_execution_without_injected_client(tmp_path):
    result = run_prepare(
        source="a-stock-data",
        codes=["300201"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=str(tmp_path / "processed"),
        config=_config(tmp_path),
    )

    assert result["error"] == "REAL_NETWORK_NOT_ENABLED"


def test_codes_file_drives_local_raw_preparation(tmp_path):
    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_inputs(raw_dir)
    codes_file = raw_dir / "codes.txt"
    codes_file.write_text("# bounded input\n300201\n", encoding="utf-8")

    result = run_prepare(
        source="local-raw",
        codes_file=str(codes_file),
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        raw_dir=str(raw_dir),
        overwrite=True,
        config=config,
    )

    assert result["status"] == "PREPARE_COMPLETED"
    assert result["capped_codes"] == ["300201"]


def test_max_codes_caps_processed_universe(tmp_path):
    config = _config(tmp_path)
    raw_dir = Path(config["backtest"]["raw_data_dir"])
    _write_raw_inputs(raw_dir)

    result = run_prepare(
        source="local-raw",
        codes=["300201", "600999"],
        start="2025-01-01",
        end="2025-01-31",
        out_dir=config["backtest"]["local_data_dir"],
        raw_dir=str(raw_dir),
        max_codes=1,
        overwrite=True,
        config=config,
    )

    assert result["status"] == "PREPARE_COMPLETED"
    assert result["requested_code_count"] == 2
    assert result["capped_codes"] == ["300201"]


def test_preparation_code_contains_no_live_or_automatic_trading_path():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "backtest" / "data_preparation.py",
        root / "backtest" / "preparation_sources.py",
        root / "scripts" / "prepare_backtest_data.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8", errors="ignore").lower() for path in paths)
    forbidden = [
        "import requests",
        "from requests",
        "urllib.request",
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
    config["backtest"]["raw_data_dir"] = str(tmp_path / "raw")
    config["backtest"]["local_data_dir"] = str(tmp_path / "processed")
    config["backtest"]["manifest_dir"] = str(tmp_path / "manifests")
    config["backtest"]["preparation_sample_dir"] = str(
        Path(__file__).resolve().parents[1] / "examples" / "historical_prepare_raw"
    )
    config["backtest"]["output_dir"] = str(tmp_path / "backtest_outputs")
    config["paths"]["records_dir"] = str(tmp_path / "records")
    config["paths"]["reports_dir"] = str(tmp_path / "reports")
    config["paths"]["examples_dir"] = str(tmp_path / "examples")
    return config


def _processed_names() -> tuple[str, ...]:
    return (
        "daily_bars.csv",
        "selection_snapshots.csv",
        "market_snapshots.csv",
        "benchmark_bars.csv",
        "dataset_manifest.yaml",
    )


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_raw_inputs(raw_dir: Path, omit_safety: bool = False, include_future_column: bool = False) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    safety = ["", "", "", "", ""] if omit_safety else ["11.22", "9.18", "false", "false", "2020-01-01"]
    fields = [
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
        "float_mcap_yi",
        "limit_up",
        "limit_down",
        "is_st",
        "is_suspended",
        "list_date",
        "is_limit_down",
    ]
    if include_future_column:
        fields.append("next_day_theme")
    rows = []
    closes = ["9.50", "9.60", "9.70", "9.80", "9.90", "10.20", "10.50"]
    dates = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
    for index, (trade_date, close) in enumerate(zip(dates, closes)):
        safety_values = safety if index >= 5 else (["", "", "", "", ""] if omit_safety else ["10.89", "8.91", "false", "false", "2020-01-01"])
        high, low, volume = ("10.30", "9.90", "180") if trade_date == "2025-01-09" else (str(round(float(close) + 0.1, 2)), str(round(float(close) - 0.1, 2)), "100")
        row = [
            trade_date,
            "300201",
            "Prepared Stock",
            str(round(float(close) - 0.1, 2)),
            high,
            low,
            close,
            volume,
            "300000000",
            "8",
            "100",
            *safety_values,
            "false",
        ]
        if include_future_column:
            row.append("FutureValue")
        rows.append(row)
    _write_csv(raw_dir / "daily_bars.csv", fields, rows)
    _write_csv(
        raw_dir / "benchmark_bars.csv",
        ["trade_date", "open", "high", "low", "close"],
        [[trade_date, "4000", "4040", "3990", "4020"] for trade_date in dates],
    )


def _write_csv(path: Path, fields: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)
