"""Default offline validation, explicit same-round audit, or externally anchored replay."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data import complete_industry_audit as audit
from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic


def _main(argv=None, *, test=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--network", action="store_true")
    mode.add_argument("--validate-only", action="store_true")
    mode.add_argument("--replay", type=Path)
    parser.add_argument("--date")
    pages = parser.add_mutually_exclusive_group()
    pages.add_argument("--page-limits", help="Frozen JSON BK-to-maximum-pages, sum<=7")
    pages.add_argument("--page-limits-file", type=Path, help="UTF-8 frozen page-limit JSON, avoids shell quoting")
    parser.add_argument("--expected-plan-hash")
    parser.add_argument("--expected-head")
    parser.add_argument("--completed-calendar", type=Path)
    parser.add_argument("--calendar-file-sha256")
    parser.add_argument("--session-confirmation", type=Path)
    parser.add_argument("--session-file-sha256")
    parser.add_argument("--expected-file-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    attempted = False
    try:
        if args.output and args.output.exists():
            raise FileExistsError("AUDIT_IMMUTABLE_OUTPUT_EXISTS")
        if args.replay:
            raw = args.replay.read_bytes()
            result = audit.replay_evidence(json.loads(raw.decode("utf-8", errors="strict")),
                expected_file_sha256=args.expected_file_sha256, actual_file_sha256=hashlib.sha256(raw).hexdigest())
        else:
            if not args.date:
                raise ValueError("AUDIT_TRADE_DATE_REQUIRED")
            limits = (json.loads(args.page_limits_file.read_text(encoding="utf-8")) if args.page_limits_file else
                      json.loads(args.page_limits) if args.page_limits else None)
            plan = audit.frozen_plan(args.date, limits)
            if args.network:
                if not args.output:
                    raise ValueError("AUDIT_IMMUTABLE_OUTPUT_REQUIRED")
                if not test and not args.output.resolve().is_relative_to(audit.CACHE.resolve()):
                    raise ValueError("AUDIT_OUTPUT_MUST_BE_IGNORED_CACHE")
                if args.expected_plan_hash != plan["plan_hash"]:
                    raise ValueError("AUDIT_FROZEN_PLAN_HASH_MISMATCH")
                write_benchmark_json_atomic(args.output.with_suffix(".plan.json"), plan)
                kwargs = dict(page_limits=limits, expected_head=args.expected_head,
                    expected_plan_hash=args.expected_plan_hash, calendar_path=args.completed_calendar,
                    calendar_sha=args.calendar_file_sha256, session_path=args.session_confirmation,
                    session_sha=args.session_file_sha256)
                attempted = True
                result = audit._run(args.date, **kwargs, test=test) if test else audit.run_audit(args.date, network=True, **kwargs)
            else:
                result = audit.validate_only(args.date, limits)
        if args.output:
            write_benchmark_json_atomic(args.output, result)
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
        result = audit.safe(status="COMPLETE_INDUSTRY_AUDIT_REJECTED", execution_ok=False,
                            errors=[str(exc)], network_requests_made=0 if not attempted or test else None,
                            dispatched_count=None if attempted else 0)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if result["execution_ok"] else 2


def main(argv=None):
    return _main(argv)


def _main_for_test(argv, context):
    if type(context) is not audit._TestContext:
        raise ValueError("AUDIT_TEST_CONTEXT_INVALID")
    return _main(argv, test=context)


if __name__ == "__main__":
    raise SystemExit(main())
