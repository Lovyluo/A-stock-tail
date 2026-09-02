from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.mootdx_boundary_audit import (
    AUDIT_CLOCKS,
    AUDIT_CODE,
    AUDIT_REQUEST_DEADLINE_MS,
    parse_audit_endpoint,
    run_mootdx_boundary_audit,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture isolated mootdx transaction evidence for 600000 at "
            "14:49:57, 14:50:01 and 14:50:08. The output is audit-only."
        )
    )
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Fixed mootdx host:port used by the main probe.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "overnight_quant" / "data" / "cache"),
    )
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    day = date.fromisoformat(args.date)
    endpoint = parse_audit_endpoint(args.endpoint)
    if args.validate_only:
        result = {
            "status": "BOUNDARY_AUDIT_VALIDATED",
            "execution_ok": True,
            "data_ready": False,
            "qualification_eligible": False,
            "formal_evidence_integration": False,
            "trade_date": day.isoformat(),
            "code": AUDIT_CODE,
            "source": "mootdx",
            "endpoint_id": endpoint["id"],
            "targets": [
                f"{day.isoformat()}T{value.isoformat()}+08:00"
                for value in AUDIT_CLOCKS
            ],
            "request_deadline_ms": AUDIT_REQUEST_DEADLINE_MS,
            "candidates": [],
            "tickets": [],
            "orders": [],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    result = run_mootdx_boundary_audit(
        trade_date=day,
        endpoint=endpoint,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "BOUNDARY_AUDIT_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
