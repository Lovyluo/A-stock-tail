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
from overnight_quant.data.snapshot_store import CloseWindowCollector, ImmutableSnapshotStore


def run_snapshot_collection(
    *,
    input_path: str | Path | None = None,
    trade_date: str | None = None,
    snapshot_root: str | Path = "overnight_quant/data/cache/close_confirmation_snapshots",
    freeze: bool = False,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now(CN_TZ)
    records = []
    source_status = []
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
        else:
            records = payload
        if not isinstance(records, list):
            records = []
    providers = {"input_file": lambda observed_at: list(records)} if records else {}
    collector = CloseWindowCollector(ImmutableSnapshotStore(snapshot_root), providers)
    if freeze:
        return collector.freeze(
            trade_date or current.date().isoformat(),
            records,
            source_status=source_status,
        )
    return collector.collect(current)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and freeze point-in-time close-confirmation snapshots.")
    parser.add_argument("--input", help="JSON file containing point-in-time records.")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--snapshot-root", default="overnight_quant/data/cache/close_confirmation_snapshots")
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()

    result = run_snapshot_collection(
        input_path=args.input,
        trade_date=args.trade_date,
        snapshot_root=args.snapshot_root,
        freeze=args.freeze,
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
