from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.execution.position_tracker import read_order_rows
from overnight_quant.execution.state_manager import config_for_state
from overnight_quant.reports.trade_review import write_trade_review
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def generate_trade_review(
    code: str,
    trade_date: str | None = None,
    state: str = "real",
    config: dict | None = None,
) -> dict:
    runtime_config = config_for_state(config or load_config(), state)
    code = str(code).zfill(6)
    records_dir = runtime_config.get("paths", {}).get("records_dir", "overnight_quant/records")
    rows = [row for row in read_order_rows(records_dir) if str(row.get("code", "")).zfill(6) == code]
    if not rows:
        return {"error": "NO_TRADE_RECORD", "code": code, "state": state}
    return write_trade_review(runtime_config, code, trade_date)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate single-trade review report.")
    parser.add_argument("--code", required=True)
    parser.add_argument("--date", default=None)
    parser.add_argument("--state", choices=["real", "example"], default="real")
    args = parser.parse_args()

    result = generate_trade_review(args.code, args.date, args.state)
    print("[Trade Review]")
    print(f"Code: {str(args.code).zfill(6)}")
    print(f"State: {args.state}")
    if result.get("error"):
        print(f"Error: {result['error']} - no manual trade records found in {args.state} state.")
        return 2
    print(f"Conclusion: {result['conclusion']}")
    print(f"Gross PnL: {result['gross_pnl']}")
    print(f"Fee Estimate: {result['fee_estimate']}")
    print(f"Net PnL: {result['net_pnl']}")
    print(f"Return Pct: {result['return_pct']}%")
    print(f"Trade Review: {result['path']}")
    print("Risk Notice: manual review only; no automated orders; not investment advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
