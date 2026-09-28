from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_transport_diagnostics import (
    TRANSPORT_IDS,
    run_transport_diagnostic,
    write_json_atomic,
)


CACHE_ROOT = (ROOT / "overnight_quant" / "data" / "cache").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one isolated Eastmoney transport diagnostic path"
    )
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--transport-id", choices=TRANSPORT_IDS, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        output = _cache_path(args.output)
        if output.exists():
            raise FileExistsError(f"market_transport_output_exists:{output}")
        result = run_transport_diagnostic(
            transport_id=args.transport_id,
            trade_date=args.date,
            round_id=args.round_id,
            network=args.network,
        )
        write_json_atomic(output, result)
    except (FileExistsError, ValueError) as exc:
        print(json.dumps({
            "status": "MARKET_TRANSPORT_REQUEST_INVALID",
            "execution_ok": False,
            "reason": str(exc),
            "data_ready": False,
            "hard_gate_authorized": False,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({
        "status": result["status"],
        "transport_id": result["transport_id"],
        "round_id": result["round_id"],
        "candidate_round_passed": result.get("candidate_round_passed", False),
        "output": str(output),
        "evidence_hash": result.get("evidence_hash", ""),
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }, ensure_ascii=False, sort_keys=True))
    return 0


def _cache_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = CACHE_ROOT / path
    resolved = path.resolve()
    if resolved != CACHE_ROOT and CACHE_ROOT not in resolved.parents:
        raise ValueError("market_transport_output_must_be_in_ignored_cache")
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
