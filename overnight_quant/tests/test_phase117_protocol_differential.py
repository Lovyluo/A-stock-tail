"""A-D experiments are synthetic local-child tests, never source evidence."""
from copy import deepcopy
from datetime import timedelta
import base64
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from overnight_quant.data import minute_capture_audit as store
from overnight_quant.data import minute_capture_preflight as pre
from overnight_quant.data import minute_protocol_audit as audit
from overnight_quant.data import minute_protocol_contract as old
from overnight_quant.data import minute_protocol_execution as single
from overnight_quant.data import minute_protocol_differential as matrix
from overnight_quant.data import minute_protocol_differential_contract as contract
from overnight_quant.scripts import run_minute_capture_preflight as pre_cli
from overnight_quant.scripts import run_minute_protocol_diagnostic as cli
from overnight_quant.tests.test_phase108_minute_preflight import case, dump, DAY, FIXTURES, SHELLS
from overnight_quant.tests.test_phase114_minute_protocol_trace import FIXTURE


@pytest.fixture(autouse=True)
def no_external_sources(monkeypatch):
    def denied(*a, **kw):
        pytest.fail("external source/DNS/NTP forbidden in differential offline tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def prepare(c, monkeypatch, capsys):
    c["clock"].base = pre.stamp(f"{DAY}T14:46:30+08:00")
    c["clock"].start = __import__('time').monotonic()
    c["matrix_plan"] = dump(c["tmp"] / "matrix-plan.json", contract.frozen_plan())
    config = dump(c["tmp"] / "config.json", {"SERVER": {"HQ": [["test-only", "59.36.5.11", 7709]]},
        "BESTIP": {"HQ": ["59.36.5.11", 7709]}})
    outer = {**matrix.draft(c["head"]), "status": "PM_APPROVED_SINGLE_DIFFERENTIAL_MATRIX",
        "test_only": True, "trade_date": DAY, "approval_source": "SYNTHETIC TEST ONLY NOT PM APPROVAL",
        "matrix_authorization_id": "synthetic-matrix-identity", "plan_file_sha256": c["matrix_plan"]["sha256"]}
    commands = {}
    for key, *_ in contract.EXPERIMENTS:
        profile = contract.ExperimentProfile(key)
        plan_ref = dump(c["tmp"] / (key + "-plan.json"), profile.frozen_plan())
        inputs = deepcopy(c["inputs"])
        payload = json.loads(c["payload"].read_bytes())
        payload.update(scope=profile.ENV_SCOPE, diagnostic_root=str(c["root"] / profile.AUDIT_RELATIVE),
                       checked_at=c["clock"]().isoformat())
        payload_path = c["tmp"] / (key + "-environment.json")
        dump(payload_path, payload)
        baseline = pre.read(inputs["before_environment"])
        baseline["payload"].update(scope=profile.ENV_SCOPE, diagnostic_root=str(c["root"] / profile.AUDIT_RELATIVE))
        baseline["stdout"] = json.dumps({"ok": True, "payload": baseline["payload"]})
        inputs["before_environment"] = dump(c["tmp"] / (key + "-baseline.json"), baseline)
        test = {**c["test"], "query_command": [str(x).replace(str(c["payload"]), str(payload_path))
                                              for x in c["test"]["query_command"]]}
        commands[key] = test["query_command"]
        original = pre.prepare
        with monkeypatch.context() as patch:
            patch.setattr(pre, "prepare", lambda **kw: original(**kw, _test=test))
            args = ["--prepare", "--execution-contract", profile.EXECUTION_CONTRACT, "--date", DAY,
                "--target-at", f"{DAY}T{profile.target_clock}+08:00", "--approved-code-sha", c["head"],
                "--plan", plan_ref["path"], "--expected-plan-sha256", plan_ref["sha256"],
                "--output-dir", str(c["tmp"] / (key + "-preflight"))]
            for name, ref in inputs.items():
                name = name.replace("_", "-")
                args += ["--" + name, ref["path"], "--" + name + "-sha256", ref["sha256"]]
            assert pre_cli.main(args) == 0, capsys.readouterr()
            output = json.loads(capsys.readouterr().out)
        auth = {**audit.authorization_draft(c["head"], profile=profile), "status": profile.APPROVAL_STATUS,
            "test_only": True, "trade_date": DAY, "target_at": f"{DAY}T{profile.target_clock}+08:00",
            "experiment_id": key, "matrix_plan_hash": contract.frozen_plan()["plan_hash"],
            "matrix_authorization_id": outer["matrix_authorization_id"], "approval_source": outer["approval_source"],
            "preflight": pre.ref(output["result_path"]), "local_config": config,
            "plan_file_sha256": plan_ref["sha256"]}
        outer["experiments"][key] = {"plan": plan_ref,
            "authorization": dump(c["tmp"] / (key + "-authorization.json"), auth)}
    c["outer"] = outer
    c["authorization"] = dump(c["tmp"] / "matrix-authorization.json", outer)
    c["context"] = {**c["test"], "test_only": True, "sleep": c["clock"].sleep,
        "worker_modes": {key: "complete" for key, *_ in contract.EXPERIMENTS},
        "worker_command_base": [sys.executable, str(FIXTURE)], "query_commands": commands}
    c["calls"] = []
    real_capture, real_dispatch = matrix.capture, single.run_prepared_wave
    monkeypatch.setattr(matrix, "capture", lambda *a, **kw: real_capture(*a, **kw, _test=c["context"]))
    def dispatch(task, **kw):
        c["calls"].append(task)
        return real_dispatch(task, **kw)
    monkeypatch.setattr(single, "run_prepared_wave", dispatch)
    return c


def command(c):
    return ["--differential", "--network", "--date", DAY, "--plan", c["matrix_plan"]["path"],
        "--expected-plan-sha256", c["matrix_plan"]["sha256"], "--authorization", c["authorization"]["path"],
        "--authorization-sha256", c["authorization"]["sha256"]]


def safe(value):
    for k, v in store.safe().items():
        assert value[k] == v


@pytest.mark.parametrize("args", [["--differential"], ["--differential", "--validate-only"]])
def test_default_and_validate_only_zero_workers_and_network(monkeypatch, capsys, args):
    def denied(*a, **kw):
        pytest.fail("offline default cannot start any worker")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(matrix, "capture", denied)
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    safe(result)
    assert result["worker_starts"] == result["source_requests"] == 0
    assert result["plan"]["date"] is None and result["plan"]["execution_authorized"] is False


def test_frozen_scope_packet_bytes_and_nonoverlapping_budgets():
    from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
    plan = contract.frozen_plan()
    assert (plan["bars_requests_max"], plan["handshake_requests_max"], plan["transaction_requests"]) == (4, 12, 0)
    previous = None
    for key, code, count, at in contract.EXPERIMENTS:
        p = contract.ExperimentProfile(key)
        parser = GetSecurityBarsCmd(None)
        parser.setParams(8, int(code.startswith("6")), code, 0, count)
        assert parser.send_pkg == p.expected_packet()
        assert p.CODES == [code] and p.request["offset"] == count
        target = pre.stamp(f"{DAY}T{at}+08:00")
        if previous:
            assert previous + timedelta(seconds=2+3) < target - timedelta(seconds=5)
        previous = target
    assert previous + timedelta(seconds=5) < pre.stamp(f"{DAY}T14:51:05+08:00")
    with pytest.raises(ValueError): contract.ExperimentProfile("E")
    assert old.frozen_plan()["request"]["offset"] == 800 and old.CODES == ["000001"]


@pytest.mark.parametrize("field,value", [("endpoint", {"host": "127.0.0.1", "port": 7709}),
    ("frequency", "5m"), ("start", 1), ("bars_requests_max", 5), ("network_work_ms", 1501),
    ("date", "2026-10-26"), ("execution_authorized", True)])
def test_matrix_plan_cannot_open_arbitrary_scope(field, value):
    plan = contract.frozen_plan()
    plan[field] = value
    plan["plan_hash"] = store.digest({k: v for k, v in plan.items() if k != "plan_hash"})
    with pytest.raises(ValueError): contract.check_plan(plan)


@pytest.mark.parametrize("mode,expected,interpretation", [
    ("complete", 0, "NO_SOURCE_QUALIFICATION_OR_SEMANTIC_CONCLUSION"),
    ("large-count-short", 2, "REQUEST_SCALE_ASSOCIATION_NOT_A_LIMIT_OR_COUNT_REDUCTION_APPROVAL"),
    ("shanghai-short", 2, "SECURITY_OR_MARKET_ASSOCIATION_NOT_ROOT_CAUSE"),
    ("short", 2, "INCONCLUSIVE_NODE_SERVICE_PROTOCOL_STOP_UNCHANGED_NODE_TRIALS")])
def test_actual_matrix_entry_four_independent_children_seals_and_double_replay(case, monkeypatch, capsys, mode, expected, interpretation):
    c = prepare(case, monkeypatch, capsys)
    c["context"]["worker_modes"] = {key: mode for key, *_ in contract.EXPERIMENTS}
    assert cli.main(command(c)) == expected
    output = json.loads(capsys.readouterr().out)
    assert output["integrity_verified"], output
    safe(output)
    assert output["interpretation"] == interpretation
    assert [x["experiment_id"] for x in c["calls"]] == list("ABCD")
    path = Path(output["evidence_path"])
    evidence = json.loads(path.read_bytes())
    assert evidence["experiment_set_complete"] and evidence["test_only"]
    originals = [ref for x in evidence["items"] for ref in x["files"].values()]
    replays = []
    for i in range(2):
        destination = c["tmp"] / f"matrix-replay-{i}.json"
        assert cli.main(["--differential", "--verify", str(path), "--expected-file-sha256", pre.ref(path)["sha256"],
                         "--replay-output", str(destination)]) == expected
        capsys.readouterr()
        replays.append(destination.read_bytes())
    assert replays[0] == replays[1]
    assert all(pre.ref(ref["path"]) == ref for ref in originals)
    # Duplicate matrix entry cannot consume the four independent experiments again.
    c["clock"].base = pre.stamp(f"{DAY}T14:46:30+08:00")
    c["clock"].start = __import__('time').monotonic()
    assert cli.main(command(c)) == 2
    capsys.readouterr()
    assert len(c["calls"]) == 4
    if mode == "complete":
        first = evidence["items"][0]
        expired = pre.stamp(first["continuation_checked_at"]) + timedelta(seconds=601)
        assert not matrix.continuation(first["files"], first["verdict"], current=expired)
        changed = deepcopy(evidence)
        changed["items"][0]["id"] = "B"
        changed["evidence_hash"] = store.digest({k: v for k, v in changed.items() if k != "evidence_hash"})
        ref = dump(c["tmp"] / "resigned-cross-experiment.json", changed)
        assert not matrix.verify(ref["path"], ref["sha256"])["integrity_verified"]


@pytest.mark.parametrize("mutation", ["old-auth", "draft", "wrong-code", "wrong-date", "wrong-leaf", "stale", "shared-guard"])
def test_preconditions_reject_with_zero_capture_dispatch(case, monkeypatch, capsys, mutation):
    c = prepare(case, monkeypatch, capsys)
    outer = c["outer"]
    if mutation == "old-auth": outer["schema"] = old.AUTHORIZATION_VERSION
    elif mutation == "draft": outer["status"] = "DRAFT_NOT_AUTHORIZED"
    elif mutation == "wrong-date": outer["trade_date"] = "2000-01-01"
    elif mutation == "wrong-code": outer["code_sha"] = "b" * 40
    elif mutation == "wrong-leaf": outer["experiments"]["A"] = outer["experiments"]["B"]
    elif mutation == "stale": c["clock"].base += timedelta(seconds=601)
    elif mutation == "shared-guard": dump(c["cache"] / "cleanup-incomplete.json", {"keep": True})
    c["authorization"] = dump(Path(c["authorization"]["path"]), outer)
    assert cli.main(command(c)) == 2
    safe(json.loads(capsys.readouterr().out))
    assert c["calls"] == []


def test_cleanup_unconfirmed_preserves_guard_and_stops_remaining_experiments(case, monkeypatch, capsys):
    c = prepare(case, monkeypatch, capsys)
    real = single.run_prepared_wave
    def lost_confirmation(*a, **kw):
        result = real(*a, **kw)  # The actual fixture process is reaped before fault injection.
        return {**result, "cleanup_complete": False, "tree_empty_observed": False}
    monkeypatch.setattr(single, "run_prepared_wave", lost_confirmation)
    assert cli.main(command(c)) == 2
    capsys.readouterr()
    assert [x["experiment_id"] for x in c["calls"]] == ["A"]
    assert (c["cache"] / "cleanup-incomplete.json").exists()
    assert not (c["root"] / contract.ExperimentProfile("B").AUDIT_RELATIVE / DAY).exists()


def test_work_timeout_is_not_a_parser_failure_and_cannot_continue(case, monkeypatch, capsys):
    c = prepare(case, monkeypatch, capsys)
    c["context"]["worker_modes"]["A"] = "timeout"
    assert cli.main(command(c)) == 2
    output = json.loads(capsys.readouterr().out)
    assert len(c["calls"]) == 1
    value = json.loads(Path(output["evidence_path"]).read_bytes())
    assert not value["experiment_set_complete"] and not value["items"][0]["continuation_allowed"]


@pytest.mark.parametrize("mutation", ["symbol", "count", "count-bool", "experiment", "target", "matrix", "anchor"])
def test_resigned_task_cannot_change_an_experiment(tmp_path, mutation):
    profile = contract.ExperimentProfile("A")
    target = pre.stamp(f"{DAY}T{profile.target_clock}+08:00")
    task = profile.task_for(target, actual=target-timedelta(seconds=5), started=100.0,
        batch_id="synthetic-only", code_sha="a"*40, test_only=True,
        config=dump(tmp_path / "config.json", {}), matrix_intent_ref=dump(tmp_path / "intent.json", {}))
    if mutation == "symbol": task["request"]["symbol"] = "600000"
    elif mutation == "count": task["request"]["offset"] = 800
    elif mutation == "count-bool": task["request"]["offset"] = True
    elif mutation == "experiment": task["experiment_id"] = "B"
    elif mutation == "target": task["scheduled_at"] = f"{DAY}T14:48:00+08:00"
    elif mutation == "matrix": task["matrix_plan_hash"] = "0"*64
    else: task["matrix_intent"]["sha256"] = "z"*64
    with pytest.raises(ValueError): profile.check_task(task)


@pytest.mark.parametrize("shell", SHELLS)
def test_actual_powershell_detects_differential_worker(tmp_path, shell):
    ops, temp = tmp_path / "ops", tmp_path / "temp"
    ops.mkdir(); temp.mkdir()
    dump(ops / "intent.json", {"created_at": pre.now().isoformat(), "environment_observation_since": pre.now().isoformat()})
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File",
        str(FIXTURES / "t31_environment_behavior_harness.ps1"), "-Script", str(pre.ENV_SCRIPT),
        "-OpsDirectory", str(ops), "-OutputPath", str(ops / "environment.json"), "-TempDirectory", str(temp),
        "-Mode", "protocol-differential-worker", "-MinuteCapture", "-ProtocolDiagnostic",
        "-ProjectRoot", str(tmp_path / "synthetic-root"), "-MinuteCaptureRoot", str(tmp_path / "cache"),
        "-ProtocolDiagnosticRoot", str(tmp_path / "differential"), "-TradeDate", DAY],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 2, result.stderr
    value = json.loads((ops / "environment.json").read_bytes())
    assert {row["ProcessId"] for row in value["workers"]} == {6000, 6001}


@pytest.mark.parametrize("mode", ["pending", "damaged"])
def test_matrix_parent_intent_blocks_even_if_its_owner_pid_was_reused(tmp_path, mode):
    import os
    directory = tmp_path / contract.MATRIX_ROOT / "2000-01-01"
    intent = dump(directory / "matrix-intent.json", {"owner_pid": os.getpid(), "preserve": True})
    if mode == "damaged":
        dump(directory / "matrix-result-v1.json", {"intent": intent, "evidence_hash": "not-valid"})
    before = pre.ref(intent["path"])
    with pytest.raises(ValueError, match="preserve_no_launch"):
        matrix.blockers(tmp_path)
    assert pre.ref(intent["path"]) == before


def test_trace_scope_rejects_count_change_not_just_hash_change():
    from types import SimpleNamespace
    profile = contract.ExperimentProfile("A")
    groups = [{"index": i, "send": {"data": {"base64": base64.b64encode(packet).decode()}}}
              for i, packet in enumerate([*contract.HANDSHAKES, profile.expected_packet()])]
    profile.check_trace_request(SimpleNamespace(interactions=groups))
    groups[-1]["send"]["data"]["base64"] = base64.b64encode(contract.ExperimentProfile("B").expected_packet()).decode()
    with pytest.raises(ValueError, match="actual_send_bytes"):
        profile.check_trace_request(SimpleNamespace(interactions=groups))


def test_actual_internal_differential_worker_cannot_skip_parent_approval(tmp_path):
    import time
    profile = contract.ExperimentProfile("A")
    target = pre.stamp(f"{DAY}T{profile.target_clock}+08:00")
    task = profile.task_for(target, actual=target-timedelta(seconds=5), started=time.perf_counter(),
        batch_id="not-authorized", code_sha="a"*40, test_only=False,
        config=dump(tmp_path / "config.json", {}), matrix_intent_ref=dump(tmp_path / "intent.json", {}))
    result = single.run_prepared_wave(task, contract_profile=profile)
    assert not result["ok"] and result["go_sent_monotonic"] is None
    assert result["cleanup_complete"] and result["tree_empty_observed"]
    assert not any(e.get("kind") == "protocol_io" for e in store.events_from_result(result))
