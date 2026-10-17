"""Actual child-process regression tests; all responses are synthetic/test-only."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

from overnight_quant.data import complete_industry_audit as audit
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.scripts import run_complete_industry_audit_manual as manual

FIXTURES = Path(__file__).parent / "fixtures"
SHELLS = list(dict.fromkeys(p for p in (shutil.which("powershell.exe"), shutil.which("pwsh.exe")) if p))


def dump(path, value):
    if type(value) is dict and "result" in value:
        value = {**value, "result_json": json.dumps(value["result"])}
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No source network in operator tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 16, 14, 36, 50, tzinfo=CN_TZ)
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def case(tmp_path):
    if not SHELLS:
        pytest.skip("PowerShell is required for real boundary behavior")
    tmp_path = tmp_path / "synthetic ops with spaces"
    tmp_path.mkdir()
    clock = Clock()
    caps = dict.fromkeys(audit.candidate.BOARDS, 1)
    plan = audit.frozen_plan("2026-10-16", caps)
    dump(tmp_path / "caps.json", caps)
    (tmp_path / "launch_preflight.py").write_text("# synthetic/test_only pinned script\n", encoding="utf-8")
    intent = {"created_at": "2026-10-16T14:00:00+08:00", "trade_date": "2026-10-16", "fixed_head": "1" * 40,
              "paths": {"evidence": str(tmp_path / "audit.json")}}
    dump(tmp_path / "intent.json", intent)
    dump(tmp_path / "prerequisite-anchors.json", {"plan_hash": plan["plan_hash"]})
    environment = {"schema_version": "industry_audit_environment_v1", "test_only": True,
        "service_running": True, "blockers": [], "workers": [], "tasks": [], "locks": [],
        "clock_or_power_events": [], "journals": [], "checked_at": clock.now().isoformat(),
        "service_process_id": 42, "system_boot_time": "2026-10-15T04:00:00+08:00"}
    preflight = {"status": "PRE_LAUNCH_GO", "test_only": True, "checks": dict.fromkeys(manual.PREFLIGHT_CHECKS, True),
                 "checked_at": clock.now().isoformat(), "last_sample_age_seconds": 280, "last_sync_age_seconds": 300}
    business = {"test_only": True, "execution_ok": True, "candidate_batch_complete": True, "plan": plan,
                "data_ready": False, "hard_gate_authorized": False, "records": [], "candidates": [], "tickets": [], "orders": []}
    payloads = {
        "environment": {"result": environment, "write_result": True, "mode": "no_exit", "seed": "unset"},
        "preflight": {"result": preflight}, "business": {"result": business},
    }
    commands = {}
    for stage, data in payloads.items():
        path = tmp_path / (stage + ".payload.json")
        dump(path, data)
        output = tmp_path / ({"environment": "pre-launch-environment.json", "preflight": "launch-preflight.json", "business": "audit.json"}[stage])
        marker = tmp_path / (stage + ".called")
        commands[stage] = ([SHELLS[-1], "-NoProfile", "-NonInteractive", "-File", str(FIXTURES / "t31_operator_environment.ps1"),
                            "-Payload", str(path), "-OutputPath", str(output), "-Marker", str(marker)] if stage == "environment" else
                           [sys.executable, str(FIXTURES / "t31_operator_child.py"), str(path), str(output), str(marker)])
    context = manual._TestContext(commands, clock.now, clock.sleep, lambda: None)
    return tmp_path, payloads, context, clock


def execute(case):
    path, payloads, context, _ = case
    for stage, data in payloads.items():
        dump(path / (stage + ".payload.json"), data)
    result = manual._run_for_test(path, preflight_sha256=manual.digest(path / "launch_preflight.py"),
                                 page_limits_file=path / "caps.json", network=True, context=context)
    assert result["data_ready"] is result["hard_gate_authorized"] is False
    assert result["qualification"] == "0/3"
    assert result["records"] == result["candidates"] == result["tickets"] == result["orders"] == []
    assert result["test_only"] is True
    return result


@pytest.mark.parametrize("seed", ["unset", "0", "17"])
@pytest.mark.parametrize("shell", SHELLS or [None])
def test_success_uses_child_exit_not_stale_last_exit_code(case, seed, shell):
    path, payloads, context, clock = case
    payloads["environment"].update(seed=seed, stderr=True)
    commands = deepcopy(context.commands)
    commands["environment"][0] = shell
    result = execute((path, payloads, replace(context, commands=commands), clock))
    assert result["execution_ok"], result
    assert (path / "business.called").exists()
    receipt = manual.read(path / "operator-run-v1/environment.receipt.json")
    assert receipt["exit_code"] == 0
    assert (path / "operator-run-v1/environment.stderr.bin").read_bytes()
    assert manual.read(path / "pre-launch-environment.json") == payloads["environment"]["result"]
    assert manual.read(path / "pre-launch-environment.json")["checked_at"] == "2026-10-16T14:36:50+08:00"


@pytest.mark.parametrize("shell", SHELLS or [None])
def test_fixture_preserves_timestamp_text_without_host_timezone_conversion(case, shell):
    path, payloads, context, clock = case
    payloads["environment"]["result"].update(
        checked_at="2026-10-16T14:36:50.123456+03:00",
        system_boot_time="2026-10-15T04:00:00.654321-04:00")
    commands = deepcopy(context.commands)
    commands["environment"][0] = shell
    result = execute((path, payloads, replace(context, commands=commands), clock))
    assert result["execution_ok"], result
    assert manual.read(path / "pre-launch-environment.json") == payloads["environment"]["result"]
    raw = (path / "pre-launch-environment.json").read_text(encoding="utf-8")
    assert raw == json.dumps(payloads["environment"]["result"])


@pytest.mark.parametrize("bad", ["exit", "throw", "missing", "malformed", "blocker", "string_bool", "missing_array", "test_scope", "timestamp", "pid", "journal_type", "task_type"])
def test_environment_failures_are_logged_and_never_start_business(case, bad):
    path, payloads, _, _ = case
    data = payloads["environment"]
    if bad == "exit": data.update(mode="exit", exit_code=9)
    elif bad == "throw": data["mode"] = "throw"
    elif bad == "missing": data["write_result"] = False
    elif bad == "malformed": data["malformed"] = True
    elif bad == "blocker": data["result"]["blockers"] = ["proxy_port_not_listening"]
    elif bad == "string_bool": data["result"]["service_running"] = "true"
    elif bad == "missing_array": del data["result"]["blockers"]
    elif bad == "test_scope": data["result"]["test_only"] = False
    elif bad == "timestamp": data["result"]["checked_at"] = {"DateTime": "synthetic"}
    elif bad == "journal_type": data["result"]["journals"] = [None]
    elif bad == "task_type": data["result"]["tasks"] = ["Disabled"]
    else: data["result"]["service_process_id"] = "42"
    result = execute(case)
    assert not result["execution_ok"] and result["current_stage"] == "environment"
    assert not (path / "preflight.called").exists() and not (path / "business.called").exists()
    assert manual.read(path / "operator-run-v1/result.json")["error_code"]
    assert (path / "operator-run-v1/environment.stdout.bin").exists()
    assert (path / "operator-run-v1/environment.stderr.bin").exists()


@pytest.mark.parametrize("bad", ["exit", "missing_check", "string_check", "stale", "malformed"])
def test_preflight_is_semantic_not_merely_exit_zero(case, bad):
    path, payloads, _, _ = case
    data = payloads["preflight"]
    if bad == "exit": data["exit_code"] = 7
    elif bad == "missing_check": data["result"]["checks"].pop("real_dual_contracts")
    elif bad == "string_check": data["result"]["checks"]["real_dual_contracts"] = "true"
    elif bad == "stale": data["result"]["last_sample_age_seconds"] = 601
    else: data["malformed"] = True
    result = execute(case)
    assert not result["execution_ok"] and not (path / "business.called").exists()
    assert not (path / "business-execution-intent.json").exists()


def test_default_and_validate_only_cli_start_no_children(case, capsys, monkeypatch):
    path, _, _, _ = case
    def forbidden(*args, **kwargs):
        raise AssertionError("Default mode must not launch even a read-only helper")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    args = ["--ops-directory", str(path), "--preflight-sha256", manual.digest(path / "launch_preflight.py"),
            "--page-limits-file", str(path / "caps.json")]
    assert manual.main(args) == manual.main(args + ["--validate-only"]) == 0
    assert not (path / "operator-run-v1").exists()
    capsys.readouterr()


def test_repeat_never_overwrites_or_calls_business_again(case):
    path, _, _, _ = case
    assert execute(case)["execution_ok"]
    hashes = {p.name: manual.digest(p) for p in (path / "operator-run-v1").iterdir()}
    second = execute(case)
    assert not second["execution_ok"]
    assert hashes == {p.name: manual.digest(p) for p in (path / "operator-run-v1").iterdir()}


@pytest.mark.parametrize("bad", ["exit", "safe_flag", "missing"])
def test_business_failure_retains_attempt_and_no_qualification(case, bad):
    path, payloads, _, _ = case
    if bad == "exit": payloads["business"]["exit_code"] = 5
    elif bad == "safe_flag": payloads["business"]["result"]["data_ready"] = True
    else: payloads["business"]["write_result"] = False
    result = execute(case)
    assert not result["execution_ok"] and result["source_process_started"]
    assert result["status"] == "MANUAL_AUDIT_COLLECTION_FAILED"
    assert (path / "business-execution-intent.json").exists()


@pytest.mark.parametrize("bad", ["wrong_date", "after_latest", "git_changed", "hash_changed"])
def test_initial_rejection_has_no_child_calls(case, bad):
    path, payloads, context, clock = case
    if bad == "wrong_date": clock.value += timedelta(days=1)
    elif bad == "after_latest": clock.value = clock.value.replace(hour=14, minute=39, second=0, microsecond=1000)
    elif bad == "hash_changed":
        anchors = manual.read(path / "prerequisite-anchors.json");anchors["plan_hash"] = "0" * 64;dump(path / "prerequisite-anchors.json", anchors)
    else:
        def changed(): raise ValueError("synthetic changed Git state")
        context = replace(context, verify_git=changed)
    result = execute((path, payloads, context, clock))
    assert not result["execution_ok"] and not list(path.glob("*.called"))


def test_timeout_kills_child_tree_and_logs_no_retry(case):
    if os.name != "nt": pytest.skip("Uses the existing Windows process-tree cleanup")
    path, payloads, context, clock = case
    payloads["preflight"].update(sleep=30, spawn_child=True, child_pid_file=str(path / "grandchild.pid"))
    context = replace(context, stage_timeout=2)
    result = execute((path, payloads, context, clock))
    assert not result["execution_ok"] and not (path / "business.called").exists()
    receipt = manual.read(path / "operator-run-v1/preflight.receipt.json")
    assert receipt["timed_out"] and receipt["process_reaped"]
    pid = int((path / "grandchild.pid").read_text())
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if handle:
        code = wintypes.DWORD()
        try:
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            assert code.value != 259
        finally:
            kernel.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() in (87, 1168)


@pytest.mark.parametrize("shell", SHELLS or [None])
@pytest.mark.parametrize("mode", ["success", "disabled", "denied", "pending", "corrupt", "existing_output"])
def test_actual_environment_script_with_isolated_cmdlet_mocks(tmp_path, shell, mode):
    if shell is None: pytest.skip("PowerShell unavailable")
    ops = tmp_path / "ops with spaces"
    ops.mkdir()
    temp = tmp_path / "isolated temp"
    temp.mkdir()
    dump(ops / "intent.json", {"created_at": "2026-10-16T14:00:00+08:00"})
    output = ops / "environment.json"
    original = None
    if mode == "existing_output":
        output.write_bytes(b"existing immutable metadata\n")
        original = output.read_bytes()
    journal = None
    if mode in ("pending", "corrupt"):
        directory = temp / "http-start-state"
        directory.mkdir()
        host = "push2.eastmoney.com"
        journal = directory / (hashlib.sha256(host.encode()).hexdigest() + ".json")
        journal.write_text("{torn" if mode == "corrupt" else json.dumps({"version": "http_client_call_spacing_v1",
            "host_name": host, "pending": True, "last_call_end_seconds": 0}) + "\n", encoding="utf-8")
        original = journal.read_bytes()
    p = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(FIXTURES / "t31_environment_behavior_harness.ps1"),
        "-Script", str(manual.ENVIRONMENT_SCRIPT), "-OpsDirectory", str(ops), "-OutputPath", str(output),
        "-TempDirectory", str(temp), "-Mode", mode], capture_output=True, timeout=20)
    assert p.returncode == (0 if mode in ("success", "disabled") else 2), (p.stdout, p.stderr)
    if mode == "existing_output":
        assert output.read_bytes() == original
    else:
        result = manual.read(output)
        assert result["schema_version"] == "industry_audit_environment_v1"
        if mode in ("success", "disabled"):
            manual._verify_environment(result)
            assert result["blockers"] == []
            assert type(result["system_boot_time"]) is str
        else:
            assert result["blockers"]
    if journal:
        assert journal.read_bytes() == original
        copies = list(ops.glob("*-" + journal.name))
        assert len(copies) == 1 and copies[0].read_bytes() == original


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", [], {}])
def test_public_and_private_network_controls_are_strict_bool(case, value):
    path, _, context, _ = case
    args = dict(preflight_sha256=manual.digest(path / "launch_preflight.py"), page_limits_file=path / "caps.json", network=value)
    with pytest.raises(ValueError, match="OPERATOR_NETWORK_CONTROL_INVALID"):
        manual.run_manual(path, **args)
    with pytest.raises(ValueError, match="OPERATOR_NETWORK_CONTROL_INVALID"):
        manual._run_for_test(path, context=context, **args)
    assert not list(path.glob("*.called"))


@pytest.mark.parametrize("boundary", ["inclusive", "late"])
def test_business_admission_window_keeps_exact_boundary(case, boundary):
    path, payloads, context, clock = case
    clock.value = clock.value.replace(minute=39, second=0, microsecond=0 if boundary == "inclusive" else 1000)
    payloads["preflight"]["result"]["checked_at"] = clock.now().isoformat()
    result = execute(case)
    assert result["execution_ok"] is (boundary == "inclusive")
    assert (path / "business.called").exists() is (boundary == "inclusive")


def test_simultaneous_controllers_accept_exactly_one_attempt(case):
    from concurrent.futures import ThreadPoolExecutor
    path, payloads, context, _ = case
    for stage, data in payloads.items(): dump(path / (stage + ".payload.json"), data)
    def run():
        return manual._run_for_test(path, preflight_sha256=manual.digest(path / "launch_preflight.py"),
            page_limits_file=path / "caps.json", network=True, context=context)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(r["execution_ok"] for r in results) == 1
    assert sum(r["source_process_started"] for r in results) == 1
    assert len(list((path / "operator-run-v1").glob("business.intent.json"))) == 1


def test_launch_error_is_logged_without_claiming_a_source_process_started(case):
    path, payloads, context, clock = case
    commands = deepcopy(context.commands)
    commands["business"] = [str(path / "missing-python-executable")]
    result = execute((path, payloads, replace(context, commands=commands), clock))
    assert not result["execution_ok"]
    assert result["status"] == "NO_GO"
    assert result["source_attempt_accepted"] and result["source_process_started"] is False
    receipt = manual.read(path / "operator-run-v1/business.receipt.json")
    assert receipt["launch_error"] and receipt["exit_code"] is None
    assert (path / "operator-run-v1/business.stderr.bin").exists()


def test_initial_gate_failure_has_an_immutable_structured_receipt(case):
    path, _, _, clock = case
    clock.value += timedelta(days=1)
    result = execute(case)
    assert not result["execution_ok"] and result["error_code"] == "OPERATOR_AUDIT_WINDOW_MISSED"
    assert manual.read(path / "operator-run-v1/result.json") == result
    assert not list(path.glob("*.called"))
