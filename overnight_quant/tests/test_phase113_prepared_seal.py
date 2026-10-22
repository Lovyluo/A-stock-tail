"""Post-write sealing proof, not source qualification. Synthetic fixture only."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

from overnight_quant.data import minute_capture_audit as legacy
from overnight_quant.data import minute_local_prepare_audit as audit
from overnight_quant.data import minute_local_prepare_contract as contract
from overnight_quant.data import minute_local_prepare_seal as sealing
from overnight_quant.scripts import run_minute_local_prepare_audit as cli
from overnight_quant.tests.test_phase107_minute_capture_audit import Clock, DAY, assert_safe
from overnight_quant.tests.test_phase112_minute_local_prepare import FIXTURE, task


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic-seal")
    config = task(root)["config"]
    auth = {"schema": contract.AUTHORIZATION_VERSION, "status": "PM_APPROVED_SINGLE_AUDIT",
        "test_only": True, "execution_contract": contract.EXECUTION_CONTRACT, "trade_date": DAY,
        "code_sha": "a" * 40, "plan_hash": contract.frozen_plan()["plan_hash"], "single_use": True,
        "preflight": {}, "local_config": config}
    clock = Clock("14:48:00")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("No external source request"))
        evidence, path = audit.capture(contract.frozen_plan(), DAY, authorization=auth, audit_root=root,
            _test={"test_only": True, "clock": clock, "sleep": clock.sleep,
                   "worker_command": [sys.executable, str(FIXTURE), "complete"]})
    assert evidence["summary"]["requests_attempted"] == 20 and len(evidence["waves"]) == 4
    assert all(w["result"]["ok"] for w in evidence["waves"])
    assert evidence["summary"]["status"] != sealing.FAILED
    return evidence, path


def anchor(path):
    return sha256(path.read_bytes()).hexdigest()


def replay(path, **changes):
    receipt, completion = path.parent / sealing.RECEIPT_NAME, path.parent / sealing.COMPLETION_NAME
    args = {"receipt_path": receipt, "expected_receipt_sha256": anchor(receipt) if receipt.exists() else None,
        "completion_path": completion, "expected_completion_sha256": anchor(completion) if completion.exists() else None}
    args.update(changes)
    return audit.verify_files(path, anchor(path), **args)


def reseal(tmp_path, evidence, monkeypatch, *, delay=0, wall="14:50:58", failure=None):
    now, mono = [datetime.fromisoformat(f"{DAY}T{wall}+08:00")], [100.0]
    monkeypatch.setattr(sealing, "time", SimpleNamespace(perf_counter=lambda: mono[0]))
    def writer(path, value):
        if Path(path).name == failure:
            raise OSError("synthetic seal publication failure")
        result = legacy.write(path, value)
        if Path(path).name == sealing.RECEIPT_NAME:
            mono[0] += delay
            now[0] += timedelta(seconds=delay)
        return result
    return sealing.seal(tmp_path, evidence, clock=lambda: now[0], write=writer)


def cli_args(path):
    receipt, completion = path.parent / sealing.RECEIPT_NAME, path.parent / sealing.COMPLETION_NAME
    return ["--verify", str(path), "--expected-file-sha256", anchor(path),
        "--seal-receipt", str(receipt), "--expected-receipt-sha256", anchor(receipt),
        "--seal-completion", str(completion), "--expected-completion-sha256", anchor(completion)]


@pytest.mark.parametrize("delay,wall,expected", [(0, "14:50:58", "SEAL_COMPLETED_WITHIN_BUDGET"),
    (0.5, "14:51:04.500", "SEAL_COMPLETED_WITHIN_BUDGET"),
    (3.0, "14:50:58", "SEAL_COMPLETED_WITHIN_BUDGET"),
    (0.501, "14:51:04.500", "SEAL_FAILED"), (3.001, "14:50:58", "SEAL_FAILED")])
def test_post_write_deadline_and_budget_are_replayed_and_cli_agrees(captured, tmp_path, monkeypatch, capsys,
                                                                    delay, wall, expected):
    evidence, original = captured
    before = original.read_bytes()
    path = reseal(tmp_path, evidence, monkeypatch, delay=delay, wall=wall)
    checked = replay(path)
    assert checked["integrity_verified"] and checked["evidence_integrity_verified"]
    assert checked["seal_integrity_verified"] and checked["seal_status"] == expected
    assert checked["capture_result"] == evidence["summary"]["status"]
    want_failed = expected == "SEAL_FAILED"
    assert (checked["final_execution_result"] == sealing.FAILED) is want_failed
    # Actual online CLI and independent verifier share the same final reducer.
    auth_path = legacy.write(tmp_path / "auth.json", evidence["authorization"])
    plan_path = legacy.write(tmp_path / "plan.json", contract.frozen_plan())
    monkeypatch.setattr(audit, "capture", lambda *a, **kw: (evidence, path))
    online_exit = cli.main(["--network", "--date", DAY, "--plan", str(plan_path),
        "--expected-plan-sha256", anchor(plan_path), "--authorization", str(auth_path),
        "--authorization-sha256", anchor(auth_path)])
    online = json.loads(capsys.readouterr().out)
    assert online_exit == (2 if want_failed else 0)
    assert online["summary"] == checked["summary"]
    outputs = []
    for index in range(2):
        output = tmp_path / f"replay-{index}.json"
        assert cli.main([*cli_args(path), "--replay-output", str(output)]) == online_exit
        value = json.loads(capsys.readouterr().out)
        assert value["summary"] == online["summary"]
        assert value["final_execution_result"] == online["final_execution_result"]
        outputs.append(output.read_bytes())
        assert_safe(value)
    assert outputs[0] == outputs[1] and original.read_bytes() == before and path.read_bytes() == before


@pytest.mark.parametrize("change", ["missing_receipt", "missing_completion", "missing_anchor", "missing_completion_anchor", "damaged",
    "cross_batch", "wrong_date", "wrong_code", "wrong_plan", "wrong_evidence", "old_version",
    "raw_tamper", "resigned_timing", "false_confirmation"])
def test_incomplete_or_forged_seal_cannot_upgrade_capture(captured, tmp_path, monkeypatch, change):
    path = reseal(tmp_path, captured[0], monkeypatch)
    receipt, completion = path.parent / sealing.RECEIPT_NAME, path.parent / sealing.COMPLETION_NAME
    changes = {}
    if change == "missing_receipt":
        changes["receipt_path"] = tmp_path / "absent"
    elif change == "missing_completion":
        changes["completion_path"] = tmp_path / "absent"
    elif change == "missing_anchor":
        changes["expected_receipt_sha256"] = None
    elif change == "missing_completion_anchor":
        changes["expected_completion_sha256"] = None
    elif change in ("damaged", "raw_tamper"):
        alternate = tmp_path / "damaged.json"
        alternate.write_bytes(b"[not-json" if change == "damaged" else receipt.read_bytes() + b" ")
        changes.update(receipt_path=alternate, expected_receipt_sha256=anchor(receipt))
    else:
        value = json.loads(completion.read_bytes())
        field = {"cross_batch": "batch_id", "wrong_date": "trade_date", "wrong_code": "code_sha",
            "wrong_plan": "plan_hash", "wrong_evidence": "evidence_file_sha256", "old_version": "schema",
            "resigned_timing": "seal_elapsed_ms", "false_confirmation": "receipt_persisted_and_readback_confirmed"}[change]
        value[field] = 9999 if change == "resigned_timing" else False if change == "false_confirmation" else "wrong"
        value = sealing.signed({k: v for k, v in value.items() if k != "completion_hash"}, "completion_hash")
        alternative = legacy.write(tmp_path / "forged.json", value)
        changes.update(completion_path=alternative, expected_completion_sha256=anchor(alternative))
    checked = replay(path, **changes)
    assert checked["evidence_integrity_verified"] and not checked["integrity_verified"]
    assert checked["seal_status"] == "SEAL_UNCONFIRMED"
    assert checked["final_execution_result"] == sealing.FAILED
    assert_safe(checked)


@pytest.mark.parametrize("failure", [sealing.RECEIPT_NAME, sealing.COMPLETION_NAME])
def test_unconfirmed_receipt_write_fails_closed_in_cli_and_replay(captured, tmp_path, monkeypatch, capsys, failure):
    evidence = captured[0]
    auth = legacy.write(tmp_path / "authorization.json", evidence["authorization"])
    plan = legacy.write(tmp_path / "plan.json", contract.frozen_plan())
    def capture(*a, **kw):
        return evidence, reseal(tmp_path / "capture", evidence, monkeypatch, failure=failure)
    monkeypatch.setattr(audit, "capture", capture)
    assert cli.main(["--network", "--date", DAY, "--plan", str(plan), "--expected-plan-sha256", anchor(plan),
        "--authorization", str(auth), "--authorization-sha256", anchor(auth)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert "seal_write_unconfirmed" in result["error"]
    assert result["seal_status"] == "SEAL_UNCONFIRMED" and result["final_execution_result"] == sealing.FAILED
    checked = replay(Path(result["evidence_path"]))
    assert checked["evidence_integrity_verified"] and not checked["integrity_verified"]
    assert checked["final_execution_result"] == result["final_execution_result"]
    assert_safe(result)


def test_legal_capture_failure_can_have_valid_integrity_but_never_execution_success(captured, tmp_path, monkeypatch):
    evidence = deepcopy(captured[0])
    evidence["capture_error"] = "synthetic publication failure after captured waves"
    evidence["summary"] = legacy.safe(**{**evidence["summary"], "status": sealing.FAILED,
        "capture_error": evidence["capture_error"]})
    evidence["evidence_hash"] = legacy.digest({k: v for k, v in evidence.items() if k != "evidence_hash"})
    path = reseal(tmp_path, evidence, monkeypatch)
    checked = replay(path)
    assert checked["integrity_verified"] and checked["seal_status"] == "SEAL_COMPLETED_WITHIN_BUDGET"
    assert checked["capture_result"] == checked["final_execution_result"] == sealing.FAILED
    assert_safe(checked)


def test_raw_only_and_old_v2_seal_are_audit_only_not_complete_execution(captured, capsys):
    evidence, path = captured
    result = audit.verify(evidence, anchor(path), anchor(path))
    assert result["evidence_integrity_verified"] and not result["integrity_verified"]
    assert result["final_execution_result"] == sealing.FAILED
    assert cli.main(["--verify", str(path), "--expected-file-sha256", anchor(path)]) == 2
    assert json.loads(capsys.readouterr().out)["seal_status"] == "SEAL_UNCONFIRMED"


@pytest.mark.parametrize("failure", ["receipt_readback", "completion_readback"])
def test_readback_not_confirmed_cannot_be_promoted(captured, tmp_path, monkeypatch, failure):
    evidence = captured[0]
    original = legacy.write
    name = sealing.RECEIPT_NAME if failure == "receipt_readback" else sealing.COMPLETION_NAME
    def corrupted(path, value):
        return original(path, {"test_only": True, "damaged": True} if Path(path).name == name else value)
    with pytest.raises(sealing.SealWriteError, match="readback_mismatch"):
        sealing.seal(tmp_path, evidence, clock=lambda: datetime.fromisoformat(f"{DAY}T14:50:58+08:00"), write=corrupted)
    checked = replay(tmp_path / sealing.EVIDENCE_NAME)
    assert checked["evidence_integrity_verified"] and not checked["integrity_verified"]
    assert checked["final_execution_result"] == sealing.FAILED
