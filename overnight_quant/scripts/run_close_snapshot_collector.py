from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.close_time_contract import (
    build_close_time_contract,
)
from overnight_quant.data.real_point_in_time_collectors import (
    RealPointInTimeCollectors,
)
from overnight_quant.data.snapshot_store import (
    CloseWindowCollector,
    ImmutableSnapshotStore,
)


def run_snapshot_collection(
    *,
    input_path: str | Path | None = None,
    trade_date: str | None = None,
    snapshot_root: str | Path = "overnight_quant/data/cache/close_confirmation_snapshots",
    freeze: bool = False,
    now: datetime | None = None,
    live: bool = False,
    codes: list[str] | None = None,
    collectors: RealPointInTimeCollectors | None = None,
    minute_label_semantics: str = "unverified",
    minute_label_verified: bool = False,
) -> dict:
    current = now or datetime.now(CN_TZ)
    records = []
    source_status = []
    time_contract = None
    if input_path:
        try:
            payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "NO_VALID_RECORDS",
                "execution_ok": False,
                "data_ready": False,
                "records": [],
                "input_error": f"{type(exc).__name__}: {exc}",
            }
        if isinstance(payload, dict):
            records = payload.get("records") or []
            source_status = list(payload.get("source_status") or [])
            time_contract = payload.get("time_contract")
        else:
            records = payload
        if not isinstance(records, list):
            records = []
    if live and input_path:
        return {
            "status": "COLLECTOR_INPUT_CONFLICT",
            "execution_ok": False,
            "data_ready": False,
            "records": [],
        }
    if live:
        runtime_collectors = collectors or RealPointInTimeCollectors(
            codes or [],
            minute_label_semantics=minute_label_semantics,
            minute_label_verified=minute_label_verified,
        )
        providers = runtime_collectors.provider_map()
        time_contract = (
            getattr(runtime_collectors, "time_contract", None)
            or build_close_time_contract(
                current.date(),
                minute_label_semantics=(
                    getattr(
                        runtime_collectors,
                        "minute_label_semantics",
                        minute_label_semantics,
                    )
                ),
                verified=bool(
                    getattr(
                        runtime_collectors,
                        "minute_label_verified",
                        minute_label_verified,
                    )
                ),
            )
        )
    else:
        providers = (
            {"input_file": lambda observed_at: list(records)}
            if records
            else {}
        )
    collector = CloseWindowCollector(
        ImmutableSnapshotStore(snapshot_root),
        providers,
        time_contract=time_contract,
    )
    if freeze:
        if live:
            return {
                "status": "LIVE_FREEZE_REQUIRES_COLLECTED_INPUT",
                "execution_ok": False,
                "data_ready": False,
                "records": [],
                "candidates": [],
                "tickets": [],
                "orders": [],
            }
        return collector.freeze(
            trade_date or current.date().isoformat(),
            records,
            source_status=source_status,
            time_contract=time_contract,
        )
    return collector.collect(current)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and freeze point-in-time close-confirmation snapshots.")
    parser.add_argument("--input", help="JSON file containing point-in-time records.")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--snapshot-root", default="overnight_quant/data/cache/close_confirmation_snapshots")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use real providers. No demo fallback is available.",
    )
    parser.add_argument(
        "--minute-label-semantics",
        choices=["unverified", "minute_start", "minute_end"],
        default="unverified",
    )
    parser.add_argument(
        "--minute-label-verified",
        action="store_true",
        help=(
            "Mark a minute label result as verified only after the "
            "four-point real-trading-day probe has been reviewed."
        ),
    )
    parser.add_argument(
        "--codes",
        default="",
        help="Comma-separated stock codes for real provider collection.",
    )
    args = parser.parse_args()

    result = run_snapshot_collection(
        input_path=args.input,
        trade_date=args.trade_date,
        snapshot_root=args.snapshot_root,
        freeze=args.freeze,
        live=args.live,
        codes=[
            item.strip()
            for item in str(args.codes).split(",")
            if item.strip()
        ],
        minute_label_semantics=args.minute_label_semantics,
        minute_label_verified=args.minute_label_verified,
    )
    print(f"Status: {result['status']}")
    print(f"execution_ok: {str(bool(result.get('execution_ok'))).lower()}")
    print(f"data_ready: {str(bool(result.get('data_ready'))).lower()}")
    print(f"coverage_by_type: {json.dumps(result.get('coverage_by_type') or {}, ensure_ascii=False, sort_keys=True)}")
    print(f"readiness_errors: {json.dumps(result.get('readiness_errors') or [], ensure_ascii=False)}")
    print(f"critical_source_status: {json.dumps(result.get('critical_source_status') or {}, ensure_ascii=False, sort_keys=True)}")
    if result.get("path"):
        print(f"Snapshot: {result['path']}")
    return 0 if result.get("execution_ok") and result.get("data_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
