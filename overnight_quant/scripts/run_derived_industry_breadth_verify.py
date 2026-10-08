from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.eastmoney_derived_industry_breadth import verify_evidence
from overnight_quant.scripts.run_market_source_validation import write_json_atomic


def verify_file(path: str | Path, *, expected_file_sha256: str) -> dict:
    source = Path(path)
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != expected_file_sha256:
        return {
            "status": "DERIVED_INDUSTRY_BREADTH_EVIDENCE_INVALID",
            "evidence_integrity_verified": False,
            "provider_contract_complete": False,
            "qualification_eligible": False,
            "expected_file_sha256": expected_file_sha256,
            "actual_file_sha256": actual,
            "errors": ["external_sha256_anchor_mismatch"],
            "data_ready": False,
            "hard_gate_authorized": False,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }
    payload = json.loads(source.read_text(encoding="utf-8"))
    result = verify_evidence(
        payload,
        expected_file_sha256=expected_file_sha256,
    )
    result["expected_file_sha256"] = expected_file_sha256
    result["actual_file_sha256"] = actual
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Verify derived Eastmoney industry breadth evidence"
    )
    parser.add_argument("path")
    parser.add_argument("--expected-file-sha256", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = verify_file(
        args.path,
        expected_file_sha256=args.expected_file_sha256,
    )
    if args.output:
        write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("evidence_integrity_verified") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
