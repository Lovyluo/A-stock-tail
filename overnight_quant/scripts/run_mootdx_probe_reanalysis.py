from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.minute_label_probe import (
    write_probe_json_atomic,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.probe_evidence import verify_probe_evidence
from overnight_quant.data.transaction_attribution import (
    REANALYSIS_INPUT_INVALID,
    build_mootdx_probe_reanalysis,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reanalyse an immutable mootdx minute probe with the "
            "versioned transaction attribution contract."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source = Path(args.input).resolve()
    target = Path(args.output).resolve()
    if source == target:
        raise SystemExit("reanalysis_output_must_differ_from_input")
    raw = source.read_bytes()
    if raw.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raise SystemExit("probe_json_must_be_utf8_without_bom")
    payload = json.loads(raw.decode("utf-8"))
    result = build_mootdx_probe_reanalysis(payload)
    write_probe_json_atomic(result, target)
    written = json.loads(target.read_text(encoding="utf-8"))
    verification = verify_probe_evidence(written, source="mootdx")
    successful = (
        result.get("status") == "PM_REVIEW_REQUIRED"
        and verification.get("status") == "PROBE_EVIDENCE_VERIFIED"
    )
    if not successful and result.get("status") == "PM_REVIEW_REQUIRED":
        result["status"] = "REANALYSIS_OUTPUT_INVALID"
        result["execution_ok"] = False
        result["minute_label_validation_status"] = "INVALID"
        result["reanalysis_errors"] = sorted(
            {
                "independent_evidence_verification_failed",
                *(
                    str(error)
                    for error in (verification.get("errors") or [])
                ),
            }
        )
    result["independent_evidence_verification"] = verification
    result.pop("reanalysis_evidence_hash", None)
    result["reanalysis_evidence_hash"] = stable_hash(result)
    write_probe_json_atomic(result, target)
    print(
        json.dumps(
            {
                "status": result.get("status", REANALYSIS_INPUT_INVALID),
                "data_ready": bool(result.get("data_ready")),
                "source": result.get("source", "mootdx"),
                "trade_date": result.get("trade_date", ""),
                "minute_label_semantics": result.get(
                    "minute_label_semantics",
                    "INCONCLUSIVE",
                ),
                "probe_evidence_hash": result.get(
                    "probe_evidence_hash",
                    "",
                ),
                "transaction_evidence_hash": result.get(
                    "transaction_evidence_hash",
                    "",
                ),
                "combined_evidence_hash": result.get(
                    "combined_evidence_hash",
                    "",
                ),
                "reanalysis_evidence_hash": result.get(
                    "reanalysis_evidence_hash",
                    "",
                ),
                "independent_verification_status": verification.get(
                    "status",
                    "PROBE_EVIDENCE_INVALID",
                ),
                "output": str(target),
                "candidates": [],
                "tickets": [],
                "orders": [],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
