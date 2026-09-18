from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_go_nogo import (
    NO_GO,
    collect_live_environment,
    run_market_source_go_nogo,
    write_go_nogo_json_atomic,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run S2 pre-sampling Go/No-Go")
    parser.add_argument("--date", required=True)
    parser.add_argument("--codes", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--calendar-contract", required=True)
    parser.add_argument("--calendar-file-sha256", required=True)
    parser.add_argument("--session-confirmation-contract", required=True)
    parser.add_argument("--session-confirmation-file-sha256", required=True)
    parser.add_argument("--cutoff-clock", default="13:30:00")
    parser.add_argument("--test-only-environment-fixture")
    args = parser.parse_args(argv)
    started = datetime.now(CN_TZ)
    output = Path(args.output).resolve()
    try:
        calendar_path = Path(args.calendar_contract).resolve()
        raw_calendar = calendar_path.read_bytes()
        import hashlib
        calendar_actual_sha256 = hashlib.sha256(raw_calendar).hexdigest()
        calendar = json.loads(raw_calendar.decode("utf-8"))
        confirmation_path = Path(args.session_confirmation_contract).resolve()
        raw_confirmation = confirmation_path.read_bytes()
        confirmation_actual_sha256 = hashlib.sha256(raw_confirmation).hexdigest()
        confirmation = json.loads(raw_confirmation.decode("utf-8"))
        if args.test_only_environment_fixture:
            if os.environ.get("A_STOCK_GO_NOGO_TEST_MODE") != "1":
                raise ValueError("test_only_environment_fixture_forbidden")
            environment = json.loads(
                Path(args.test_only_environment_fixture).read_text(
                    encoding="utf-8"
                )
            )
            evidence_scope = "test_only"
        else:
            environment = collect_live_environment(output_path=output)
            evidence_scope = "production"
        completed = datetime.now(CN_TZ)
        result = run_market_source_go_nogo(
            trade_date=args.date,
            codes=args.codes.split(","),
            cutoff_clock=args.cutoff_clock,
            calendar_contract=calendar,
            calendar_expected_file_sha256=args.calendar_file_sha256,
            calendar_actual_file_sha256=calendar_actual_sha256,
            session_confirmation_contract=confirmation,
            session_expected_file_sha256=(
                args.session_confirmation_file_sha256
            ),
            session_actual_file_sha256=confirmation_actual_sha256,
            environment=environment,
            started_at=started.isoformat(timespec="microseconds"),
            completed_at=completed.isoformat(timespec="microseconds"),
            evidence_scope=evidence_scope,
        )
        write_go_nogo_json_atomic(output, result)
    except FileExistsError:
        result = _failure("immutable_result_exists")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 3
    except Exception as exc:
        result = _failure(f"go_nogo_failed:{type(exc).__name__}:{exc}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") != NO_GO else 2


def _failure(reason: str) -> dict:
    return {
        "status": NO_GO,
        "execution_ok": False,
        "evidence_integrity_verified": False,
        "sampling_authorized": False,
        "qualification_result": "NOT_EVALUATED",
        "consecutive_qualified_days": 0,
        "failure_reasons": [reason],
        "task_actions": [],
        "enabled_tasks": [],
        "automatic_configuration_change": False,
        "automatic_qualification_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }


if __name__ == "__main__":
    raise SystemExit(main())
