import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys

import pytest

from overnight_quant.data import ths_single_sample as ths
from overnight_quant.scripts import run_ths_single_sample as cli


HEAD = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ths.ROOT, text=True).strip()


def fixture_file(tmp_path, *, body=None, status=200, content_type="application/json; charset=utf-8", **extra):
    if body is None:
        body = json.dumps({"errocode": 0, "data": [{"code": "000001", "market": "SZ",
            "name": "测试证券", "reason": "测试题材\U0001f680", "date": "2026-11-20"}]}, ensure_ascii=False).encode()
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"body_b64": base64.b64encode(body).decode(), "status": status,
                               "content_type": content_type, **extra}), encoding="utf-8")
    return path


def run_cli(tmp_path, fixture, *, extra=()):
    output = tmp_path / "batch"
    command = [shutil.which("pwsh") or shutil.which("powershell"), "-NoProfile", "-File",
               str(ths.ROOT / "overnight_quant/scripts/run_ths_single_sample_manual.ps1"),
               "-TargetDate", "2026-11-20", "-ApprovedSha", HEAD, "-BatchId", "test-only",
               "-Output", str(output), "-Fixture", str(fixture), "-TestState", str(tmp_path / "state"),
               "-PythonPath", sys.executable, *extra]
    run = subprocess.run(command, cwd=ths.ROOT, capture_output=True, timeout=15)
    returned = tmp_path / "returned.json"
    returned.write_bytes(run.stdout)
    return run, json.loads(run.stdout), output, returned


def replay(result, output, returned):
    return ths.verify(output, result["completion_sha256"], returned, ths.digest(returned.read_bytes()))


def test_default_real_cli_zero_network_zero_worker():
    run = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_ths_single_sample"],
                         cwd=ths.ROOT, capture_output=True, timeout=10)
    result = json.loads(run.stdout)
    assert run.returncode == 0
    assert result["status"] == "NETWORK_NOT_REQUESTED"
    assert result["workers"] == result["actual_http_requests"] == 0
    assert result["permission_version"] == ths.PERMISSION
    assert result["plan"]["max_requests"] == 1


@pytest.mark.parametrize("case,exitcode,error", [
    ("valid", 0, None), ("empty", 0, None), ("encoding", 2, "UnicodeDecodeError"),
    ("nonjson", 2, "JSONDecodeError"), ("redirect", 2, "redirect_stopped"),
    ("timeout", 2, "fixture_timeout"), ("limit", 2, "response_byte_limit"),
])
def test_actual_cli_fixture_and_independent_replay(tmp_path, case, exitcode, error):
    args = {}
    if case == "empty":
        args["body"] = b'{"errocode":0,"data":[]}'
    elif case == "encoding":
        args.update(body=b'\xff\xfe', content_type="application/json")
    elif case == "nonjson":
        args["body"] = b'<html>not json</html>'
    elif case == "redirect":
        args.update(status=302, location="https://example.invalid/never-follow")
    elif case == "timeout":
        args["error"] = "timeout"
    elif case == "limit":
        args["body"] = b' ' * (ths.PLAN["max_response_bytes"] + 1)
    run, result, output, returned = run_cli(tmp_path, fixture_file(tmp_path, **args))
    assert run.returncode == exitcode, result
    assert result["actual_http_requests"] == 0
    assert result["process"]["cleanup_complete"] is True
    assert result["process"]["tree_empty_observed"] is True
    assert not (tmp_path / "state/active.lock").exists()
    verified = replay(result, output, returned)
    assert verified["evidence_integrity_verified"] is True
    assert verified["capture_execution_result"] == ("PASSED" if exitcode == 0 else "FAILED")
    assert verified["current_analysis_usable"] is False
    if error:
        assert error in result["error"]
    if case != "timeout":
        envelope = json.loads((output / "response-bytes.json").read_text())
        assert ths.digest(base64.b64decode(envelope["body_b64"])) == envelope["sha256"]
    else:
        assert not (output / "response-bytes.json").exists()
    if case == "valid":
        sample = verified["sample"]
        assert sample["received_rows"] == sample["readable_attribution_rows"] == 1
        assert sample["coverage"] == sample["source_time_semantics"] == "UNKNOWN"
        assert sample["hotspot_ranking"] == sample["core_stock_candidates"] == []
        assert sample["clues"][0]["source_values"]["reason"] == "测试题材\U0001f680"


def test_encoding_is_not_inferred_from_url():
    body = '{"errocode":0,"data":[{"reason":"中文"}]}'.encode("gbk")
    with pytest.raises(UnicodeDecodeError):
        ths.parse_body(body, "application/json")
    assert ths.parse_body(body, "application/json; charset=GBK")["readable_attribution_rows"] == 1
    with pytest.raises(ValueError, match="unsupported_declared_encoding"):
        ths.parse_body(body, "application/json; charset=latin-1")


def test_missing_execute_inputs_zero_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(ths, "run_probe_worker_process", lambda *a, **k: pytest.fail("worker dispatched"))
    assert cli.main(["--execute"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["actual_http_requests"] == result["requests_attempted"] == 0


def test_raw_save_failure_stops_parse_and_has_one_attempt(tmp_path, monkeypatch):
    fixture = fixture_file(tmp_path)
    def fail(*args):
        raise OSError("disk_write_failed")
    monkeypatch.setattr(ths, "_raw_save", fail)
    monkeypatch.setattr(ths, "parse_body", lambda *a: pytest.fail("parsed before raw sealed"))
    result = ths.request_sample({"output": str(tmp_path), "target_date": "2026-11-20",
        "url": ths.PLAN["url"].format(D="2026-11-20"), "contract_hash": ths.CONTRACT,
        "work_deadline": ths.time.perf_counter() + 7, "fixture": str(fixture)})
    assert result["status"] == "FAILED"
    assert result["failure_stage"] == "raw_seal"
    assert result["requests_attempted"] == 1
    assert result["actual_http_requests"] == 0


def test_cleanup_failure_persists_block_and_prevents_new_worker(tmp_path, monkeypatch):
    fixture = fixture_file(tmp_path)
    calls = []
    def failed(*args, **kwargs):
        calls.append(args)
        return {"ok": False, "cleanup_complete": False, "error": "test_cleanup_unknown"}
    monkeypatch.setattr(ths, "run_probe_worker_process", failed)
    args = dict(target="2026-11-20", head=HEAD, fixture=str(fixture), state=tmp_path / "state")
    first = ths.capture(**args, output=tmp_path / "one", batch="one")
    assert first["status"] == "FAILED" and first["actual_http_requests"] is None
    assert (tmp_path / "state/cleanup-incomplete.json").exists()
    assert (tmp_path / "state/active.lock").exists()
    second = ths.capture(**args, output=tmp_path / "two", batch="two")
    assert second["status"] == "FAILED" and "persistent_cleanup_block" in second["error"]
    assert len(calls) == 1


def test_existing_output_and_wrong_sha_zero_workers(tmp_path, monkeypatch):
    fixture = fixture_file(tmp_path)
    monkeypatch.setattr(ths, "run_probe_worker_process", lambda *a, **k: pytest.fail("dispatched"))
    output = tmp_path / "batch"
    output.mkdir()
    for head, dest in [(HEAD, output), ("0" * 40, tmp_path / "new")]:
        result = ths.capture(target="2026-11-20", head=head, output=dest, batch="test", fixture=str(fixture), state=tmp_path / "state")
        assert result["status"] == "FAILED" and result["actual_http_requests"] == 0


def test_no_completion_or_no_returned_result_never_admitted(tmp_path):
    run, result, output, returned = run_cli(tmp_path, fixture_file(tmp_path))
    assert run.returncode == 0
    with pytest.raises((TypeError, OSError)):
        ths.verify(output, result["completion_sha256"], None, None)
    completion = output / "completion.json"
    completion.unlink()
    with pytest.raises(OSError):
        replay(result, output, returned)


def test_redirect_handler_and_proxy_policy():
    assert ths.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.invalid") is None
    opener = ths.urllib.request.build_opener(ths.NoRedirect())
    assert any(isinstance(handler, ths.NoRedirect) for handler in opener.handlers)


def test_approval_does_not_accept_old_contract_or_nonboolean(tmp_path):
    path = tmp_path / "approval-test-only.json"
    path.write_text(json.dumps({"schema": "concept_structure_authorization_v3", "user_approved": "true"}))
    with pytest.raises(ValueError, match="identity_or_scope"):
        ths.check_permission(path, ths.digest(path.read_bytes()), target="2026-11-20", head=HEAD,
                             output=tmp_path / "out", batch="sample", evaluated_at=datetime.now(ths.CN))


@pytest.mark.parametrize("mode", ["normal", "short", "partial_error", "redirect"])
def test_real_transport_route_with_memory_transport_only(tmp_path, monkeypatch, mode):
    from overnight_quant.data import minute_local_prepare_worker
    state = tmp_path / "state"
    state.mkdir()
    output = state / "out"
    output.mkdir()
    (state / "active.lock").write_text(str(output), encoding="utf-8")
    monkeypatch.setattr(ths, "STATE", state)
    monkeypatch.setattr(ths, "check_permission", lambda *a, **k: {
        "window_start": "2020-01-01T00:00:00+08:00", "window_end": "2099-01-01T00:00:00+08:00"})
    monkeypatch.setattr(minute_local_prepare_worker, "parent_chain", lambda: [9, 8])
    monkeypatch.setattr(ths.subprocess, "check_output", lambda args, **k: HEAD if "rev-parse" in args else "")
    monkeypatch.setattr(ths.urllib.request, "getproxies", lambda: {})
    body = b'{"errocode":0,"data":[]}'
    class Response:
        code = 302 if mode == "redirect" else 200
        headers = {"Content-Type": "application/json", "Content-Length": str(999 if mode == "short" else len(body))}
        reads = 0
        closed = False
        def read1(self, size):
            self.reads += 1
            if self.reads == 1:
                return body[:10] if mode == "partial_error" else body
            if mode == "partial_error":
                raise TimeoutError("partial_read_timeout")
            return b""
        def close(self):
            self.closed = True
    response = Response()
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert request.method == "GET" and 0 < timeout <= 5
            assert "Cookie" not in request.headers and "Authorization" not in request.headers
            return response
    monkeypatch.setattr(ths.urllib.request, "build_opener", lambda *handlers: Opener())
    task = {"output": str(output), "target_date": "2026-11-20", "approved_sha": HEAD,
        "batch_id": "test-only", "controller_pid": 8, "approval": "test-only-not-a-permit",
        "approval_sha": "0" * 64, "url": ths.PLAN["url"].format(D="2026-11-20"),
        "contract_hash": ths.CONTRACT, "work_deadline": ths.time.perf_counter() + 5, "fixture": None}
    ths.write_once(output / "intent.json", {**task, "approval_sha256": task["approval_sha"]})
    result = ths.request_sample(task)
    assert len(calls) == result["actual_http_requests"] == 1
    assert response.closed
    assert result["status"] == ("SAMPLE_PARSED" if mode == "normal" else "FAILED")
    raw = json.loads((output / "response-bytes.json").read_text())
    assert base64.b64decode(raw["body_b64"]) == (body[:10] if mode == "partial_error" else body)
    if mode == "short":
        assert "declared_response_length_mismatch" in result["error"]
    if mode == "partial_error":
        assert result["body_complete"] is False
        assert list(output.glob("received-*.json"))


def test_final_report_write_overrun_is_not_replay_success(tmp_path, monkeypatch):
    _, result, output, returned = run_cli(tmp_path, fixture_file(tmp_path))
    wrapper = json.loads(returned.read_bytes())
    wrapper["launcher_elapsed_ms"] = 10000.001
    returned.write_text(json.dumps(wrapper), encoding="utf-8")
    with pytest.raises(ValueError, match="launcher_budget_unconfirmed"):
        replay(result, output, returned)


def test_direct_live_worker_rejected_before_request(tmp_path, monkeypatch):
    from overnight_quant.data import minute_local_prepare_worker
    monkeypatch.setattr(minute_local_prepare_worker, "parent_chain", lambda: [99, 98])
    monkeypatch.setattr(ths.urllib.request, "build_opener", lambda *a: pytest.fail("HTTP dispatched"))
    with pytest.raises(ValueError, match="supervising_controller_required"):
        ths.request_sample({"output": str(tmp_path), "target_date": "2026-11-20", "fixture": None,
            "url": ths.PLAN["url"].format(D="2026-11-20"), "contract_hash": ths.CONTRACT,
            "controller_pid": 123})


@pytest.mark.parametrize("field,value", [(None, None), ("acknowledges_unknown_basis", "true"),
    ("user_approved", 1), ("known_use_constraint", "KNOWN_PROHIBITION"),
    ("schema", "old_permission"), ("max_requests", 2), ("approved_sha", "0" * 40),
    ("window_end", "2026-11-21T09:59:00+08:00")])
def test_permission_exact_binding_and_unknown_ack(tmp_path, monkeypatch, field, value):
    from overnight_quant.data import quote_observation_permit
    # Deliberately invalid test-only reference: this file cannot authorize a live call.
    monkeypatch.setattr(quote_observation_permit, "_check_approval_reference", lambda *a: None)
    output = tmp_path / "not-a-live-output"
    auth = {"schema": ths.PERMISSION, "status": "USER_APPROVED", "contract_hash": ths.CONTRACT,
        "target_date": "2026-11-20", "approved_sha": HEAD, "batch_id": "test-only",
        "output": str(output), "worktree": str(ths.ROOT), "max_requests": 1,
        "purpose": "PERSONAL_INTERNAL_SINGLE_DAY_STRUCTURE_SAMPLE", "source_use_basis": "UNKNOWN",
        "known_use_constraint": "NO_KNOWN_PROHIBITION", "user_approved": True,
        "acknowledges_unknown_basis": True, "non_public": True, "single_use": True,
        "no_access_control_bypass": True, "window_start": "2026-11-21T09:00:00+08:00",
        "window_end": "2026-11-21T10:10:00+08:00", "approval_reference": {
            "test_only_not_valid_reference": True, "operator_check": {"checked_at": "2026-11-21T08:59:00+08:00"}}}
    if field:
        auth[field] = value
    path = tmp_path / "test-only-unusable-permission.json"
    path.write_text(json.dumps(auth), encoding="utf-8")
    args = dict(target="2026-11-20", head=HEAD, output=output, batch="test-only",
                evaluated_at=ths.stamp("2026-11-21T10:00:00+08:00"))
    if field:
        with pytest.raises(ValueError):
            ths.check_permission(path, ths.digest(path.read_bytes()), **args)
    else:
        assert ths.check_permission(path, ths.digest(path.read_bytes()), **args)["source_use_basis"] == "UNKNOWN"


def test_direct_controller_rejected_without_supervising_entry(tmp_path):
    run = subprocess.run([sys.executable, "-m", "overnight_quant.scripts.run_ths_single_sample", "--controller"],
        input=b'{"execute":true}', capture_output=True, cwd=ths.ROOT, timeout=5)
    assert run.returncode == 2
    assert "supervising_entry_required" in json.loads(run.stdout)["error"]


def test_launcher_serialization_overrun_never_publishes_success(tmp_path):
    run, result, output, returned = run_cli(tmp_path, fixture_file(tmp_path), extra=("-TestScenario", "SerializationOverrun"))
    assert run.returncode == 2 and result["status"] == "FAILED"
    assert "receipt_serialization_deadline" in result["error"]
    completion_sha = ths.digest((output / "completion.json").read_bytes())
    verified = ths.verify(output, completion_sha, returned, ths.digest(returned.read_bytes()))
    assert verified["evidence_integrity_verified"] is True
    assert verified["capture_execution_result"] == "FAILED"
