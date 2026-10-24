"""Actual versioned entries, synthetic contracts and real local fixture children."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import base64
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from overnight_quant.data import minute_capture_preflight as pre
from overnight_quant.data import minute_capture_audit as store
from overnight_quant.data import minute_protocol_audit as audit
from overnight_quant.data import minute_protocol_contract as contract
from overnight_quant.data import minute_protocol_execution as execution
from overnight_quant.data import minute_local_prepare_seal as sealing
from overnight_quant.scripts import run_minute_capture_preflight as pre_cli
from overnight_quant.scripts import run_minute_protocol_diagnostic as cli
from overnight_quant.tests.test_phase108_minute_preflight import case, dump, DAY, FIXTURES, SHELLS
from overnight_quant.tests.test_phase114_minute_protocol_trace import FIXTURE, task

TARGET = f"{DAY}T14:49:55+08:00"


@pytest.fixture(autouse=True)
def external_requests_forbidden(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("source/DNS/NTP forbidden in offline protocol tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def protocol_case(case):
    case["plan_ref"] = dump(case["tmp"] / "protocol-plan.json", contract.frozen_plan())
    for path in (case["payload"], Path(case["inputs"]["before_environment"]["path"])):
        value = json.loads(path.read_bytes())
        environment = value.get("payload", value)
        environment.update(scope=contract.ENV_SCOPE,
            diagnostic_root=str(case["root"] / execution.AUDIT_RELATIVE))
        if "payload" in value:
            value["stdout"] = json.dumps({"ok": True, "payload": environment})
        dump(path, value)
    case["inputs"]["before_environment"] = pre.ref(case["inputs"]["before_environment"]["path"])
    case["test"].update(test_only=True, sleep=case["clock"].sleep,
        worker_command=[sys.executable, str(FIXTURE), "complete"])
    case["calls"] = []
    return case


def generate(c, monkeypatch, capsys):
    original = pre.prepare
    with monkeypatch.context() as patch:
        patch.setattr(pre, "prepare", lambda **kw: original(**kw, _test=c["test"]))
        args = ["--prepare", "--execution-contract", contract.EXECUTION_CONTRACT, "--date", DAY,
            "--target-at", TARGET, "--approved-code-sha", c["head"], "--plan", c["plan_ref"]["path"],
            "--expected-plan-sha256", c["plan_ref"]["sha256"], "--output-dir", str(c["tmp"] / "prepared")]
        for name, reference in c["inputs"].items():
            name = name.replace("_", "-")
            args += ["--" + name, reference["path"], "--" + name + "-sha256", reference["sha256"]]
        code = pre_cli.main(args)
    result = json.loads(capsys.readouterr().out)
    c["preflight"] = pre.ref(result["result_path"])
    return code, result


def authorize(c):
    config = dump(c["tmp"] / "config.json", {"SERVER": {"HQ": [["test-only", "59.36.5.11", 7709]]},
        "BESTIP": {"HQ": ["59.36.5.11", 7709]}})
    auth = {**audit.authorization_draft(c["head"]), "status": "PM_APPROVED_SINGLE_PROTOCOL_DIAGNOSTIC",
        "test_only": True, "trade_date": DAY, "target_at": TARGET,
        "approval_source": "SYNTHETIC TEST ONLY - NOT HUMAN AUTHORIZATION",
        "preflight": c["preflight"], "local_config": config, "plan_file_sha256": c["plan_ref"]["sha256"]}
    c["auth"] = dump(c["tmp"] / "synthetic-authorization.json", auth)
    return auth


def install_context(c, monkeypatch):
    original_capture, original_dispatch = execution.capture, execution.run_prepared_wave
    monkeypatch.setattr(execution, "capture", lambda *a, **kw: original_capture(*a, **kw, _test=c["test"]))
    def dispatch(*args, **kwargs):
        c["calls"].append(args[0])
        return original_dispatch(*args, **kwargs)
    monkeypatch.setattr(execution, "run_prepared_wave", dispatch)


def command(c):
    return ["--network", "--date", DAY, "--target-at", TARGET, "--plan", c["plan_ref"]["path"],
        "--expected-plan-sha256", c["plan_ref"]["sha256"], "--authorization", c["auth"]["path"],
        "--authorization-sha256", c["auth"]["sha256"]]


def safe(value):
    assert value["qualification"] == "0/3"
    assert all(value[k] is False for k in ("data_ready", "hard_gate_authorized", "sampling_authorized"))
    assert all(value[k] == [] for k in ("records", "research_ranking", "candidates", "tickets", "orders"))


@pytest.mark.parametrize("mode,expected", [("complete", 0), ("short", 2), ("timeout", 2)])
def test_actual_preflight_authorization_READY_GO_seal_and_two_replays(protocol_case, monkeypatch, capsys, mode, expected):
    c = protocol_case
    assert generate(c, monkeypatch, capsys)[0] == 0
    real_verify = pre.verify_file
    with monkeypatch.context() as patch:
        patch.setattr(pre, "verify_file", lambda *a, **kw: real_verify(*a, **{**kw,
            "root": c["root"], "current": c["clock"](), "_test_only": True}))
        assert pre_cli.main(["--verify", c["preflight"]["path"], "--expected-file-sha256", c["preflight"]["sha256"],
            "--date", DAY, "--target-at", TARGET, "--approved-code-sha", c["head"],
            "--execution-contract", contract.EXECUTION_CONTRACT]) == 0
        capsys.readouterr()
    authorize(c)
    c["test"]["worker_command"][-1] = mode
    install_context(c, monkeypatch)
    legacy_intent = c["cache"] / DAY / "intent.json"
    dump(legacy_intent, {"historical_failed_intent": True, "do_not_overwrite": True})
    old_hash = pre.ref(legacy_intent)["sha256"]
    monkeypatch.chdir(c["tmp"])  # The public entry must not inherit an unrelated caller cwd.
    assert cli.main(command(c)) == expected
    result = json.loads(capsys.readouterr().out)
    safe(result)
    assert result["evidence_integrity_verified"] and result["execution_materials_integrity_verified"], result
    assert len(c["calls"]) == 1
    path = Path(result["evidence_path"])
    evidence = json.loads(path.read_bytes())
    assert evidence["test_only"] and evidence["result"]["cleanup_complete"]
    launched = next(e for e in store.events_from_result(evidence["result"]) if e.get("kind") == "fixture_launch_validated")
    assert Path(launched["worker_cwd"]).resolve() == c["root"].resolve()
    assert evidence["result"]["tree_empty_observed"] and pre.ref(legacy_intent)["sha256"] == old_hash
    anchors = result["external_sha256"]
    originals = [path, path.parent / sealing.RECEIPT_NAME, path.parent / sealing.COMPLETION_NAME]
    before = [pre.ref(p)["sha256"] for p in originals]
    replays = []
    for i in range(2):
        replay = c["tmp"] / f"independent-replay-{i}.json"
        assert cli.main(["--verify", str(path), "--expected-file-sha256", anchors["evidence"],
            "--seal-receipt", str(originals[1]), "--expected-receipt-sha256", anchors["receipt"],
            "--seal-completion", str(originals[2]), "--expected-completion-sha256", anchors["completion"],
            "--replay-output", str(replay)]) == expected
        replayed = json.loads(capsys.readouterr().out)
        assert replayed["final_execution_result"] == result["final_execution_result"]
        replays.append(replay.read_bytes())
    assert replays[0] == replays[1] and before == [pre.ref(p)["sha256"] for p in originals]
    if mode == "complete":
        missing = execution.verify_files(path, anchors["evidence"])
        assert missing["evidence_integrity_verified"] and missing["final_execution_result"] == "PROTOCOL_DIAGNOSTIC_FAILED"
        other = json.loads(originals[1].read_bytes())
        other["batch_id"] = "different-batch"
        other["receipt_hash"] = store.digest({k: v for k, v in other.items() if k != "receipt_hash"})
        wrong = dump(c["tmp"] / "cross-batch-receipt.json", other)
        rejected = execution.verify_files(path, anchors["evidence"], receipt_path=wrong["path"],
            expected_receipt_sha256=wrong["sha256"], completion_path=originals[2], expected_completion_sha256=anchors["completion"])
        assert rejected["final_execution_result"] == "PROTOCOL_DIAGNOSTIC_FAILED"
    assert cli.main(command(c)) == 2
    assert len(c["calls"]) == 1  # Duplicate intent cannot start another child.


@pytest.mark.parametrize("mutation", ["date", "sha", "old-authorization", "draft", "shared-cleanup",
    "pending-shared", "existing-intent", "worker", "lock", "missing-preflight", "plan-anchor", "pending-protocol"])
def test_rejection_never_dispatches_capture_worker(protocol_case, monkeypatch, capsys, mutation):
    c = protocol_case
    assert generate(c, monkeypatch, capsys)[0] == 0
    auth = authorize(c)
    install_context(c, monkeypatch)
    args = command(c)
    if mutation == "date": args[2] = "2000-01-01"
    elif mutation == "plan-anchor": args[args.index("--expected-plan-sha256") + 1] = "0" * 64
    elif mutation == "sha":
        auth["code_sha"] = "b" * 40
        c["auth"] = dump(Path(c["auth"]["path"]), auth)
        args = command(c)
    elif mutation in ("old-authorization", "draft"):
        if mutation == "old-authorization": auth["schema"] = "mootdx_protocol_diagnostic_authorization_v1"
        else: auth["status"] = "DRAFT_NOT_AUTHORIZED"
        c["auth"] = dump(Path(c["auth"]["path"]), auth)
        args = command(c)
    elif mutation == "shared-cleanup": dump(c["cache"] / "cleanup-incomplete.json", {"preserve": True})
    elif mutation == "pending-shared": dump(c["cache"] / "2000-01-01/wave-0.started.json", {"pending": True})
    elif mutation == "existing-intent": dump(c["root"] / execution.AUDIT_RELATIVE / DAY / "intent.json", {"preserve": True})
    elif mutation == "pending-protocol": dump(c["root"] / execution.AUDIT_RELATIVE / "2000-01-01/request.started.json", {"preserve": True})
    elif mutation in ("worker", "lock"):
        payload = json.loads(c["payload"].read_bytes())
        payload["workers" if mutation == "worker" else "locks"] = [{"pid": 123, "test_only": True}]
        dump(c["payload"], payload)
    elif mutation == "missing-preflight":
        auth["preflight"]["path"] += ".missing"
        c["auth"] = dump(Path(c["auth"]["path"]), auth)
        args = command(c)
    assert cli.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    safe(result)
    assert c["calls"] == []


@pytest.mark.parametrize("failure", ["stale", "clock-error"])
def test_real_preflight_predicates_not_caller_flags(protocol_case, monkeypatch, capsys, failure):
    c = protocol_case
    baseline = json.loads(Path(c["inputs"]["before_environment"]["path"]).read_bytes())
    if failure == "stale":
        baseline["payload"]["checked_at"] = f"{DAY}T14:30:00+08:00"
        raw = Path(c["inputs"]["clock_raw"]["path"])
        lines = raw.read_text().splitlines()
        for i in range(2, 5):
            cells = lines[i].split(",")
            cells[2] = str(int(cells[2]) - 11 * 60 * 10000000)
            lines[i] = ",".join(cells)
        raw.write_text("\n".join(lines))
        c["inputs"]["clock_raw"] = pre.ref(raw)
    else:
        payload = json.loads(c["payload"].read_bytes())
        text = base64.b64decode(payload["clock_query"]["stdout_base64"]).decode().replace("Last Sync Error: 0", "Last Sync Error: 2")
        payload["clock_query"]["stdout_base64"] = base64.b64encode(text.encode()).decode()
        dump(c["payload"], payload)
    baseline["stdout"] = json.dumps({"ok": True, "payload": baseline["payload"]})
    c["inputs"]["before_environment"] = dump(Path(c["inputs"]["before_environment"]["path"]), baseline)
    code, value = generate(c, monkeypatch, capsys)
    assert code == 2 and value["status"] == "MINUTE_PREFLIGHT_NO_GO"
    assert any(("stale" if failure == "stale" else "explicit_failure") in e for e in value["failure_reasons"])
    authorize(c)
    install_context(c, monkeypatch)
    assert cli.main(command(c)) == 2 and c["calls"] == []


def test_output_failure_retains_partial_evidence_without_success(protocol_case, monkeypatch, capsys):
    c = protocol_case
    assert generate(c, monkeypatch, capsys)[0] == 0
    authorize(c)
    def broken(path, value):
        if Path(path).name == sealing.COMPLETION_NAME:
            raise OSError("synthetic completion write denied")
        return store.write(path, value)
    c["test"]["write"] = broken
    install_context(c, monkeypatch)
    assert cli.main(command(c)) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_FAILED"
    assert Path(result["evidence_path"]).exists() and "write_unconfirmed" in result["error"]
    assert len(c["calls"]) == 1
    assert cli.main(command(c)) == 2 and len(c["calls"]) == 1


def test_receipt_publication_cost_is_in_existing_seal_budget(protocol_case, monkeypatch, capsys):
    c = protocol_case
    assert generate(c, monkeypatch, capsys)[0] == 0
    authorize(c)
    def delayed(path, value):
        if Path(path).name == "request.json":
            time.sleep(3.05)  # Test-only post-worker publication, not network or work-budget relaxation.
        return store.write(path, value)
    c["test"]["write"] = delayed
    install_context(c, monkeypatch)
    assert cli.main(command(c)) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["evidence_integrity_verified"] and result["execution_materials_integrity_verified"], result
    assert result["capture_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED"
    assert result["seal_status"] == "SEAL_FAILED" and result["final_execution_result"] == "PROTOCOL_DIAGNOSTIC_FAILED"


@pytest.mark.parametrize("args", [[], ["--validate-only"], ["--network"]])
def test_default_or_missing_authorization_starts_no_child(monkeypatch, capsys, args):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("no child may start"))
    assert cli.main(args) == (2 if args == ["--network"] else 0)
    safe(json.loads(capsys.readouterr().out))


def test_internal_worker_cannot_skip_launch_preflight(tmp_path):
    value = task(tmp_path)
    value["test_only"] = False
    result = execution.run_prepared_wave(value, contract_profile=contract)
    assert not result["ok"] and result["go_sent_monotonic"] is None
    assert result["cleanup_complete"] and result["tree_empty_observed"]
    assert not any(e.get("stage") == "local_dependency_imports" for e in store.events_from_result(result))


def test_actual_query_command_selects_protocol_and_shared_roots(tmp_path, monkeypatch):
    commands = []
    def local_only(_task, _budget, **kw):
        commands.append(kw["worker_command"])
        return {"ok": False, "stdout": "synthetic refused", "stderr": "", "returncode": 2, "cleanup_complete": True}
    monkeypatch.setattr(pre, "run_probe_worker_process", local_only)
    pre._query(tmp_path, DAY, tmp_path, f"{DAY}T14:30:00+08:00",
        execution_contract=contract.EXECUTION_CONTRACT, controller_pid=6000)
    assert len(commands) == 1
    command = commands[0]
    assert "-ProtocolDiagnostic" in command and "-MinuteCapture" in command
    assert command[command.index("-AllowedControllerPid") + 1] == "6000"
    assert command[command.index("-MinuteCaptureRoot") + 1] == str(tmp_path / execution.SHARED_RELATIVE)
    assert command[command.index("-ProtocolDiagnosticRoot") + 1] == str(tmp_path / execution.AUDIT_RELATIVE)


@pytest.mark.parametrize("label,allowed", [("14:51:00", True), ("14:51:00.000001", False),
                                           ("14:51:01", False), ("14:51:03", False)])
def test_whole_remaining_work_cleanup_and_seal_budget_before_query(protocol_case, monkeypatch, label, allowed):
    c = protocol_case
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("budget validation must start no process"))
    kwargs = {"execution_contract": contract.EXECUTION_CONTRACT, "target_at": f"{DAY}T{label}+08:00"}
    if allowed:
        assert pre._basic(DAY, c["head"], c["plan_ref"], c["clock"](), **kwargs) == contract.frozen_plan()
    else:
        with pytest.raises(ValueError, match="absolute_budget_invalid"):
            pre._basic(DAY, c["head"], c["plan_ref"], c["clock"](), **kwargs)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("mode", ["protocol-worker", "protocol-cli", "protocol-task", "unrelated", "own-controller", "controller-worker"])
def test_actual_powershell_recognizes_protocol_processes_and_tasks(tmp_path, shell, mode):
    ops, temp = tmp_path / "ops", tmp_path / "temp"
    ops.mkdir(); temp.mkdir()
    dump(ops / "intent.json", {"created_at": pre.now().isoformat(), "environment_observation_since": pre.now().isoformat()})
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(FIXTURES / "t31_environment_behavior_harness.ps1"),
        "-Script", str(pre.ENV_SCRIPT), "-OpsDirectory", str(ops), "-OutputPath", str(ops / "environment.json"),
        "-TempDirectory", str(temp), "-Mode", {"own-controller": "protocol-cli", "controller-worker": "protocol-controller-worker"}.get(mode, mode),
        "-AllowedControllerPid", "6000" if mode in ("own-controller", "controller-worker") else "0",
        "-MinuteCapture", "-ProtocolDiagnostic", "-ProjectRoot", str(tmp_path),
        "-MinuteCaptureRoot", str(tmp_path / "shared"), "-ProtocolDiagnosticRoot", str(tmp_path / "diagnostic"),
        "-TradeDate", DAY], capture_output=True, timeout=20)
    assert result.returncode == (0 if mode in ("unrelated", "own-controller") else 2), (result.stdout, result.stderr)
    record = json.loads((ops / "environment.json").read_text(encoding="utf-8-sig"))
    assert record["scope"] == contract.ENV_SCOPE
    if mode in ("protocol-worker", "protocol-cli"):
        assert {v["ProcessId"] for v in record["workers"]} == {6000, 6001}
    elif mode == "protocol-task":
        assert any("Active project task" in e for e in record["blockers"])
    elif mode == "controller-worker":
        assert {v["ProcessId"] for v in record["workers"]} == {6010, 6011}
