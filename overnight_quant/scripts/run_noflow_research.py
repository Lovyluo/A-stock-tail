"""Offline opt-in research CLI. No transport, live mode or order interface."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_session_confirmation import write_market_session_confirmation_json_atomic
from overnight_quant.strategy.close_confirmation_noflow_v1.contracts import STRATEGY_ID, safety
from overnight_quant.strategy.close_confirmation_noflow_v1.strategy import NoFundFlowResearchStrategy


def run(*, input_path: Path | None, expected_sha256: str | None, test_only: bool = False) -> dict:
    try:
        if input_path is None:
            raise ValueError("offline_input_required")
        raw = input_path.read_bytes()
        if not expected_sha256 or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("external_input_sha256_mismatch")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate_json_key")
                result[key] = value
            return result
        snapshot = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite_json")))
        return NoFundFlowResearchStrategy().evaluate_snapshot(snapshot, test_only=test_only)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        return {**safety(), "execution_ok": False, "research_ready": False, "research_ranking": [],
                "research_selection": [], "status": "POINT_IN_TIME_DATA_INCOMPLETE", "missing_reasons": [str(exc)]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=[STRATEGY_ID], required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--expected-input-sha256")
    parser.add_argument("--test-only", action="store_true", help="Accept only explicitly synthetic records; never formal data.")
    parser.add_argument("--output-dir", type=Path, help="Optional write-once strategy-isolated report directory.")
    args = parser.parse_args(argv)
    result = run(input_path=args.input, expected_sha256=args.expected_input_sha256, test_only=args.test_only)
    if args.output_dir:
        target = args.output_dir / STRATEGY_ID / ("test_only" if args.test_only else "research") / (
            result.get("snapshot_hash", "rejected") + ".json"
        )
        try:
            write_market_session_confirmation_json_atomic(target, result)
        except (FileExistsError, OSError, ValueError) as exc:
            print(json.dumps({**safety(), "status": "OUTPUT_REJECTED", "error": str(exc)}))
            return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0 if result.get("research_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
