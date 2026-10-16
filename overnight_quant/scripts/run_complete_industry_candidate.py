"""Offline captured-input candidate composition or planning; never calls HTTP."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.complete_industry_candidate import (
    CompleteIndustryCandidateEnvelope, CompleteIndustryCandidateProvider,
    _safe, complete_industry_audit_plan,
)
from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input", type=Path)
    mode.add_argument("--plan", action="store_true")
    parser.add_argument("--expected-file-sha256")
    parser.add_argument("--date")
    parser.add_argument("--page-counts", help="Explicit JSON BK-to-page-count plan")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.plan:
            if not args.date:
                parser.error("--plan requires --date")
            pages = json.loads(args.page_counts) if args.page_counts else None
            result = complete_industry_audit_plan(args.date, page_counts=pages)
        else:
            if not args.expected_file_sha256:
                parser.error("--input requires an external --expected-file-sha256")
            raw = args.input.read_bytes()
            actual = hashlib.sha256(raw).hexdigest()
            if actual != args.expected_file_sha256:
                raise ValueError("candidate_input_external_sha256_mismatch")
            envelope = json.loads(raw.decode("utf-8", errors="strict"))
            result = CompleteIndustryCandidateProvider(CompleteIndustryCandidateEnvelope(
                provider_key=envelope["provider_key"], batch=envelope["batch"],
            )).collect_batch()
            result["input_file_sha256"] = actual
        if args.output:
            write_benchmark_json_atomic(args.output, result)
    except FileExistsError:
        result = _safe(status="COMPLETE_INDUSTRY_CANDIDATE_WRITE_REJECTED", execution_ok=False,
                       candidate_batch_complete=False, audit_records=[], errors=["immutable_output_exists"])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result = _safe(status="COMPLETE_INDUSTRY_CANDIDATE_REJECTED", execution_ok=False,
                       candidate_batch_complete=False, audit_records=[], errors=[str(exc)])
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if result["execution_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
