from __future__ import annotations

from pathlib import Path

from overnight_quant.scripts.run_chip_volume_calibration import (
    CH_CATEGORY,
    CH_CONFIDENCE_DELTA,
    CH_PEAK_PROXY,
    render_summary_markdown,
    run_calibration,
    summarize_chip_volume,
    write_summary,
)


def test_empty_input_safely_summarizes_zero_rows():
    summary = summarize_chip_volume([], {"date": "2026-07-09", "source_type": "none"})

    assert summary["total_rows"] == 0
    assert all(count == 0 for count in summary["peak_counts"].values())
    assert summary["confidence_delta"]["average"] == 0.0
    assert summary["confidence_delta"]["distribution"] == {}


def test_peak_counts_by_type():
    rows = [
        {CH_CATEGORY: "A", CH_PEAK_PROXY: "\u5efa\u4ed3\u5cf0", CH_CONFIDENCE_DELTA: "6"},
        {CH_CATEGORY: "B", CH_PEAK_PROXY: "\u62c9\u5347\u5cf0", CH_CONFIDENCE_DELTA: "4"},
        {CH_CATEGORY: "C", CH_PEAK_PROXY: "\u51fa\u8d27\u5cf0", CH_CONFIDENCE_DELTA: "-8"},
        {CH_CATEGORY: "C", CH_PEAK_PROXY: "\u4e2d\u6027", CH_CONFIDENCE_DELTA: "0"},
    ]

    summary = summarize_chip_volume(rows, {"date": "2026-07-09"})

    assert summary["peak_counts"]["accumulation"] == 1
    assert summary["peak_counts"]["markup"] == 1
    assert summary["peak_counts"]["distribution"] == 1
    assert summary["peak_counts"]["neutral"] == 1
    assert summary["category_peak_counts"]["C"]["distribution"] == 1
    assert summary["category_peak_counts"]["C"]["neutral"] == 1


def test_confidence_delta_average_and_distribution():
    rows = [
        {"category": "A", "chip_peak_type": "accumulation", "confidence_delta": "6"},
        {"category": "B", "chip_peak_type": "washout", "confidence_delta": "2"},
        {"category": "C", "chip_peak_type": "distribution", "confidence_delta": "-8"},
    ]

    summary = summarize_chip_volume(rows, {"date": "2026-07-09"})

    assert summary["confidence_delta"]["average"] == 0.0
    assert summary["confidence_delta"]["min"] == -8
    assert summary["confidence_delta"]["max"] == 6
    assert summary["confidence_delta"]["distribution"] == {-8: 1, 2: 1, 6: 1}


def test_summary_output_avoids_deterministic_terms(tmp_path):
    summary = summarize_chip_volume(
        [{"category": "A", "chip_peak_type": "markup", "confidence_delta": "6"}],
        {"date": "2026-07-09", "source_type": "unit_test"},
    )

    text = render_summary_markdown(summary)
    assert "\u5fc5\u6da8" not in text
    assert "\u4e70\u5165" not in text
    assert "\u7a33\u8d5a" not in text

    path = write_summary(summary, root=tmp_path)
    assert path.exists()
    assert path.name == "chip_volume_summary_2026-07-09.md"


def test_calibration_reads_observation_report_not_personal_trade_records(tmp_path):
    package = tmp_path / "overnight_quant"
    reports = package / "examples" / "reports"
    records = package / "records"
    reports.mkdir(parents=True)
    records.mkdir(parents=True)
    (records / "manual_orders.csv").write_text("SECRET_PERSONAL_TRADE\n", encoding="utf-8")

    section = "\u7b79\u7801\u4e0e\u91cf\u4ef7\u786e\u8ba4"
    report = (
        "# After Close\n\n"
        "date: 2026-07-09\n\n"
        f"## 6. {section}\n\n"
        f"| \u4ee3\u7801 | \u540d\u79f0 | {CH_CATEGORY} | {CH_PEAK_PROXY} | "
        f"20\u65e5\u6210\u672c proxy | \u504f\u79bb\u6210\u672c% | \u91cf\u80fd\u4fe1\u53f7 | "
        f"{CH_CONFIDENCE_DELTA} | \u8bf4\u660e |\n"
        "|---|---|---|---|---:|---:|---|---:|---|\n"
        "| 600001 | sample | A | \u5efa\u4ed3\u5cf0 | 10.1 | 2.0 | prev_day_high_volume | 6 | proxy |\n"
    )
    (reports / "after_close_analysis_2026-07-09.md").write_text(report, encoding="utf-8")

    result = run_calibration(mode="demo", root=tmp_path)
    text = Path(result["output_path"]).read_text(encoding="utf-8")

    assert result["total_rows"] == 1
    assert result["peak_counts"]["accumulation"] == 1
    assert "SECRET_PERSONAL_TRADE" not in text
