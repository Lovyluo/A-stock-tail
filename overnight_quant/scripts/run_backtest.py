from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.backtest.backtest_engine import BacktestEngine
from overnight_quant.backtest.backtest_metrics import calculate_metrics
from overnight_quant.backtest.backtest_report import write_backtest_outputs
from overnight_quant.backtest.historical_data import HistoricalDataError, LocalCsvHistoricalDataProvider, SampleHistoricalDataProvider
from overnight_quant.strategy.yang_yongxing_overnight import load_config


SUPPORTED_DATASET = "sample"
SUPPORTED_FIDELITY = "sample_exact"


def run_backtest(
    dataset: str = SUPPORTED_DATASET,
    fidelity: str = SUPPORTED_FIDELITY,
    intraday_assumption: str = "conservative",
    run_id: str | None = None,
    config: dict | None = None,
    data_dir: str | None = None,
) -> dict:
    if (dataset, fidelity) not in {("sample", "sample_exact"), ("local", "daily_proxy")}:
        return {
            "error": "NOT_IMPLEMENTED_IN_PHASE_3_1",
            "dataset": dataset,
            "fidelity": fidelity,
        }

    config = config or load_config()
    backtest_config = config.get("backtest", {})
    output_root = _resolve_configured_path(
        backtest_config.get("output_dir", "overnight_quant/backtest_outputs")
    )
    resolved_run_id = run_id or datetime.now().strftime(f"%Y%m%d_%H%M%S_%f_{fidelity}")
    try:
        if (dataset, fidelity) == ("sample", "sample_exact"):
            sample_dir = _resolve_configured_path(
                backtest_config.get("sample_data_dir", "overnight_quant/examples/historical")
            )
            provider = SampleHistoricalDataProvider(sample_dir)
        else:
            local_dir = _resolve_configured_path(
                data_dir or backtest_config.get("local_data_dir", "overnight_quant/backtest_data/processed")
            )
            provider = LocalCsvHistoricalDataProvider(local_dir)
    except HistoricalDataError as exc:
        return {"error": exc.code, "detail": exc.detail, "dataset": dataset, "fidelity": fidelity}
    result = BacktestEngine(provider, config, fidelity, intraday_assumption).run()
    metrics = calculate_metrics(result)
    output_dir = output_root / resolved_run_id
    result["metrics"] = metrics
    result["output_dir"] = str(output_dir)
    result["output_files"] = write_backtest_outputs(result, metrics, output_dir)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline sample-exact overnight backtest.")
    parser.add_argument("--dataset", default=SUPPORTED_DATASET)
    parser.add_argument("--fidelity", default=SUPPORTED_FIDELITY)
    parser.add_argument(
        "--intraday-assumption",
        default="conservative",
        choices=["conservative", "optimistic", "close_based"],
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()

    result = run_backtest(
        dataset=args.dataset,
        fidelity=args.fidelity,
        intraday_assumption=args.intraday_assumption,
        run_id=args.run_id,
        data_dir=args.data_dir,
    )
    if result.get("error"):
        if result["error"] == "NOT_IMPLEMENTED_IN_PHASE_3_1":
            print(
                "NOT_IMPLEMENTED_IN_PHASE_3_1: supported combinations are "
                "--dataset sample --fidelity sample_exact and "
                "--dataset local --fidelity daily_proxy."
            )
        else:
            print(f"{result['error']}: {result.get('detail', '')}".rstrip())
        return 2

    metrics = result["metrics"]
    if args.fidelity == "daily_proxy":
        print("[Daily Proxy Backtest]")
        print("Report Fidelity: DAILY_PROXY")
        print("Scope: daily-bar proxy research only; not a complete historical strategy validation.")
    else:
        print("[Sample Exact Backtest]")
        print("Data Fidelity: sample_exact")
        print("Scope: engine and event-order validation only; not evidence of strategy profitability.")
    print(f"Trades: {metrics['trade_count']}")
    print(f"Total Return: {metrics['total_return_pct']}%")
    print(f"Max Drawdown: {metrics['max_drawdown_pct']}%")
    print(f"Output Directory: {result['output_dir']}")
    return 0


def _resolve_configured_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


if __name__ == "__main__":
    raise SystemExit(main())
