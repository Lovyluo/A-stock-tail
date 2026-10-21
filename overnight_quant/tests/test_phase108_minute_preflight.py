"""Synthetic-only CLI -> verifier -> capture CLI -> real fixture worker."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys

import pytest

from overnight_quant.data import minute_capture_audit as audit
from overnight_quant.data import minute_capture_preflight as pre
from overnight_quant.scripts import run_minute_capture_preflight as cli
from overnight_quant.scripts import run_minute_capture_audit as capture_cli
from overnight_quant.tests import test_phase92_calendar_session_contract as session_fixture
from overnight_quant.tests.test_phase107_minute_capture_audit import Clock, context, assert_safe

DAY = "2026-10-21"  # synthetic, not a trading-day assertion
FIXTURES = Path(__file__).parent / "fixtures"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return pre.ref(path)


def service_text(error=0):
    return ("Leap Indicator: 0\nLast Successful Sync Time: 2026/10/21 14:30:00\n"
            f"Source: time.windows.com,0x9\nLast Sync Error: {error}\nState Machine: 1 (Hold)\n")


def environment(root, head, at):
    return {"schema_version": "industry_audit_environment_v1", "scope": pre.ENV_SCOPE,
        "test_only": True, "trade_date": DAY, "project_root": str(root),
        "capture_root": str(root / "overnight_quant/data/cache/minute_capture_audit"),
        "service_running": True, "service_process_id": 42, "system_boot_time": f"{DAY}T04:00:00+08:00",
        "timezone": "China Standard Time", "checked_at": f"{DAY}T{at}+08:00", "git_head": head,
        "git_status": [], "workers": [], "tasks": [], "locks": [], "clock_or_power_events": [],
        "journals": [], "blockers": [], "cleanup_incomplete_exists": False, "date_output_exists": False,
        "clock_query": {"command": ["w32tm", "/query", "/status", "/verbose"], "exit_code": 0,
                        "stdout_base64": base64.b64encode(service_text().encode()).decode(), "stderr_base64": ""}}


@pytest.fixture(autouse=True)
def no_source_or_ntp(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("external network forbidden in synthetic harness")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path / "synthetic repository"
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.name", "Synthetic Test"),
                 ("config", "user.email", "test@example.invalid")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / ".gitignore").write_text("overnight_quant/data/cache/\n")
    subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "synthetic test-only identity"], check=True, capture_output=True)
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    cache, clock = root / "overnight_quant/data/cache/minute_capture_audit", Clock("14:48:00")
    monkeypatch.setattr(session_fixture, "TRADE_DATE", DAY)
    calendar, session = session_fixture._calendar(), session_fixture._confirmation()
    calendar["test_only"] = session["test_only"] = True
    session["market_session_confirmation_evidence_hash"] = session_fixture.compute_market_session_confirmation_hash(session)
    inputs = {"calendar": dump(tmp_path / "calendar.json", calendar), "session": dump(tmp_path / "session.json", session)}
    baseline = environment(root, head, "14:44:00")
    inputs["before_environment"] = dump(tmp_path / "baseline.json", {"ok": True, "returncode": 0,
        "cleanup_complete": True, "payload": baseline, "stdout": json.dumps({"ok": True, "payload": baseline})})
    origin = datetime(1601, 1, 1, tzinfo=timezone.utc)
    lines = ["Tracking time.windows.com [20.189.79.72:123].", "RdtscStart,RdtscEnd,FileTime,RoundtripDelay,NtpOffset"]
    for i in range(3):
        delta = datetime.fromisoformat(f"{DAY}T14:45:0{i * 2}+08:00") - origin
        ticks = (delta.days * 86400 + delta.seconds) * 10000000 + delta.microseconds * 10
        lines.append(f"{100+i*10},{105+i*10},{ticks},+00.1000000,-00.0200000")
    raw = tmp_path / "clock-raw.bin"
    raw.write_bytes("\n".join(lines).encode())
    inputs["clock_raw"] = pre.ref(raw)
    payload = tmp_path / "environment.json"
    dump(payload, environment(root, head, "14:47:00"))
    test = {"root": root, "clock": clock, "query_command": [sys.executable,
            str(FIXTURES / "minute_preflight_query_fixture.py"), str(payload), "ok"]}
    return {"root": root, "head": head, "cache": cache, "clock": clock, "inputs": inputs,
        "plan_ref": dump(cache / "plan.json", audit.frozen_plan()), "test": test, "payload": payload, "tmp": tmp_path}


def generate(case, output="preflight", **changes):
    args = dict(trade_date=DAY, approved_sha=case["head"], plan_ref=case["plan_ref"],
                output=case["tmp"] / output, inputs=case["inputs"], _test=case["test"])
    args.update(changes)
    return pre.prepare(**args)


def verify(case, path):
    return pre.verify_file(path, pre.ref(path)["sha256"], trade_date=DAY, approved_sha=case["head"],
                           current=case["clock"](), root=case["root"], _test_only=True)


def test_actual_entries_end_to_end_twenty_fixture_requests(case, monkeypatch, capsys):
    real_prepare = pre.prepare
    monkeypatch.setattr(pre, "prepare", lambda **kwargs: real_prepare(**kwargs, _test=case["test"]))
    args = ["--prepare", "--date", DAY, "--approved-code-sha", case["head"], "--plan", case["plan_ref"]["path"],
            "--expected-plan-sha256", case["plan_ref"]["sha256"], "--output-dir", str(case["tmp"] / "cli-preflight")]
    for key, reference in case["inputs"].items():
        name = key.replace("_", "-")
        args.extend(["--" + name, reference["path"], "--" + name + "-sha256", reference["sha256"]])
    assert cli.main(args) == 0
    value = json.loads(capsys.readouterr().out)
    path = Path(value["result_path"])
    checked = verify(case, path)
    assert checked["integrity_verified"], checked
    assert not checked["sampling_authorized"] and not checked["pm_approval_generated"]
    real_verify = pre.verify_file
    monkeypatch.setattr(pre, "verify_file", lambda *a, **k: real_verify(*a, **{**k, "current": case["clock"](),
        "root": case["root"], "_test_only": True}))
    assert cli.main(["--verify", str(path), "--expected-file-sha256", pre.ref(path)["sha256"],
                     "--date", DAY, "--approved-code-sha", case["head"]]) == 0
    capsys.readouterr()
    auth = {"schema": pre.AUTHORIZATION_VERSION, "status": "PM_APPROVED_SINGLE_AUDIT", "test_only": True,
            "trade_date": DAY, "code_sha": case["head"], "plan_hash": audit.frozen_plan()["plan_hash"],
            "single_use": True, "preflight": pre.ref(path)}
    authorization = dump(case["tmp"] / "synthetic-pm-authorization.json", auth)
    monkeypatch.setattr(audit, "ROOT", case["root"])
    original_capture, real_process, calls = audit.capture, audit.run_probe_worker_process, []
    def tracked(task, *a, **kw):
        calls.append(task["wave"])
        return real_process(task, *a, **kw)
    monkeypatch.setattr(audit, "run_probe_worker_process", tracked)
    ctx = {**context(clock=case["clock"]), "validate_authorization": True}
    monkeypatch.setattr(audit, "capture", lambda *a, **k: original_capture(*a, **k, audit_root=case["cache"], _test=ctx))
    assert capture_cli.main(["--network", "--date", DAY, "--plan", case["plan_ref"]["path"],
        "--expected-plan-sha256", case["plan_ref"]["sha256"], "--authorization", authorization["path"],
        "--authorization-sha256", authorization["sha256"]]) == 0
    output = json.loads(capsys.readouterr().out)
    assert calls == [0, 1, 2, 3] and output["summary"]["requests_attempted"] == 20
    assert_safe(output)
    evidence = json.loads(Path(output["evidence_path"]).read_text())
    assert evidence["test_only"] and all(w["result"]["cleanup_complete"] for w in evidence["waves"])
    assert audit.verify(evidence, "a" * 64, "a" * 64)["integrity_verified"]


@pytest.mark.parametrize("args", [[], ["--validate-only"]])
def test_default_zero_children_and_network(monkeypatch, capsys, args):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("no child allowed"))
    assert cli.main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["child_processes_started"] == value["network_requests_made"] == value["ntp_measurements"] == 0
    assert_safe(value)


@pytest.mark.parametrize("name", ["date", "sha", "industry_plan", "plan_anchor"])
def test_wrong_bindings_reject_before_system_query(case, monkeypatch, name):
    monkeypatch.setattr(pre, "_query", lambda *a, **k: pytest.fail("query forbidden"))
    changes = {"trade_date": "2026-10-19"} if name == "date" else {"approved_sha": "old-sha"} if name == "sha" else {}
    if name == "industry_plan": case["plan_ref"] = dump(case["tmp"] / "old-plan.json", {"maximum_requests": 13})
    if name == "plan_anchor": case["plan_ref"]["sha256"] = "f" * 64
    result, _ = generate(case, **changes)
    assert result["status"] == "MINUTE_PREFLIGHT_NO_GO" and result["failure_reasons"]
    assert_safe(result)


@pytest.mark.parametrize("change", ["worker", "cleanup", "occupied", "clock_error", "clock_parse", "dirty",
    "wrong_head", "service", "task", "pending", "power", "missing_calendar", "calendar_anchor", "old_session",
    "stale_clock", "offset", "two_samples", "raw_clock_hash", "restart"])
def test_failures_cannot_reach_capture(case, monkeypatch, capsys, change):
    env = json.loads(case["payload"].read_text())
    changes = {"worker": ("workers", [{"ProcessId": 4, "CommandLine": "-m overnight_quant.data.minute_capture_worker"}]),
        "cleanup": ("cleanup_incomplete_exists", True), "occupied": ("date_output_exists", True),
        "dirty": ("git_status", [" M unexpected.py"]), "wrong_head": ("git_head", "b" * 40),
        "service": ("service_running", False), "task": ("tasks", [{"Enabled": True, "State": "Ready"}]),
        "pending": ("journals", [{"valid": True, "pending": True}]), "power": ("clock_or_power_events", [{"Id": 42}]),
        "restart": ("service_process_id", 43)}
    if change in changes: env[changes[change][0]] = changes[change][1]
    elif change in ("clock_error", "clock_parse"):
        env["clock_query"]["stdout_base64"] = base64.b64encode((service_text(2) if change == "clock_error" else "bad").encode()).decode()
    elif change == "missing_calendar": case["inputs"]["calendar"]["path"] += "-missing"
    elif change == "calendar_anchor": case["inputs"]["calendar"]["sha256"] = "f" * 64
    elif change == "old_session":
        value = json.loads(Path(case["inputs"]["session"]["path"]).read_text())
        value["trade_date"] = "2026-10-19"
        case["inputs"]["session"] = dump(case["tmp"] / "old-session.json", value)
    else:
        raw = Path(case["inputs"]["clock_raw"]["path"])
        value = raw.read_text()
        if change == "stale_clock": case["clock"].base += timedelta(minutes=8)
        elif change == "offset": raw.write_text(value.replace("-00.0200000", "-03.0000000"))
        elif change == "two_samples": raw.write_text("\n".join(value.splitlines()[:-1]))
        elif change == "raw_clock_hash": raw.write_text(value + " changed")
        if change != "raw_clock_hash": case["inputs"]["clock_raw"] = pre.ref(raw)
    dump(case["payload"], env)
    monkeypatch.setattr(audit, "run_probe_worker_process", lambda *a, **k: pytest.fail("capture worker forbidden"))
    result, path = generate(case)
    assert result["status"] == "MINUTE_PREFLIGHT_NO_GO", result
    assert not verify(case, path)["integrity_verified"] and not (case["cache"] / DAY).exists()
    assert_safe(result)
    auth = {"schema": pre.AUTHORIZATION_VERSION, "status": "PM_APPROVED_SINGLE_AUDIT", "test_only": True,
        "trade_date": DAY, "code_sha": case["head"], "single_use": True,
        "plan_hash": audit.frozen_plan()["plan_hash"], "preflight": pre.ref(path)}
    authorization = dump(case["tmp"] / "synthetic-pm-authorization.json", auth)
    monkeypatch.setattr(audit, "ROOT", case["root"])
    real_capture = audit.capture
    monkeypatch.setattr(audit, "capture", lambda *a, **k: real_capture(*a, **k, audit_root=case["cache"],
        _test={**context(clock=case["clock"]), "validate_authorization": True}))
    assert capture_cli.main(["--network", "--date", DAY, "--plan", case["plan_ref"]["path"],
        "--expected-plan-sha256", case["plan_ref"]["sha256"], "--authorization", authorization["path"],
        "--authorization-sha256", authorization["sha256"]]) == 2
    assert_safe(json.loads(capsys.readouterr().out))
    assert not (case["cache"] / DAY).exists()


@pytest.mark.parametrize("mode", ["denied", "invalid", "missing", "exit", "timeout"])
def test_real_query_process_failure_receipt_and_reaping(case, mode):
    case["test"]["query_command"][-1] = mode
    if mode == "timeout": case["test"]["query_timeout_ms"] = 1500
    result, path = generate(case)
    assert result["status"] == "MINUTE_PREFLIGHT_NO_GO" and result["failure_reasons"]
    receipt = json.loads((path.parent / "environment-query.json").read_text())
    assert receipt["returncode"] is not None and receipt["cleanup_complete"]
    assert receipt["pid"] > 0 and "stdout" in receipt and "stderr" in receipt
    if mode == "timeout":
        assert receipt["request_timed_out"] and receipt["worker_terminated"]
        if os.name == "nt":
            listing = subprocess.check_output(["tasklist", "/FI", f"PID eq {receipt['pid']}", "/FO", "CSV"], text=True)
            assert f'"{receipt["pid"]}"' not in listing
    assert not (case["cache"] / DAY).exists()


def test_existing_output_is_immutable(case, monkeypatch):
    _, path = generate(case)
    original = path.read_bytes()
    monkeypatch.setattr(pre, "_query", lambda *a, **k: pytest.fail("no repeated query"))
    with pytest.raises(FileExistsError): generate(case)
    assert path.read_bytes() == original


def test_resigned_flags_old_schemas_and_test_scope_closed(case):
    value, path = generate(case)
    assert verify(case, path)["integrity_verified"]
    for alter in (lambda x: x["checks"].update(environment=False),
                  lambda x: x["evaluation"]["service"].update(error=2),
                  lambda x: x.update(sampling_authorized=True), lambda x: x.update(schema="industry_preflight_v1")):
        modified = deepcopy(value)
        alter(modified)
        modified["evidence_hash"] = audit.digest({k: v for k, v in modified.items() if k != "evidence_hash"})
        reference = dump(case["tmp"] / "resigned.json", modified)
        assert not verify(case, Path(reference["path"]))["integrity_verified"]
    assert not pre.verify_file(path, pre.ref(path)["sha256"], trade_date=DAY,
        approved_sha=case["head"], root=case["root"], current=case["clock"]())["integrity_verified"]
    assert not pre.verify_file(path, "f" * 64, trade_date=DAY, approved_sha=case["head"])["integrity_verified"]


def test_old_manual_authorization_refused_without_intent(case, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", case["root"])
    with pytest.raises(ValueError, match="date_specific_PM_authorization"):
        audit.capture(audit.frozen_plan(), DAY, authorization={"schema": "minute_capture_manual_authorization_v1"},
            audit_root=case["cache"], _test={**context(clock=case["clock"]), "validate_authorization": True})
    assert not (case["cache"] / DAY).exists()


SHELLS = list(dict.fromkeys(p for p in (shutil.which("powershell.exe"), shutil.which("pwsh.exe")) if p))


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("mode", ["unrelated", "worker", "cli", "quoted-cli", "denied", "disabled",
                                  "module-cli", "module-cli-compact", "minute-task", "minute-task-disabled"])
def test_actual_powershell_minute_inventory_and_child_scope(tmp_path, shell, mode):
    ops, temp = tmp_path / "synthetic ops", tmp_path / "synthetic temp"
    ops.mkdir(); temp.mkdir()
    dump(ops / "intent.json", {"created_at": datetime.now(audit.CN).isoformat(), "test_only": True,
                              "environment_observation_since": datetime.now(audit.CN).isoformat()})
    command = [shell, "-NoProfile", "-NonInteractive", "-File", str(FIXTURES / "t31_environment_behavior_harness.ps1"),
        "-Script", str(pre.ENV_SCRIPT), "-OpsDirectory", str(ops), "-OutputPath", str(ops / "environment.json"),
        "-TempDirectory", str(temp), "-Mode", mode, "-MinuteCapture", "-ProjectRoot", str(tmp_path),
        "-MinuteCaptureRoot", str(tmp_path / "minute-capture"), "-TradeDate", DAY]
    result = subprocess.run(command, capture_output=True, timeout=20)
    expected = 2 if mode in ("worker", "cli", "quoted-cli", "module-cli", "module-cli-compact", "denied", "minute-task") else 0
    assert result.returncode == expected, (result.stdout, result.stderr)
    record = json.loads((ops / "environment.json").read_text(encoding="utf-8-sig"))
    if mode in ("worker", "cli", "quoted-cli", "module-cli", "module-cli-compact"):
        assert {r["ProcessId"] for r in record["workers"]} == {6000, 6001}
    elif mode == "denied":
        assert any("denied" in x for x in record["blockers"])
    elif mode == "minute-task":
        assert any("Active project task" in x for x in record["blockers"])
    else:
        assert record["workers"] == [] and record["scope"] == pre.ENV_SCOPE
        assert pre.clock_status(record)["error"] == 0
        if mode == "disabled": assert record["tasks"][0]["Enabled"] is False


def test_cli_rejected_preflight_zero_capture_dispatch_and_no_intent(case, monkeypatch, capsys):
    value, path = generate(case)
    assert value["status"] == "MINUTE_PREFLIGHT_PASSED_NOT_AUTHORIZED"
    case["clock"].base += timedelta(minutes=8)
    auth = {"schema": pre.AUTHORIZATION_VERSION, "status": "PM_APPROVED_SINGLE_AUDIT", "test_only": True,
        "trade_date": DAY, "code_sha": case["head"], "single_use": True,
        "plan_hash": audit.frozen_plan()["plan_hash"], "preflight": pre.ref(path)}
    authorization = dump(case["tmp"] / "synthetic-pm-authorization.json", auth)
    monkeypatch.setattr(audit, "ROOT", case["root"])
    monkeypatch.setattr(audit, "run_probe_worker_process", lambda *a, **k: pytest.fail("capture forbidden"))
    real = audit.capture
    monkeypatch.setattr(audit, "capture", lambda *a, **k: real(*a, **k, audit_root=case["cache"],
        _test={**context(clock=case["clock"]), "validate_authorization": True}))
    assert capture_cli.main(["--network", "--date", DAY, "--plan", case["plan_ref"]["path"],
        "--expected-plan-sha256", case["plan_ref"]["sha256"], "--authorization", authorization["path"],
        "--authorization-sha256", authorization["sha256"]]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "MINUTE_CAPTURE_REQUEST_REJECTED" and not (case["cache"] / DAY).exists()
    assert_safe(result)


def test_environment_only_baseline_is_not_full_preflight(case):
    result, path = generate(case, environment_only=True)
    assert result["status"] == "MINUTE_ENVIRONMENT_RECORDED_NOT_AUTHORIZED"
    assert result["checks"] == {} and not verify(case, path)["integrity_verified"]
    assert_safe(result)


@pytest.mark.parametrize("age,allowed", [(600, True), (600.001, False)])
def test_existing_clock_freshness_exact_boundary(case, age, allowed):
    value, _ = generate(case)
    checked = pre.stamp(value["checked_at"])
    sample_age = value["evaluation"]["last_sample_age_seconds"]
    when = checked + timedelta(seconds=age - sample_age)
    if allowed: pre.fresh_at(value, when)
    else:
        with pytest.raises(ValueError, match="clock_evidence_stale"): pre.fresh_at(value, when)


def test_explicit_service_failure_does_not_read_ntp_to_override(case, monkeypatch):
    env = json.loads(case["payload"].read_text())
    env["clock_query"]["stdout_base64"] = base64.b64encode(service_text(2).encode()).decode()
    dump(case["payload"], env)
    monkeypatch.setattr(pre, "clock_samples", lambda *a: pytest.fail("do not mask service failure"))
    value, _ = generate(case)
    assert "clock_service_explicit_failure" in value["failure_reasons"][0]


def test_inputs_permissions_fail_closed_with_specific_cli_error(case, monkeypatch, capsys):
    real = pre.prepare
    monkeypatch.setattr(pre, "prepare", lambda **kw: real(**kw, _test=case["test"]))
    def denied(*a, **k): raise PermissionError("synthetic output directory permission denied")
    monkeypatch.setattr(Path, "mkdir", denied)
    assert cli.main(["--inspect-environment", "--date", DAY, "--approved-code-sha", case["head"],
        "--plan", case["plan_ref"]["path"], "--expected-plan-sha256", case["plan_ref"]["sha256"],
        "--output-dir", str(case["tmp"] / "denied")]) == 2
    assert "PermissionError" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("missing", [False, True])
def test_client_runtime_mismatch_cannot_start_system_query(case, monkeypatch, missing):
    def version(_):
        if missing: raise pre.PackageNotFoundError("synthetic package missing")
        return "unapproved-version"
    monkeypatch.setattr(pre, "installed_version", version)
    monkeypatch.setattr(pre, "_query", lambda *a, **k: pytest.fail("query forbidden"))
    result, _ = generate(case)
    assert result["status"] == "MINUTE_PREFLIGHT_NO_GO"
    assert "frozen_client_" in result["failure_reasons"][0]
