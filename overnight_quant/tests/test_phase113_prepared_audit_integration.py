"""Synthetic-only new CLI/preflight/capture chain; old evidence is not reclassified."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from overnight_quant.data import minute_capture_audit as legacy
from overnight_quant.data import minute_capture_preflight as pre
from overnight_quant.data import minute_local_prepare_audit as audit
from overnight_quant.data import minute_local_prepare_contract as contract
from overnight_quant.data import minute_local_prepare_process as process
from overnight_quant.scripts import run_minute_local_prepare_audit as cli
from overnight_quant.scripts import run_minute_capture_preflight as pre_cli
from overnight_quant.tests.test_phase108_minute_preflight import case, no_source_or_ntp, dump, DAY, SHELLS, FIXTURES
from overnight_quant.tests.test_phase107_minute_capture_audit import assert_safe
from overnight_quant.tests.test_phase112_minute_local_prepare import FIXTURE, task as native_task_fixture


def prepared(case, monkeypatch):
    case["plan_ref"] = dump(case["cache"] / "prepared-plan.json", contract.frozen_plan())
    original = pre.prepare
    monkeypatch.setattr(pre, "prepare", lambda **kw: original(**kw, _test=case["test"]))
    return case


def preflight_args(case, name):
    args = ["--prepare", "--execution-contract", contract.EXECUTION_CONTRACT,
        "--date", DAY, "--approved-code-sha", case["head"], "--plan", case["plan_ref"]["path"],
        "--expected-plan-sha256", case["plan_ref"]["sha256"], "--output-dir", str(case["tmp"] / name)]
    for key, reference in case["inputs"].items():
        name = key.replace("_", "-")
        args += ["--" + name, reference["path"], "--" + name + "-sha256", reference["sha256"]]
    return args


def authorization(case, reference):
    config = dump(case["tmp"] / "native-config.json", {"SERVER": {"HQ": [["test-only", "59.36.5.11", 7709]]},
        "BESTIP": {"HQ": ["59.36.5.11", 7709]}})
    return {"schema": contract.AUTHORIZATION_VERSION, "status": "PM_APPROVED_SINGLE_AUDIT", "test_only": True,
        "execution_contract": contract.EXECUTION_CONTRACT, "trade_date": DAY, "code_sha": case["head"],
        "plan_hash": contract.frozen_plan()["plan_hash"], "single_use": True,
        "preflight": reference, "local_config": config}


def context(case, mode="complete", validate=True):
    return {"test_only": True, "root": case["root"], "clock": case["clock"], "sleep": case["clock"].sleep,
        "validate_authorization": validate, "worker_command": [sys.executable, str(FIXTURE), mode]}


def test_real_v2_entries_four_workers_twenty_bars_and_dual_replay(case, monkeypatch, capsys):
    prepared(case, monkeypatch)
    assert pre_cli.main(preflight_args(case, "new-preflight")) == 0
    value = json.loads(capsys.readouterr().out)
    path = Path(value["result_path"])
    reference = pre.ref(path)
    verified = pre.verify_file(path, reference["sha256"], trade_date=DAY, approved_sha=case["head"],
        current=case["clock"](), root=case["root"], _test_only=True, execution_contract=contract.EXECUTION_CONTRACT)
    assert verified["integrity_verified"] and not verified["sampling_authorized"]
    assert value["schema"] == contract.PREFLIGHT_VERSION
    # Explicit old verifier cannot silently accept the new preflight contract.
    assert not pre.verify_file(path, reference["sha256"], trade_date=DAY, approved_sha=case["head"],
        current=case["clock"](), root=case["root"], _test_only=True)["integrity_verified"]
    auth_ref = dump(case["tmp"] / "new-pm-authorization.json", authorization(case, reference))
    original = audit.capture
    monkeypatch.setattr(audit, "capture", lambda *a, **k: original(*a, **k,
        audit_root=case["cache"], _test=context(case)))
    assert cli.main(["--network", "--date", DAY, "--plan", case["plan_ref"]["path"],
        "--expected-plan-sha256", case["plan_ref"]["sha256"], "--authorization", auth_ref["path"],
        "--authorization-sha256", auth_ref["sha256"]]) == 0
    output = json.loads(capsys.readouterr().out)
    raw_path = Path(output["evidence_path"])
    raw = raw_path.read_bytes()
    evidence = json.loads(raw)
    assert evidence["test_only"] and evidence["summary"]["requests_attempted"] == 20
    # A PID may be reused by Windows; identify each independent lifetime too.
    assert len({(w["result"]["pid"], w["result"]["process_started_monotonic"]) for w in evidence["waves"]}) == 4
    assert all(w["result"]["ready_pid_in_owned_job"] for w in evidence["waves"])
    assert all(a["result"]["finished_monotonic"] <= b["result"]["process_started_monotonic"]
               for a, b in zip(evidence["waves"], evidence["waves"][1:]))
    assert all(w["result"]["total_residence_ms"] > 4000 for w in evidence["waves"])
    assert all(w["result"]["wave_elapsed_ms"] < 2000 for w in evidence["waves"])
    for row in evidence["summary"]["row_stability"]:
        assert row["observed_stable"] and not row["is_final"] and row["finality"] == "UNPROVEN"
    anchor = sha256(raw).hexdigest()
    replays = []
    for index in range(2):
        replay = case["tmp"] / f"independent-replay-{index}.json"
        assert cli.main(["--verify", str(raw_path), "--expected-file-sha256", anchor, "--replay-output", str(replay)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["integrity_verified"]
        assert_safe(result)
        replays.append(replay.read_bytes())
    assert replays[0] == replays[1] and raw_path.read_bytes() == raw
    assert legacy.pending_preparation_receipts(case["cache"]) == []
    assert not legacy.verify(evidence, anchor, anchor)["integrity_verified"]
    assert_safe(output)
    # Independent raw anchors still reject a fully internally re-signed edit.
    modified = deepcopy(evidence)
    modified["waves"][0]["result"]["task"]["io_deadline_monotonic"] += .001
    modified["evidence_hash"] = legacy.digest({k: v for k, v in modified.items() if k != "evidence_hash"})
    assert not audit.verify(modified, anchor, sha256(json.dumps(modified).encode()).hexdigest())["integrity_verified"]
    # Even a new external anchor plus all re-signed task/stream hashes cannot
    # authorize a changed absolute phase budget.
    result = modified["waves"][0]["result"]
    result["task_hash"] = legacy.digest(result["task"])
    result["ready"]["task_hash"] = result["task_hash"]
    values = [json.loads(line) for line in result["stdout"].splitlines()]
    for item in values:
        if "task_hash" in item:
            item["task_hash"] = result["task_hash"]
    result["stdout"] = "\n".join(legacy.json.dumps(v, sort_keys=True) for v in values) + "\n"
    result["stdout_sha256"] = sha256(result["stdout"].encode()).hexdigest()
    modified["evidence_hash"] = legacy.digest({k: v for k, v in modified.items() if k != "evidence_hash"})
    resigned = sha256(json.dumps(modified).encode()).hexdigest()
    assert not audit.verify(modified, resigned, resigned)["integrity_verified"]


@pytest.mark.parametrize("args", [[], ["--validate-only"]])
def test_new_default_and_validation_zero_processes(monkeypatch, capsys, args):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("No child allowed"))
    assert cli.main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["network_requests_made"] == value["child_processes_started"] == 0
    assert_safe(value)


def test_returned_plan_or_task_cannot_mutate_the_frozen_source_identity(tmp_path):
    baseline = contract.frozen_plan()
    edited = contract.frozen_plan()
    edited["endpoint"]["host"] = "unapproved.invalid"
    edited["client_versions"]["mootdx"] = "unknown"
    assert contract.frozen_plan() == baseline
    target = pre.stamp("2026-10-22T14:49:55+08:00")
    reference = {"path": str(tmp_path / "config.json"), "sha256": "0" * 64}
    value = audit.task_for(target, actual=target - timedelta(seconds=5), started=100,
        batch_id="synthetic", wave=0, code_sha="a" * 40, config=reference, test_only=True)
    value["endpoint"]["host"] = "unapproved.invalid"
    value["dependencies"]["mootdx"] = "unknown"
    value["config"]["sha256"] = "1" * 64
    assert reference["sha256"] == "0" * 64
    assert contract.frozen_plan() == baseline
    with pytest.raises(ValueError, match="identity_invalid"):
        contract.check_task(value)


def test_old_authorization_new_capture_and_old_evidence_are_not_upgraded(case, monkeypatch):
    monkeypatch.setattr(audit, "run_prepared_wave", lambda *a, **k: pytest.fail("No worker"))
    bad = {"schema": pre.AUTHORIZATION_VERSION}
    with pytest.raises(ValueError, match="new_version_date_specific"):
        audit.capture(contract.frozen_plan(), DAY, authorization=bad, audit_root=case["cache"],
                      _test=context(case))
    assert not (case["cache"] / DAY / "intent.json").exists()
    old = legacy.safe(schema=legacy.VERSION, plan=legacy.frozen_plan(), waves=[], trade_date=DAY)
    old["evidence_hash"] = legacy.digest(old)
    assert not audit.verify(old, "a" * 64, "a" * 64)["integrity_verified"]


def test_cleanup_failure_persists_guard_and_dispatches_no_later_wave(case, monkeypatch):
    original_close = process._ProcessScope.close
    def failure(self):
        original_close(self)
        raise OSError("synthetic cleanup confirmation failure")
    monkeypatch.setattr(process._ProcessScope, "close", failure)
    auth = authorization(case, {"path": "test-only unused", "sha256": "0" * 64})
    evidence, path = audit.capture(contract.frozen_plan(), DAY, authorization=auth,
        audit_root=case["cache"], _test=context(case, mode="wrong_ready", validate=False))
    assert len(evidence["waves"]) == 1
    assert not evidence["waves"][0]["result"]["cleanup_complete"]
    assert evidence["summary"]["requests_attempted"] == 0
    guard = case["cache"] / "cleanup-incomplete.json"
    saved = guard.read_bytes()
    monkeypatch.setattr(audit, "run_prepared_wave", lambda *a, **k: pytest.fail("No repeat worker"))
    with pytest.raises(ValueError, match="prior_worker_cleanup_unconfirmed"):
        audit.capture(contract.frozen_plan(), DAY, authorization=auth, audit_root=case["cache"],
                      _test=context(case, validate=False))
    assert guard.read_bytes() == saved
    assert_safe(evidence)


@pytest.mark.parametrize("failure", ["late_dispatch", "start_failure"])
def test_zero_child_failure_is_frozen_and_replays_without_invented_PID(case, monkeypatch, failure):
    original = audit.run_prepared_wave
    def no_start(*args, **kwargs):
        raise OSError("synthetic process creation failure")
    monkeypatch.setattr(process.subprocess, "Popen", no_start)
    if failure == "late_dispatch":
        def expired(*a, **k):
            time.sleep(3.6)
            return original(*a, **k)
        monkeypatch.setattr(audit, "run_prepared_wave", expired)
    auth = authorization(case, {"path": "test-only unused", "sha256": "0" * 64})
    evidence, path = audit.capture(contract.frozen_plan(), DAY, authorization=auth,
        audit_root=case["cache"], _test=context(case, validate=False))
    assert len(evidence["waves"]) == 1 and evidence["waves"][0]["result"]["pid"] is None
    assert evidence["summary"]["status"] == "MINUTE_CAPTURE_AUDIT_FAILED"
    assert evidence["summary"]["requests_attempted"] == 0
    anchor = sha256(path.read_bytes()).hexdigest()
    assert audit.verify(evidence, anchor, anchor)["integrity_verified"]
    assert_safe(evidence)


def test_historical_date_intent_and_new_prepare_window_are_not_bypassed(case, monkeypatch):
    monkeypatch.setattr(audit, "run_prepared_wave", lambda *a, **k: pytest.fail("No worker"))
    old_intent = case["cache"] / DAY / "intent.json"
    dump(old_intent, {"historical_failure": True})
    before = old_intent.read_bytes()
    auth = authorization(case, {"path": "test-only unused", "sha256": "0" * 64})
    with pytest.raises(FileExistsError):
        audit.capture(contract.frozen_plan(), DAY, authorization=auth, audit_root=case["cache"],
                      _test=context(case, validate=False))
    assert old_intent.read_bytes() == before
    case["clock"].base += timedelta(minutes=2)
    with pytest.raises(ValueError, match="preparation_window_missed"):
        audit.capture(contract.frozen_plan(), DAY, authorization=auth, audit_root=case["cache"],
                      _test=context(case, validate=False))


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("mode", ["prepared-worker", "prepared-cli", "prepared-module", "prepared-task"])
def test_actual_PS_inventory_recognizes_new_entry_and_owned_descendant(tmp_path, shell, mode):
    ops, temp = tmp_path / "ops", tmp_path / "temp"
    ops.mkdir()
    temp.mkdir()
    dump(ops / "intent.json", {"created_at": "2026-10-21T14:44:00+08:00",
                              "environment_observation_since": "2026-10-21T14:44:00+08:00"})
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File",
        str(FIXTURES / "t31_environment_behavior_harness.ps1"), "-Script", str(pre.ENV_SCRIPT),
        "-OpsDirectory", str(ops), "-OutputPath", str(ops / "environment.json"), "-TempDirectory", str(temp),
        "-Mode", mode, "-MinuteCapture", "-ProjectRoot", str(tmp_path),
        "-MinuteCaptureRoot", str(tmp_path / "capture"), "-TradeDate", DAY],
        capture_output=True, timeout=20)
    assert result.returncode == 2, (result.stdout, result.stderr)
    record = json.loads((ops / "environment.json").read_text(encoding="utf-8-sig"))
    if mode == "prepared-task":
        assert any("Active project task" in v for v in record["blockers"])
    else:
        assert {p["ProcessId"] for p in record["workers"]} == {6000, 6001}


@pytest.mark.parametrize("state", ["pending", "damaged_start", "damaged_close", "unknown", "scalar_task", "confirmed"])
def test_persistent_started_receipts_block_both_versions_until_matching_cleanup(case, monkeypatch, state):
    value = native_task_fixture(case["tmp"])
    started = case["cache"] / value["trade_date"] / "wave-0.started.json"
    reference = dump(started, {"task": value})
    if state == "damaged_start":
        started.write_bytes(b"{damaged")
    elif state == "unknown":
        reference = dump(started, {"task": {}})
    elif state == "scalar_task":
        reference = dump(started, {"task": 1})
    closed = started.with_name("wave-0.json")
    if state in ("confirmed", "unknown"):
        dump(closed, {"start_receipt_sha256": reference["sha256"], "result": {
            "cleanup_complete": True, "task": {**(value if state == "confirmed" else {}), "launch_root_pid": 123}}})
    elif state == "damaged_close":
        closed.write_bytes(b"{damaged")
    original = started.read_bytes()
    pending = legacy.pending_preparation_receipts(case["cache"])
    if state == "confirmed":
        assert pending == []
        return
    assert pending == [str(started)]
    monkeypatch.setattr(audit, "run_prepared_wave", lambda *a, **k: pytest.fail("No new worker"))
    for module, plan in ((legacy, legacy.frozen_plan()), (audit, contract.frozen_plan())):
        with pytest.raises(ValueError, match="pending_preparation_cleanup_receipt"):
            module.capture(plan, DAY, authorization={}, audit_root=case["cache"], _test=context(case, validate=False))
    assert started.read_bytes() == original


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("confirmed", [False, True])
def test_actual_PS_pending_receipt_guard_and_reordered_cleanup_are_read_only(tmp_path, shell, confirmed):
    ops, temp, root = tmp_path / "ops", tmp_path / "temp", tmp_path / "capture"
    ops.mkdir()
    temp.mkdir()
    value = native_task_fixture(tmp_path)
    started = root / value["trade_date"] / "wave-0.started.json"
    reference = dump(started, {"task": value})
    before = started.read_bytes()
    if confirmed:
        # Equivalent structured records do not depend on JSON property order.
        task_reordered = {key: value[key] for key in reversed(list(value))}
        task_reordered["config"] = dict(reversed(list(value["config"].items())))
        task_reordered["launch_root_pid"] = 123
        (started.parent / "wave-0.json").write_text(json.dumps({"result": {
            "task": task_reordered, "cleanup_complete": True}, "start_receipt_sha256": reference["sha256"]}))
    dump(ops / "intent.json", {"created_at": "2026-10-21T14:44:00+08:00",
                              "environment_observation_since": "2026-10-21T14:44:00+08:00"})
    completed = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File",
        str(FIXTURES / "t31_environment_behavior_harness.ps1"), "-Script", str(pre.ENV_SCRIPT),
        "-OpsDirectory", str(ops), "-OutputPath", str(ops / "environment.json"), "-TempDirectory", str(temp),
        "-Mode", "unrelated", "-MinuteCapture", "-ProjectRoot", str(tmp_path),
        "-MinuteCaptureRoot", str(root), "-TradeDate", DAY], capture_output=True, timeout=20)
    assert completed.returncode == (0 if confirmed else 2), (completed.stdout, completed.stderr)
    record = json.loads((ops / "environment.json").read_text(encoding="utf-8-sig"))
    assert record["pending_preparations"] == ([] if confirmed else [str(started)])
    assert started.read_bytes() == before
