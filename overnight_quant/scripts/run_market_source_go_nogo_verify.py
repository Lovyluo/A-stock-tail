from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_source_go_nogo import (
    file_sha256,
    verify_market_source_go_nogo_evidence,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify S2 Go/No-Go evidence")
    parser.add_argument("path")
    parser.add_argument("--expected-file-sha256", required=True)
    parser.add_argument("--completed-calendar-contract")
    parser.add_argument("--completed-calendar-file-sha256")
    parser.add_argument("--session-confirmation-contract")
    parser.add_argument("--session-confirmation-file-sha256")
    args = parser.parse_args(argv)
    path = Path(args.path).resolve()
    actual = file_sha256(path)
    evidence = json.loads(path.read_bytes().decode("utf-8"))
    calendar = None
    calendar_actual = None
    if args.completed_calendar_contract:
        calendar_path = Path(args.completed_calendar_contract).resolve()
        calendar = json.loads(calendar_path.read_bytes().decode("utf-8"))
        calendar_actual = file_sha256(calendar_path)
    confirmation = None
    confirmation_actual = None
    if args.session_confirmation_contract:
        confirmation_path = Path(args.session_confirmation_contract).resolve()
        confirmation = json.loads(
            confirmation_path.read_bytes().decode("utf-8")
        )
        confirmation_actual = file_sha256(confirmation_path)
    result = verify_market_source_go_nogo_evidence(
        evidence,
        expected_file_sha256=args.expected_file_sha256,
        actual_file_sha256=actual,
        completed_calendar_contract=calendar,
        completed_calendar_expected_file_sha256=(
            args.completed_calendar_file_sha256
        ),
        completed_calendar_actual_file_sha256=calendar_actual,
        current_session_confirmation_contract=confirmation,
        current_session_expected_file_sha256=(
            args.session_confirmation_file_sha256
        ),
        current_session_actual_file_sha256=confirmation_actual,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["evidence_integrity_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
