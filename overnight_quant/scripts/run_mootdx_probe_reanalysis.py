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
from overnight_quant.data.transaction_attribution import (
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
    print(
        json.dumps(
            {
                "status": result["status"],
                "data_ready": result["data_ready"],
                "source": result["source"],
                "trade_date": result["trade_date"],
                "minute_label_semantics": result[
                    "minute_label_semantics"
                ],
                "probe_evidence_hash": result[
                    "probe_evidence_hash"
                ],
                "transaction_evidence_hash": result[
                    "transaction_evidence_hash"
                ],
                "combined_evidence_hash": result[
                    "combined_evidence_hash"
                ],
                "reanalysis_evidence_hash": result[
                    "reanalysis_evidence_hash"
                ],
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
    return 0 if result["status"] == "PM_REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
