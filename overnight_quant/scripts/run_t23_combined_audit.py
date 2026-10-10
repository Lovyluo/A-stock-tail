from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.t23_combined_audit import AuditParameters, run_combined_audit, safe


def main(argv=None, *, _clock=None, _test_transport_factory=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Single-batch T2.3 audit; never a qualification day")
    parser.add_argument("--date", required=True)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--expected-head")
    parser.add_argument("--start-at")
    parser.add_argument("--latest-start-at")
    parser.add_argument("--feature-cutoff")
    parser.add_argument("--collection-deadline")
    parser.add_argument("--completed-calendar")
    parser.add_argument("--calendar-file-sha256")
    parser.add_argument("--session-confirmation")
    parser.add_argument("--session-file-sha256")
    args = parser.parse_args(argv)
    try:
        if args.network and args.validate_only:
            raise ValueError("AUDIT_MODES_MUTUALLY_EXCLUSIVE")
        parameters = AuditParameters.for_date(args.date, **{
            key: getattr(args, key) for key in (
                "start_at", "latest_start_at", "feature_cutoff", "collection_deadline")})
        result = run_combined_audit(
            parameters, network=args.network, output=args.output,
            expected_head=args.expected_head, calendar_path=args.completed_calendar,
            calendar_sha=args.calendar_file_sha256, session_path=args.session_confirmation,
            session_sha=args.session_file_sha256, clock=_clock,
            _test_transport_factory=_test_transport_factory,
        )
    except (ValueError, OSError) as exc:
        result = safe({"status": "COMBINED_AUDIT_PARAMETERS_INVALID", "execution_ok": False,
                       "error_code": str(exc), "network_requests_made": 0})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["status"] in {"COMBINED_AUDIT_PARAMETERS_VALID", "COMBINED_AUDIT_VALIDATED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
