"""Read-only, write-once minute preflight. Never grants PM approval or starts capture."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from importlib.metadata import PackageNotFoundError, version as installed_version
import math
import os
from pathlib import Path
import re
import shutil
import sys

from . import minute_capture_audit as audit
from .market_source_go_nogo import _completed_calendar_errors
from .market_session_confirmation import verify_market_session_confirmation_evidence
from .probe_worker_process import run_probe_worker_process
from overnight_quant.scripts.run_complete_industry_audit_manual import _verify_environment

VERSION = "minute_capture_preflight_v1"
VERIFIER = "minute_capture_preflight_verifier_v1"
ENV_SCOPE = "minute_capture_environment_v1"
AUTHORIZATION_VERSION = "minute_capture_manual_authorization_v2"
ROOT = audit.ROOT
ENV_SCRIPT = ROOT / "overnight_quant/scripts/inspect_industry_audit_environment.ps1"
CHECKS = frozenset({"correct_date", "fixed_head", "clean_worktree", "environment",
    "no_reboot_or_service_restart", "frozen_plan", "plan_file_anchor", "unused_outputs",
    "service_clock_contract", "source_unchanged", "one_fresh_measurement", "real_dual_contracts"})


def execution_profile(name="legacy_v1"):
    if name == "legacy_v1":
        return audit, VERSION, VERIFIER
    if name == "local_prepare_v2":
        from . import minute_local_prepare_contract as prepared
        return prepared, prepared.PREFLIGHT_VERSION, prepared.PREFLIGHT_VERIFIER
    if name == "single_bars_protocol_diagnostic_v1":
        from . import minute_protocol_contract as protocol
        return protocol, protocol.PREFLIGHT_VERSION, protocol.PREFLIGHT_VERIFIER
    from . import minute_protocol_differential_contract as differential
    if name in differential.CONTRACT_NAMES:
        profile = differential.profile_for_name(name)
        return profile, profile.PREFLIGHT_VERSION, profile.PREFLIGHT_VERIFIER
    raise ValueError("unknown_minute_execution_contract")


def _protocol_contract(name):
    from .minute_protocol_differential_contract import CONTRACT_NAMES
    return name == "single_bars_protocol_diagnostic_v1" or name in CONTRACT_NAMES


def now():
    return datetime.now(audit.CN)


def ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}


def read(reference):
    result = audit.read_anchored(reference["path"], reference["sha256"])
    if type(result) is not dict:
        raise ValueError("preflight_input_not_object")
    return result


def stamp(text):
    value = datetime.fromisoformat(text)
    if value.utcoffset() != timedelta(hours=8):
        raise ValueError("timestamp_not_china_time")
    return value


def _text(raw):
    try:
        return raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="strict")


def clock_status(environment):
    query = environment["clock_query"]
    if query["command"] != ["w32tm", "/query", "/status", "/verbose"] or query["exit_code"] != 0:
        raise ValueError("clock_status_query_failed")
    text = _text(base64.b64decode(query["stdout_base64"], validate=True))

    def field(pattern):
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            raise ValueError("clock_status_field_unavailable:" + pattern)
        return match.group(1).strip()

    last = field(r"^\s*(?:上次成功同步时间|Last Successful Sync Time)\s*[:：]\s*(.+)$")
    return {"leap": int(field(r"^\s*(?:Leap 指示符|Leap Indicator)\s*[:：]\s*(\d+)")),
            "error": int(field(r"^\s*(?:上次同步错误|Last Sync Error)\s*[:：]\s*(\d+)")),
            "source": field(r"^\s*(?:源|Source)\s*[:：]\s*(.+)$"),
            "last_success": datetime.strptime(last, "%Y/%m/%d %H:%M:%S").replace(tzinfo=audit.CN).isoformat()}


def clock_samples(reference):
    raw = Path(reference["path"]).read_bytes()
    if sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("clock_raw_external_sha256_mismatch")
    text = _text(raw)
    sources = list(re.finditer(r"(?:正在跟踪|Tracking)\s+(\S+)\s+\[([^\]]+)\]", text, re.IGNORECASE))
    if len(sources) != 1:
        raise ValueError("clock_measurement_source_unavailable")
    source = sources[0]
    rows = []
    for line in text.splitlines():
        if not re.match(r"^\s*\d+\s*,", line):
            continue
        cells = line.split(",")
        if len(cells) != 5:
            raise ValueError("clock_sample_parse_error")
        start, end, filetime = map(int, cells[:3])
        rtt, offset = (float(v) * 1000 for v in cells[3:])
        if not (end >= start >= 0 and filetime > 0 and math.isfinite(rtt) and rtt >= 0
                and math.isfinite(offset)):
            raise ValueError("clock_sample_invalid")
        # FILETIME is supplied by w32tm, not a caller-written summary timestamp.
        sampled = (datetime(1601, 1, 1, tzinfo=timezone.utc)
                   + timedelta(microseconds=filetime // 10)).astimezone(audit.CN)
        rows.append({"request_time": sampled.isoformat(), "rtt_ms": rtt, "offset_ms": offset,
                     "filetime": str(filetime), "rdtsc_start": str(start), "rdtsc_end": str(end)})
    if len(rows) != 3 or any(stamp(a["request_time"]) >= stamp(b["request_time"])
                             for a, b in zip(rows, rows[1:])):
        raise ValueError("exactly_three_ordered_clock_samples_required")
    return {"source": source[1], "resolved_endpoint": source[2], "samples": rows,
            "raw_sha256": reference["sha256"], "independent_source_cross_validation": False}


def _query_record(reference):
    result = read(reference)
    if result.get("ok") is not True or result.get("returncode") != 0 or result.get("cleanup_complete") is not True:
        raise ValueError("environment_query_failed:" + str(result.get("error_code")) + ":" + str(result.get("error", ""))[:300])
    for key in ("stdout", "stderr"):
        if key + "_sha256" in result and result[key + "_sha256"] != sha256(result.get(key, "").encode("utf-8")).hexdigest():
            raise ValueError("environment_raw_output_hash_mismatch:" + key)
    raw_result = json.loads(result["stdout"])
    if (type(raw_result) is not dict or raw_result.get("ok") is not True
            or type(raw_result.get("payload")) is not dict or result.get("payload") != raw_result.get("payload")):
        raise ValueError("environment_payload_raw_mismatch")
    return raw_result["payload"]


def _reject_test(value):
    if isinstance(value, dict):
        if value.get("test_only") is True or value.get("synthetic_test_only") is True or value.get("evidence_scope") == "test_only":
            raise ValueError("test_only_material_cannot_authorize_real_capture")
        for child in value.values():
            _reject_test(child)
    elif isinstance(value, list):
        for child in value:
            _reject_test(child)


def _basic(trade_date, approved_sha, plan_ref, current, *, execution_contract="legacy_v1", target_at=None):
    if current.utcoffset() != timedelta(hours=8) or current.date().isoformat() != trade_date:
        raise ValueError("current_date_or_timezone_mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", approved_sha):
        raise ValueError("approved_code_sha_invalid")
    plan = read(plan_ref)
    profile, _, _ = execution_profile(execution_contract)
    profile.check_plan(plan)
    protocol = _protocol_contract(execution_contract)
    for package, expected in plan["dependencies" if protocol else "client_versions"].items():
        try:
            actual = installed_version(package)
        except PackageNotFoundError as exc:
            raise ValueError("frozen_client_package_missing:" + package) from exc
        if actual != expected:
            raise ValueError("frozen_client_version_mismatch:" + package)
    first_start = stamp(f"{trade_date}T{audit.CLOCKS[0]}+08:00")
    if execution_contract == "local_prepare_v2":
        first_start -= timedelta(seconds=5)
    if protocol:
        target = stamp(target_at)
        if hasattr(profile, "target_clock") and target_at != f"{trade_date}T{profile.target_clock}+08:00":
            raise ValueError("differential_frozen_target_mismatch")
        remaining_ms = plan["absolute_network_and_cleanup_ms"] + plan["seal_budget_ms"]
        if (target.date().isoformat() != trade_date or
                target + timedelta(milliseconds=remaining_ms) > stamp(f"{trade_date}T{plan['collection_deadline']}+08:00")):
            raise ValueError("protocol_target_date_or_absolute_budget_invalid")
        first_start = target - timedelta(seconds=5)
    if current >= first_start:
        raise ValueError("first_observation_window_already_started")
    return plan


def _environment(reference, trade_date, approved_sha, root, current, test_only, *, execution_contract="legacy_v1"):
    environment = _query_record(reference)
    if not test_only:
        _reject_test(environment)
    _verify_environment(environment)
    protocol = _protocol_contract(execution_contract)
    profile, _, _ = execution_profile(execution_contract)
    if (environment.get("scope") != (profile.ENV_SCOPE if protocol else ENV_SCOPE)
            or environment.get("trade_date") != trade_date
            or Path(environment["project_root"]).resolve() != Path(root).resolve()
            or Path(environment["capture_root"]).resolve() != (Path(root) / "overnight_quant/data/cache/minute_capture_audit").resolve()
            or environment.get("timezone") != "China Standard Time"):
        raise ValueError("minute_environment_identity_mismatch")
    relative = getattr(profile, "AUDIT_RELATIVE", "overnight_quant/data/cache/minute_protocol_diagnostic")
    if protocol and Path(environment["diagnostic_root"]).resolve() != (Path(root) / relative).resolve():
        raise ValueError("protocol_environment_root_mismatch")
    if environment.get("git_head") != approved_sha or environment.get("git_status") != []:
        raise ValueError("fixed_head_or_clean_worktree_mismatch")
    if environment.get("cleanup_incomplete_exists") is not False or environment.get("date_output_exists") is not False:
        raise ValueError("cleanup_or_existing_capture_output_blocks_launch")
    if stamp(environment["checked_at"]) > current or stamp(environment["checked_at"]).date().isoformat() != trade_date:
        raise ValueError("environment_time_invalid")
    status = clock_status(environment)
    if status["leap"] != 0 or status["error"] != 0:
        raise ValueError("clock_service_explicit_failure")
    if not 0 <= (current - stamp(status["last_success"])).total_seconds() <= 3600:
        raise ValueError("last_successful_sync_stale")
    return environment, status


def evaluate(inputs, trade_date, approved_sha, root, current, *, test_only=False, execution_contract="legacy_v1", target_at=None):
    plan = _basic(trade_date, approved_sha, inputs["plan"], current, execution_contract=execution_contract, target_at=target_at)
    profile, _, _ = execution_profile(execution_contract)
    environment, service = _environment(inputs["environment"], trade_date, approved_sha, root, current, test_only,
                                        execution_contract=execution_contract)
    baseline, before_service = _environment(inputs["before_environment"], trade_date, approved_sha, root,
                                           stamp(_query_record(inputs["before_environment"])["checked_at"]), test_only,
                                           execution_contract=execution_contract)
    samples = clock_samples(inputs["clock_raw"])
    sample_times = [stamp(row["request_time"]) for row in samples["samples"]]
    if (any(t.date().isoformat() != trade_date for t in sample_times)
            or not stamp(baseline["checked_at"]) <= sample_times[0] <= sample_times[-1] <= stamp(environment["checked_at"])
            or environment["service_process_id"] != baseline["service_process_id"]
            or stamp(environment["system_boot_time"]) != stamp(baseline["system_boot_time"])):
        raise ValueError("clock_sample_order_or_environment_identity_changed")
    if service["source"] != before_service["source"]:
        raise ValueError("system_sync_source_changed")
    if any(abs(row["offset_ms"]) > 2000 for row in samples["samples"]):
        raise ValueError("clock_offset_exceeds_existing_limit")
    age = (current - sample_times[-1]).total_seconds()
    if not 0 <= age <= 600:
        raise ValueError("clock_measurement_stale")
    calendar, session = read(inputs["calendar"]), read(inputs["session"])
    if not test_only:
        _reject_test(calendar)
        _reject_test(session)
    errors = _completed_calendar_errors(calendar, trade_date, expected_file_sha256=inputs["calendar"]["sha256"],
                                         actual_file_sha256=inputs["calendar"]["sha256"])
    if errors:
        raise ValueError("completed_calendar_rejected:" + ",".join(errors))
    verified = verify_market_session_confirmation_evidence(session, expected_file_sha256=inputs["session"]["sha256"],
                                                           actual_file_sha256=inputs["session"]["sha256"])
    if (not verified["evidence_integrity_verified"] or session.get("trade_date") != trade_date
            or session.get("status") != "MARKET_SESSION_CONFIRMED"):
        raise ValueError("session_confirmation_rejected")
    sync_age = (current - stamp(service["last_success"])).total_seconds()
    checks = {
        "correct_date": current.date().isoformat() == trade_date,
        "fixed_head": environment["git_head"] == approved_sha,
        "clean_worktree": environment["git_status"] == [],
        "environment": not any(environment[key] for key in ("workers", "locks", "blockers", "clock_or_power_events")),
        "no_reboot_or_service_restart": environment["service_process_id"] == baseline["service_process_id"]
            and environment["system_boot_time"] == baseline["system_boot_time"],
        "frozen_plan": plan == profile.frozen_plan(),
        "plan_file_anchor": ref(inputs["plan"]["path"])["sha256"] == inputs["plan"]["sha256"],
        "unused_outputs": environment["cleanup_incomplete_exists"] is False and environment["date_output_exists"] is False,
        "service_clock_contract": environment["service_running"] is True and service["leap"] == service["error"] == 0
            and 0 <= sync_age <= 3600,
        "source_unchanged": service["source"] == before_service["source"],
        "one_fresh_measurement": len(samples["samples"]) == 3 and 0 <= age <= 600
            and all(abs(row["offset_ms"]) <= 2000 for row in samples["samples"]),
        "real_dual_contracts": not errors and verified["evidence_integrity_verified"] is True
            and session["trade_date"] == trade_date and session["status"] == "MARKET_SESSION_CONFIRMED",
    }
    if set(checks) != CHECKS or not all(checks.values()):
        raise ValueError("minute_preflight_predicate_failed:" + ",".join(k for k, v in checks.items() if not v))
    return {"checks": checks, "plan_hash": plan["plan_hash"], "last_sync_age_seconds": sync_age,
            "last_sample_age_seconds": age, "service": service, "measurement": samples,
            "source_matches_system_sync_name": samples["source"].lower() == service["source"].split(",")[0].lower(),
            "hold_not_a_standalone_rejection": True,
            "first_wave_at": target_at if _protocol_contract(execution_contract) else f"{trade_date}T{audit.CLOCKS[0]}+08:00"}


def _query(output, trade_date, root, prepared_at, *, test=None, execution_contract="legacy_v1", controller_pid=None):
    command = (test["query_command"] if test else [shutil.which("powershell.exe") or "powershell.exe",
        "-NoProfile", "-NonInteractive", "-File", str(ENV_SCRIPT), "-OpsDirectory", str(output),
        "-OutputPath", str(output / "environment-native.json"), "-MinuteCapture", "-ProjectRoot", str(root),
        "-MinuteCaptureRoot", str(Path(root) / "overnight_quant/data/cache/minute_capture_audit"), "-TradeDate", trade_date])
    if not test and _protocol_contract(execution_contract):
        profile, _, _ = execution_profile(execution_contract)
        relative = getattr(profile, "AUDIT_RELATIVE", "overnight_quant/data/cache/minute_protocol_diagnostic")
        command += ["-ProtocolDiagnostic", "-ProtocolDiagnosticRoot", str(Path(root) / relative)]
        if hasattr(profile, "experiment_id"):
            command += ["-ProtocolDiagnosticWorkerVersion", profile.WORKER_VERSION]
        if controller_pid is not None:
            if type(controller_pid) is not int or controller_pid <= 0:
                raise ValueError("protocol_controller_pid_invalid")
            command += ["-AllowedControllerPid", str(controller_pid),
                "-ControllerPythonPath", sys.executable,
                "-ControllerBasePythonPath", getattr(sys, "_base_executable", None) or sys.executable,
                "-ControllerWorkingDirectory", os.getcwd()]
    started = (test["clock"]() if test else now()).isoformat()
    audit.write(output / "intent.json", {"created_at": started, "environment_observation_since": prepared_at, "trade_date": trade_date,
        "purpose": execution_profile(execution_contract)[1] if _protocol_contract(execution_contract) else VERSION,
        "sampling_authorized": False})
    result = run_probe_worker_process({}, test.get("query_timeout_ms", 10500) if test else 10500,
        worker_command=command, retain_streams=True, bounded_cleanup_ms=500)
    result["query_command"] = command
    result["query_started_at"] = started
    result["query_completed_at"] = (test["clock"]() if test else now()).isoformat()
    result["raw_output_encoding"] = "utf-8"
    for key in ("stdout", "stderr"):
        result[key + "_sha256"] = sha256(result.get(key, "").encode("utf-8")).hexdigest()
    audit.write(output / "environment-query.json", result)
    return ref(output / "environment-query.json")


def prepare(*, trade_date, approved_sha, plan_ref, output, inputs=None, environment_only=False,
            execution_contract="legacy_v1", target_at=None, _test=None):
    """Private injection is only used by the isolated harness, never a CLI option."""
    _, schema, verifier = execution_profile(execution_contract)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    clock = _test["clock"] if _test else now
    root = _test["root"] if _test else ROOT
    current = clock()
    result = audit.safe(schema=schema, verifier=verifier, status="MINUTE_PREFLIGHT_NO_GO",
                        test_only=bool(_test), code_sha=approved_sha, trade_date=trade_date, project_root=str(root),
                        started_at=current.isoformat(), checks={}, failure_reasons=[], inputs={"plan": plan_ref},
                        pm_approval_generated=False, capture_started=False, network_requests_made=0, ntp_measurements=0)
    if _protocol_contract(execution_contract):
        result["target_at"] = target_at
    try:
        _basic(trade_date, approved_sha, plan_ref, current, execution_contract=execution_contract, target_at=target_at)
        result["runtime"] = {"executable": sys.executable, "python_version": sys.version,
                             "packages": {key: installed_version(key) for key in ("mootdx", "tdxpy")}}
        query_ref = _query(output, trade_date, root, current.isoformat() if environment_only else
                           _query_record(inputs["before_environment"])["checked_at"], test=_test,
                           execution_contract=execution_contract)
        result["inputs"]["environment"] = query_ref
        if environment_only:
            _environment(query_ref, trade_date, approved_sha, root, clock(), bool(_test), execution_contract=execution_contract)
            result["status"] = "MINUTE_ENVIRONMENT_RECORDED_NOT_AUTHORIZED"
        else:
            result["inputs"].update(inputs)
            result["checked_at"] = clock().isoformat()
            result["evaluation"] = evaluate(result["inputs"], trade_date, approved_sha, root,
                stamp(result["checked_at"]), test_only=bool(_test), execution_contract=execution_contract, target_at=target_at)
            result["checks"] = result["evaluation"]["checks"]
            result["status"] = "MINUTE_PREFLIGHT_PASSED_NOT_AUTHORIZED"
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, OverflowError) as exc:
        result["failure_reasons"] = [f"{type(exc).__name__}:{exc}"]
    result.setdefault("checked_at", clock().isoformat())
    result["evidence_hash"] = audit.digest(result)
    path = audit.write(output / "preflight.json", result)
    audit.write(output / "file-anchor.json", ref(path))
    return result, path


def verify_file(path, expected_sha, *, trade_date, approved_sha, current=None, root=None,
                execution_contract="legacy_v1", target_at=None, _test_only=False):
    """Re-read anchored raw inputs and recompute; internal re-signing grants no authority."""
    _, schema, verifier = execution_profile(execution_contract)
    errors, evaluation = [], None
    try:
        value = audit.read_anchored(path, expected_sha)
        if value.get("schema") != schema or value.get("verifier") != verifier:
            raise ValueError("minute_preflight_schema_rejected_legacy_audit_only")
        if value.get("test_only") is not _test_only:
            raise ValueError("preflight_scope_mismatch")
        if (value.get("trade_date") != trade_date or value.get("code_sha") != approved_sha
                or value.get("project_root") != str(root or ROOT)):
            raise ValueError("preflight_date_code_or_root_mismatch")
        if _protocol_contract(execution_contract) and value.get("target_at") != target_at:
            raise ValueError("protocol_preflight_target_mismatch")
        if value.get("evidence_hash") != audit.digest({k: v for k, v in value.items() if k != "evidence_hash"}):
            raise ValueError("preflight_evidence_hash_mismatch")
        if value.get("status") != "MINUTE_PREFLIGHT_PASSED_NOT_AUTHORIZED":
            raise ValueError("preflight_not_passed")
        for key, safe_value in audit.safe().items():
            if value.get(key) != safe_value:
                raise ValueError("unsafe_preflight_output:" + key)
        checked = stamp(value["checked_at"])
        evaluation = evaluate(value["inputs"], trade_date, approved_sha, root or ROOT, checked,
                              test_only=_test_only, execution_contract=execution_contract, target_at=target_at)
        if value.get("evaluation") != evaluation or value.get("checks") != evaluation["checks"]:
            raise ValueError("preflight_derived_checks_mismatch")
        fresh_at(value, current or now())
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, OverflowError) as exc:
        errors.append(f"{type(exc).__name__}:{exc}")
    return audit.safe(status="MINUTE_PREFLIGHT_VERIFIED_NOT_AUTHORIZED" if not errors else "MINUTE_PREFLIGHT_INVALID",
                      integrity_verified=not errors, verifier=verifier, errors=errors, evaluation=evaluation,
                      test_only=_test_only, pm_approval_generated=False, capture_started=False)


def fresh_at(value, current):
    elapsed = (current - stamp(value["checked_at"])).total_seconds()
    for field, limit in (("last_sample_age_seconds", 600), ("last_sync_age_seconds", 3600)):
        age = value["evaluation"][field]
        if type(age) not in (int, float) or not math.isfinite(age) or elapsed < 0 or not 0 <= age + elapsed <= limit:
            raise ValueError("minute_preflight_clock_evidence_stale:" + field)
