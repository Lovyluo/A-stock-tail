"""Read-only source replay; intentionally has no network or collection mode."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.completed_daily_observation import observe, read_anchored_json
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-evidence", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--historical-offline", action="store_true")
    parser.add_argument("--synthetic-test-only", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if bool(args.manifest) != bool(args.manifest_sha256):
        parser.error("manifest and external SHA must be supplied together")
    if not args.manifest and not args.historical_offline:
        parser.error("frozen manifest required outside explicit historical replay")
    if Path(args.output).exists():
        parser.error("output already exists; no overwrite")
    try:
        evidence = read_anchored_json(args.source_evidence, args.source_sha256)
        manifest = read_anchored_json(args.manifest, args.manifest_sha256) if args.manifest else None
        result = observe(evidence, source_file_sha256=args.source_sha256, manifest=manifest,
                         run_date=args.run_date, generated_at=datetime.now(CN_TZ).isoformat(),
                         historical_offline=args.historical_offline,
                         synthetic_test_only=args.synthetic_test_only)
        result["audit"]["manifest_file_sha256"] = args.manifest_sha256
        write_benchmark_json_atomic(args.output, result)
    except (OSError, ValueError) as exc:
        print(f"OBSERVATION_INPUT_ERROR:{type(exc).__name__}:{exc}", file=sys.stderr)
        return 2
    print(result["status"])
    return 0 if result["observation_input_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
