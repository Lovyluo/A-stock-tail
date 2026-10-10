from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_transport_diagnostics import (
    evaluate_transport_candidates,
    file_sha256,
    verify_transport_diagnostic,
    write_json_atomic,
)


CACHE_ROOT = (ROOT / "overnight_quant" / "data" / "cache").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate three-round Eastmoney transport diagnostics"
    )
    parser.add_argument(
        "--evidence",
        action="append",
        nargs=2,
        metavar=("PATH", "SHA256"),
        required=True,
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    verified = []
    try:
        target = _cache_path(args.output)
        if target.exists():
            raise FileExistsError(f"market_transport_qualification_exists:{target}")
        for value, expected in args.evidence:
            source = _cache_path(value)
            payload = json.loads(source.read_text(encoding="utf-8"))
            result = verify_transport_diagnostic(
                payload,
                expected_file_sha256=expected,
                actual_file_sha256=file_sha256(source),
            )
            verified.append(result)
        qualification = evaluate_transport_candidates(verified)
        write_json_atomic(target, qualification)
    except (FileExistsError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "status": "EASTMONEY_TRANSPORT_UNQUALIFIED",
            "execution_ok": False,
            "errors": [str(exc)],
            "data_ready": False,
            "hard_gate_authorized": False,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(qualification, ensure_ascii=False, sort_keys=True))
    return 0 if qualification["status"] == "EASTMONEY_TRANSPORT_CANDIDATE_SELECTED" else 3


def _cache_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = CACHE_ROOT / path
    resolved = path.resolve()
    if resolved != CACHE_ROOT and CACHE_ROOT not in resolved.parents:
        raise ValueError("market_transport_path_must_be_in_ignored_cache")
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
