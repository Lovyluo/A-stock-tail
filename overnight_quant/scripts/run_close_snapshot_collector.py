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


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and freeze point-in-time close-confirmation snapshots.")
    parser.add_argument("--input", help="JSON file containing point-in-time records.")
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--snapshot-root", default="overnight_quant/data/cache/close_confirmation_snapshots")
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    records = json.loads(Path(args.input).read_text(encoding="utf-8")) if args.input else []
    if isinstance(records, dict):
        records = records.get("records") or []
    providers = {"input_file": lambda observed_at: list(records)} if records else {}
    collector = CloseWindowCollector(ImmutableSnapshotStore(args.snapshot_root), providers)
    if args.freeze:
        result = collector.freeze(args.trade_date or now.date().isoformat(), records)
    else:
        result = collector.collect(now)
    print(f"Status: {result['status']}")
    if result.get("path"):
        print(f"Snapshot: {result['path']}")
    return 0 if result["status"] in {"COLLECTED", "FROZEN_1450"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
