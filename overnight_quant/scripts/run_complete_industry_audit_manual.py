"""Versioned manual T3.1 launcher; default mode never starts a child process."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overnight_quant.data import complete_industry_audit as audit
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import _terminate_process_tree
from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic as write

VERSION = "t31_manual_orchestration_v1"
ENVIRONMENT_SCRIPT = ROOT / "overnight_quant/scripts/inspect_industry_audit_environment.ps1"
PREFLIGHT_CHECKS = frozenset({"correct_date", "fixed_head", "clean_worktree", "environment",
    "no_reboot_or_service_restart", "frozen_plan", "plan_file_anchor", "unused_outputs",
    "service_clock_contract", "source_unchanged", "one_fresh_measurement", "real_dual_contracts"})


def read(path):
    # JSON dates remain strings; PowerShell's implicit DateTime conversion is not used.
    value = json.loads(Path(path).read_bytes().decode("utf-8-sig", errors="strict"))
    if type(value) is not dict:
        raise ValueError("OPERATOR_RESULT_NOT_OBJECT")
    return value


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(**values):
    return {**values, "operator_contract_version": VERSION, "qualification": "0/3",
            "data_ready": False, "hard_gate_authorized": False, "sampling_authorized": False,
            "provider_contract_complete": False, "records": [], "candidates": [], "tickets": [], "orders": [],
            "automatic_configuration_change": False}


@dataclass(frozen=True)
class _TestContext:
    commands: dict[str, list[str]]
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    verify_git: Callable[[], None]
    stage_timeout: float = 20


def _verify_environment(result):
    if (result.get("schema_version") != "industry_audit_environment_v1"
            or result.get("service_running") is not True
            or type(result.get("blockers")) is not list or result["blockers"]
            or type(result.get("workers")) is not list or result["workers"]
            or type(result.get("tasks")) is not list
            or type(result.get("locks")) is not list or result["locks"]
            or type(result.get("clock_or_power_events")) is not list or result["clock_or_power_events"]
            or type(result.get("journals")) is not list
            or any(type(j) is not dict or j.get("valid") is not True or j.get("pending") is not False
                   for j in result["journals"])):
        raise ValueError("OPERATOR_ENVIRONMENT_RESULT_REJECTED")
    if any(type(t) is not dict or type(t.get("Enabled")) is not bool or t["Enabled"] or t.get("State") != "Disabled"
           for t in result["tasks"]):
        raise ValueError("OPERATOR_PROJECT_TASK_REJECTED")
    if type(result.get("service_process_id")) is not int or result["service_process_id"] <= 0:
        raise ValueError("OPERATOR_ENVIRONMENT_IDENTITY_INVALID")
    for field in ("checked_at", "system_boot_time"):
        if type(result.get(field)) is not str or datetime.fromisoformat(result[field]).utcoffset() is None:
            raise ValueError("OPERATOR_ENVIRONMENT_TIMESTAMP_INVALID")


def _verify_preflight(result, now):
    checks = result.get("checks")
    if (result.get("status") != "PRE_LAUNCH_GO" or type(checks) is not dict
            or set(checks) != PREFLIGHT_CHECKS or any(v is not True for v in checks.values())):
        raise ValueError("OPERATOR_PREFLIGHT_RESULT_REJECTED")
    checked = datetime.fromisoformat(result["checked_at"])
    elapsed = (now - checked).total_seconds()
    for field, limit in (("last_sample_age_seconds", 600), ("last_sync_age_seconds", 3600)):
        value = result.get(field)
        if type(value) not in (float, int) or not math.isfinite(value) or elapsed < 0 or not 0 <= value + elapsed <= limit:
            raise ValueError("OPERATOR_CLOCK_EVIDENCE_STALE")


def _stage(name, command, directory, timeout):
    receipt = {"stage": name, "command": command, "started_at": datetime.now(CN_TZ).isoformat(),
               "exit_code": None, "timed_out": False, "process_started": False}
    write(directory / f"{name}.intent.json", receipt)
    started = time.perf_counter()
    process = None
    stdout, stderr = b"", b""
    try:
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        receipt["process_started"] = True
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            receipt["timed_out"] = True
            _terminate_process_tree(process)
            stdout, stderr = process.communicate(timeout=5)
        receipt["exit_code"] = process.returncode
    except OSError as exc:
        receipt["launch_error"] = f"{type(exc).__name__}:{exc}"
    except BaseException:
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
            stdout, stderr = process.communicate(timeout=5)
        raise
    finally:
        receipt.update(completed_at=datetime.now(CN_TZ).isoformat(),
                       elapsed_ms=(time.perf_counter() - started) * 1000,
                       process_reaped=process is None or process.poll() is not None)
        for suffix, data in (("stdout.bin", stdout), ("stderr.bin", stderr)):
            path = directory / f"{name}.{suffix}"
            with path.open("xb") as stream:
                stream.write(data)
            receipt[suffix.replace(".", "_") + "_sha256"] = digest(path)
        write(directory / f"{name}.receipt.json", receipt)
    if receipt["exit_code"] != 0 or receipt["timed_out"] or receipt.get("launch_error"):
        raise ValueError(f"OPERATOR_{name.upper()}_PROCESS_FAILED")
    return receipt


def run_manual(ops_directory, *, preflight_sha256, page_limits_file, network=False):
    if type(network) is not bool:
        raise ValueError("OPERATOR_NETWORK_CONTROL_INVALID")
    return _run(ops_directory, preflight_sha256=preflight_sha256,
                page_limits_file=page_limits_file, network=network)


def _run(ops_directory, *, preflight_sha256, page_limits_file, network, test=None):
    if type(network) is not bool:
        raise ValueError("OPERATOR_NETWORK_CONTROL_INVALID")
    result = safe(status="NO_GO", execution_ok=False, current_stage="parameters",
                  source_attempt_accepted=False, source_process_started=False,
                  network_requests_made=0, test_only=test is not None)
    directory = Path(ops_directory).resolve()
    journal = directory / "operator-run-v1"
    claimed = False
    now = test.now if test else lambda: datetime.now(CN_TZ)
    sleep = test.sleep if test else time.sleep
    try:
        if network:
            journal.mkdir(exist_ok=False)
            claimed = True
        intent, anchors = read(directory / "intent.json"), read(directory / "prerequisite-anchors.json")
        limits = read(page_limits_file)
        plan = audit.frozen_plan(intent["trade_date"], limits)
        if plan["plan_hash"] != anchors["plan_hash"] or plan["maximum_requests"] != 13:
            raise ValueError("OPERATOR_FROZEN_PLAN_MISMATCH")
        if digest(directory / "launch_preflight.py") != preflight_sha256:
            raise ValueError("OPERATOR_PREFLIGHT_SCRIPT_HASH_MISMATCH")
        result.update(plan_hash=plan["plan_hash"], trade_date=plan["trade_date"])
        if not network:
            return safe(**{**result, "status": "MANUAL_AUDIT_PLAN_VALIDATED", "execution_ok": True})
        start = datetime.fromisoformat(plan["time_configuration"]["start_at"])
        latest = datetime.fromisoformat(plan["time_configuration"]["latest_start_at"])
        def window():
            if now().date().isoformat() != plan["trade_date"] or now() > latest:
                raise ValueError("OPERATOR_AUDIT_WINDOW_MISSED")
        def git_check():
            if test:
                test.verify_git()
            elif (subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != intent["fixed_head"]
                  or subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()):
                raise ValueError("OPERATOR_FIXED_HEAD_OR_WORKTREE_CHANGED")
        def wait_until(target):
            while now() < target:
                window()
                sleep(min(.1, (target - now()).total_seconds()))
        window()
        git_check()
        if (directory / "business-execution-intent.json").exists():
            raise ValueError("OPERATOR_BUSINESS_ATTEMPT_EXISTS")
        wait_until(start - timedelta(seconds=10))
        shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        commands = test.commands if test else {
            "environment": [shell, "-NoProfile", "-NonInteractive", "-File", str(ENVIRONMENT_SCRIPT),
                            "-OpsDirectory", str(directory), "-OutputPath", str(directory / "pre-launch-environment.json")],
            "preflight": [sys.executable, str(directory / "launch_preflight.py")],
            "business": [sys.executable, str(ROOT / "overnight_quant/scripts/run_complete_industry_audit.py"),
                "--network", "--date", plan["trade_date"], "--page-limits-file", str(page_limits_file),
                "--expected-head", intent["fixed_head"], "--expected-plan-hash", plan["plan_hash"],
                "--completed-calendar", intent["paths"]["calendar"], "--calendar-file-sha256", anchors["calendar"]["file_sha256"],
                "--session-confirmation", intent["paths"]["session"], "--session-file-sha256", anchors["session"]["file_sha256"],
                "--output", intent["paths"]["evidence"]],
        }
        for name, output, validate in (
            ("environment", "pre-launch-environment.json", _verify_environment),
            ("preflight", "launch-preflight.json", lambda r: _verify_preflight(r, now())),
        ):
            result["current_stage"] = name
            window()
            _stage(name, commands[name], journal, test.stage_timeout if test else 20)
            value = read(directory / output)
            if value.get("test_only", False) is not (test is not None):
                raise ValueError("OPERATOR_TEST_SCOPE_MISMATCH")
            validate(value)
        result["current_stage"] = "business_admission"
        wait_until(start)
        window()
        git_check()
        _verify_preflight(read(directory / "launch-preflight.json"), now())
        if digest(directory / "launch_preflight.py") != preflight_sha256:
            raise ValueError("OPERATOR_PREFLIGHT_SCRIPT_CHANGED")
        write(directory / "business-execution-intent.json", {"accepted_at": now().isoformat(),
              "operator_contract_version": VERSION, "plan_hash": plan["plan_hash"], "test_only": test is not None})
        result.update(status="MANUAL_AUDIT_COLLECTION_FAILED", current_stage="business", source_attempt_accepted=True,
                      network_requests_made=0 if test else None)
        deadline = datetime.fromisoformat(plan["time_configuration"]["collection_deadline"])
        _stage("business", commands["business"], journal,
               min(test.stage_timeout if test else 125, (deadline - now()).total_seconds()))
        source = read(intent["paths"]["evidence"])
        if (source.get("execution_ok") is not True or source.get("candidate_batch_complete") is not True
                or source.get("test_only") is not (test is not None)
                or source.get("plan") != plan
                or any(source.get(k) is not False for k in ("data_ready", "hard_gate_authorized"))
                or any(source.get(k) != [] for k in ("records", "candidates", "tickets", "orders"))):
            raise ValueError("OPERATOR_BUSINESS_RESULT_REJECTED")
        result.update(status="MANUAL_AUDIT_COLLECTION_FINISHED_REPLAY_REQUIRED", execution_ok=True,
                      source_file_sha256=digest(intent["paths"]["evidence"]))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        result.update(error_code=str(exc) or type(exc).__name__, exception_type=type(exc).__name__)
    finally:
        if claimed:
            receipt = journal / "business.receipt.json"
            if receipt.exists():
                result["source_process_started"] = read(receipt)["process_started"]
                if result["source_process_started"] is False:
                    result["network_requests_made"] = 0
                    result["status"] = "NO_GO"
            write(journal / "result.json", result)
    return result


def _run_for_test(*args, context, **kwargs):
    if type(context) is not _TestContext:
        raise ValueError("OPERATOR_TEST_CONTEXT_INVALID")
    return _run(*args, **kwargs, test=context)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ops-directory", type=Path, required=True)
    parser.add_argument("--preflight-sha256", required=True)
    parser.add_argument("--page-limits-file", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--network", action="store_true")
    mode.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    result = run_manual(args.ops_directory, preflight_sha256=args.preflight_sha256,
                        page_limits_file=args.page_limits_file, network=args.network)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0 if result["execution_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
