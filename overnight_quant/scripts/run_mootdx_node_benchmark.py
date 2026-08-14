from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.mootdx_node_benchmark import (
    run_mootdx_node_benchmark,
    write_benchmark_json_atomic,
)


DEFAULT_CODES = "000001,000333,600000,600519,601318"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit mootdx endpoint latency and offset equivalence. "
            "This command never changes configuration or creates trades."
        )
    )
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--codes", default=DEFAULT_CODES)
    parser.add_argument("--top-count", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--deadline-ms", type=int, default=2000)
    parser.add_argument("--recommendation-max-ms", type=int, default=1500)
    parser.add_argument("--skip-offset-comparison", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    result = run_mootdx_node_benchmark(
        _codes(args.codes),
        trade_date=args.date,
        top_count=args.top_count,
        qualification_rounds=args.rounds,
        deadline_ms=args.deadline_ms,
        recommendation_max_ms=args.recommendation_max_ms,
        compare_offsets=not args.skip_offset_comparison,
    )
    output = Path(args.output) if args.output else (
        ROOT
        / "overnight_quant"
        / "data"
        / "cache"
        / f"mootdx_node_benchmark_{args.date}.json"
    )
    write_benchmark_json_atomic(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _codes(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
