from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.collector_stress import (
    run_provider_stress,
    summarize_stress_runs,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.real_point_in_time_collectors import (
    RealPointInTimeCollectors,
    RequestsTransport,
)


DEFAULT_CODES = [
    "000001", "000002", "000063", "000100", "000157",
    "000333", "000338", "000425", "000538", "000568",
    "000625", "000651", "000725", "000768", "000858",
    "000876", "000895", "000938", "000963", "001289",
    "002027", "002050", "002129", "002142", "002230",
    "002241", "002271", "002304", "002352", "002371",
    "002415", "002459", "002460", "002475", "002594",
    "002601", "002714", "002736", "002812", "002920",
    "600000", "600009", "600028", "600030", "600036",
    "600050", "600104", "600276", "600309", "600519",
]


def run_real_collector_stress(
    *,
    sizes: list[int],
    deadline_seconds: float,
) -> dict:
    results = []
    for size in sizes:
        codes = DEFAULT_CODES[: max(1, min(int(size), 50))]
        transport = RequestsTransport(
            timeout_seconds=min(8.0, deadline_seconds),
            max_attempts=2,
        )
        collectors = RealPointInTimeCollectors(
            codes,
            transport=transport,
        )
        result = run_provider_stress(
            collectors.provider_map(),
            expected_codes=codes,
            observed_at=datetime.now(CN_TZ),
            deadline_seconds=deadline_seconds,
            max_workers=4,
        )
        result["stock_count"] = len(codes)
        result["sla_claim"] = False
        result["sla_note"] = (
            "Only an in-window run can prove the 14:50 SLA."
        )
        results.append(result)
    return summarize_stress_runs(results)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark real providers for 1/10/30/50 stocks. "
            "No snapshots or strategy outputs are written."
        )
    )
    parser.add_argument("--sizes", default="1,10,30,50")
    parser.add_argument("--deadline-seconds", type=float, default=30.0)
    args = parser.parse_args()
    result = run_real_collector_stress(
        sizes=[
            int(item)
            for item in str(args.sizes).split(",")
            if item.strip()
        ],
        deadline_seconds=args.deadline_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
