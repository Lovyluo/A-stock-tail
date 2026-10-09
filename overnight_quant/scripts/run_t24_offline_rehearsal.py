from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from overnight_quant.data.t24_offline_rehearsal import run_offline_rehearsal, synthetic_fixture
from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-network synthetic integration rehearsal, not source evidence.")
    parser.add_argument("--synthetic-test-only", required=True, action="store_true")
    parser.add_argument("--output", type=Path, help="Immutable synthetic report, never qualification evidence.")
    args = parser.parse_args()
    result = run_offline_rehearsal(synthetic_fixture())
    if args.output:
        write_benchmark_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if args.synthetic_test_only and result["status"] == "OFFLINE_CHAIN_EXECUTABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
