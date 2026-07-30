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
from overnight_quant.data.real_point_in_time_collectors import (
    RealPointInTimeCollectors,
)


def run_real_source_validation(
    codes: list[str],
    *,
    now: datetime | None = None,
    collectors: RealPointInTimeCollectors | None = None,
) -> dict:
    current = now or datetime.now(CN_TZ)
    runtime_collectors = collectors or RealPointInTimeCollectors(codes)
    return runtime_collectors.validate_sources(current)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate real point-in-time sources without writing snapshots "
            "or creating strategy outputs."
        )
    )
    parser.add_argument(
        "--codes",
        default="000001",
        help="Comma-separated A-share stock codes used for source probes.",
    )
    args = parser.parse_args()
    result = run_real_source_validation(
        [
            item.strip()
            for item in str(args.codes).split(",")
            if item.strip()
        ]
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("execution_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
