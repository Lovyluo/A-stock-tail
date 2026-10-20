"""Minute-only preflight entry: never queries quotes/NTP or starts capture."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from overnight_quant.data import minute_capture_preflight as preflight


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--inspect-environment", action="store_true")
    mode.add_argument("--verify", type=Path)
    mode.add_argument("--validate-only", action="store_true")
    parser.add_argument("--date")
    parser.add_argument("--approved-code-sha")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--expected-file-sha256")
    for name in ("calendar", "session", "before-environment", "clock-raw"):
        parser.add_argument("--" + name, type=Path)
        parser.add_argument("--" + name + "-sha256")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            if not args.date or not args.approved_code_sha:
                raise ValueError("explicit_date_and_approved_code_required")
            result = preflight.verify_file(args.verify, args.expected_file_sha256,
                trade_date=args.date, approved_sha=args.approved_code_sha)
            code = 0 if result["integrity_verified"] else 2
        elif not args.prepare and not args.inspect_environment:
            if args.plan:
                preflight.audit.check_plan(preflight.audit.read_anchored(args.plan, args.expected_plan_sha256))
            result = preflight.audit.safe(status="MINUTE_PREFLIGHT_TOOL_VALIDATED_NOT_AUTHORIZED",
                network_requests_made=0, child_processes_started=0, ntp_measurements=0, pm_approval_generated=False)
            code = 0
        else:
            if not all((args.date, args.approved_code_sha, args.plan, args.expected_plan_sha256, args.output_dir)):
                raise ValueError("date_code_plan_anchor_and_output_required")
            inputs = None
            if args.prepare:
                inputs = {}
                for key in ("calendar", "session", "before_environment", "clock_raw"):
                    path, anchor = getattr(args, key), getattr(args, key + "_sha256")
                    if not path or not anchor:
                        raise ValueError("missing_anchored_input:" + key)
                    inputs[key] = {"path": str(path.resolve()), "sha256": anchor}
            result, path = preflight.prepare(trade_date=args.date, approved_sha=args.approved_code_sha,
                plan_ref={"path": str(args.plan.resolve()), "sha256": args.expected_plan_sha256},
                output=args.output_dir, inputs=inputs, environment_only=args.inspect_environment)
            result = {**result, "result_path": str(path)}
            code = 2 if result["status"] == "MINUTE_PREFLIGHT_NO_GO" else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = preflight.audit.safe(status="MINUTE_PREFLIGHT_REQUEST_REJECTED", error=f"{type(exc).__name__}:{exc}")
        code = 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
