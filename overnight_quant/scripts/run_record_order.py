from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.execution.order_recorder import record_manual_order, record_position_update
from overnight_quant.execution.state_manager import config_for_state
from overnight_quant.strategy.yang_yongxing_overnight import load_config


def record_order_for_state(
    code: str,
    price: float,
    qty: int,
    side: str,
    trade_time: str,
    notes: str = "",
    state: str = "real",
    config: dict | None = None,
    position_update: bool = False,
    name: str = "",
    stop_loss_price: float | str = "",
) -> dict:
    runtime_config = config_for_state(config or load_config(), state)
    if position_update:
        return record_position_update(
            runtime_config,
            code=code,
            name=name,
            price=price,
            qty=qty,
            side=side,
            trade_time=trade_time,
            notes=notes,
            stop_loss_price=stop_loss_price,
        )
    return record_manual_order(runtime_config, code=code, price=price, qty=qty, side=side, trade_time=trade_time, notes=notes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a manually executed order or a manual position update.")
    parser.add_argument("--code", required=False)
    parser.add_argument("--name", default="")
    parser.add_argument("--price", type=float, required=False)
    parser.add_argument("--qty", type=int, required=False)
    parser.add_argument("--side", choices=["BUY", "SELL"], required=False)
    parser.add_argument("--trade-time", default=None)
    parser.add_argument("--notes", default="")
    parser.add_argument("--stop-loss", default="")
    parser.add_argument("--position-update", action="store_true")
    parser.add_argument("--state", choices=["real", "example"], default="real")
    args = parser.parse_args()

    code = args.code or input("Code: ").strip()
    price = args.price if args.price is not None else float(input("Price: ").strip())
    qty = args.qty if args.qty is not None else int(input("Qty: ").strip())
    side = args.side or input("Side BUY/SELL: ").strip().upper()
    trade_time = args.trade_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    result = record_order_for_state(
        code=code,
        price=price,
        qty=qty,
        side=side,
        trade_time=trade_time,
        notes=args.notes,
        state=args.state,
        position_update=args.position_update,
        name=args.name,
        stop_loss_price=args.stop_loss,
    )
    print("[Manual Position Update]" if args.position_update else "[Manual Order Record]")
    print(f"State: {args.state}")
    print(f"Status: {'RECORDED' if result['allow'] else 'REJECTED'}")
    if result["reasons"]:
        print("Reasons:")
        for reason in result["reasons"]:
            print(f"- {reason}")
    if result.get("orders_csv"):
        print(f"Manual Orders CSV: {result['orders_csv']}")
    if result.get("report_path"):
        print(f"Record Report: {result['report_path']}")
    if result.get("lifecycle_report_path"):
        print(f"Trade Lifecycle Report: {result['lifecycle_report_path']}")
    print("Risk Notice: manual record only; no automated orders; not investment advice.")
    return 0 if result["allow"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
