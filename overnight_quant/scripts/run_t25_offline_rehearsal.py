from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic
from overnight_quant.data.t25_offline_rehearsal import run_local_chain
from overnight_quant.data.t25_native_source_research import native_research_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Local subprocess -> test-only Provider/Adapter/PIT/features; no network")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--synthetic-test-only", action="store_true")
    modes.add_argument("--research-plan-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        raise FileExistsError(str(args.output))
    if args.research_plan_only:
        result = {**native_research_plan(), "execution_ok": True}
    else:
        with tempfile.TemporaryDirectory(prefix="t25-local-") as directory:
            result = run_local_chain(lock_path=Path(directory) / "executor.lock")
    if args.output:
        write_benchmark_json_atomic(args.output, result)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["execution_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
