"""Actual isolated PowerShell inspectors; no real services, tasks, NTP or sources."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from overnight_quant.data import minute_capture_preflight as pre
from overnight_quant.scripts.run_complete_industry_audit_manual import _verify_environment


HARNESS = Path(__file__).parent / "fixtures/t31_environment_behavior_harness.ps1"
SHELLS = list(dict.fromkeys(p for p in (shutil.which("powershell.exe"), shutil.which("pwsh.exe")) if p))


def run(shell, tmp_path, mode, target=None):
    ops, temp = tmp_path / "synthetic ops", tmp_path / "synthetic temp"
    ops.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    (ops / "intent.json").write_text(json.dumps({"created_at": datetime.now(pre.audit.CN).isoformat(),
        "environment_observation_since": datetime.now(pre.audit.CN).isoformat(), "test_only": True}), encoding="utf-8")
    command = [shell, "-NoProfile", "-NonInteractive", "-File", str(HARNESS), "-Script", str(pre.ENV_SCRIPT),
        "-OpsDirectory", str(ops), "-OutputPath", str(ops / "environment.json"),
        "-TempDirectory", str(temp), "-Mode", mode, "-HashUnavailable", "-MinuteCapture",
        "-ProjectRoot", str(tmp_path), "-MinuteCaptureRoot", str(tmp_path / "capture"), "-TradeDate", "2026-10-21"]
    if target is not None:
        command += ["-HashTarget", str(target)]
    result = subprocess.run(command, capture_output=True, timeout=20)
    path = ops / "environment.json"
    value = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else json.loads(result.stdout.decode("utf-8-sig"))
    return result, value, ops


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("payload", [b"", bytes(range(256)) * 64, "synthetic bytes, not market evidence".encode()],
                         ids=["empty", "binary", "text"])
def test_actual_dotnet_hash_matches_python_and_releases_file(tmp_path, shell, payload):
    target = tmp_path / "non-ASCII-\u6587\u4ef6-\u8def\u5f84.bin"
    target.write_bytes(payload)
    result, value, _ = run(shell, tmp_path, "hash-file", target)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert value["sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(value["sha256"]) == 64 and value["handles_released"] is True
    assert target.read_bytes() == payload


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("failure", ["missing", "locked", "directory"])
def test_hash_read_failure_never_returns_a_digest(tmp_path, shell, failure):
    target = tmp_path / "target.bin"
    if failure == "locked":
        target.write_bytes(b"synthetic protected file")
    elif failure == "directory":
        target.mkdir()
    result, value, _ = run(shell, tmp_path, "hash-locked" if failure == "locked" else "hash-file", target)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert value["error"] and "sha256" not in value
    if failure == "locked":
        # The harness releases its deliberate deny-read handle even on failure.
        assert target.read_bytes() == b"synthetic protected file"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("kind", ["none", "normal", "pending", "corrupt", "empty", "binary", "lock", "read_failure"])
def test_real_inspector_without_hash_cmdlet_preserves_all_blockers(tmp_path, shell, kind):
    temp = tmp_path / "synthetic temp"
    temp.mkdir()
    target = None
    original = None
    if kind == "lock":
        target = temp / "a-stock-t25-global-executor.lock"
        original = b"synthetic live executor lock"
        target.write_bytes(original)
    elif kind != "none":
        directory = temp / "http-start-state"
        directory.mkdir()
        host = "push2.eastmoney.com"
        target = directory / (hashlib.sha256(host.encode()).hexdigest() + ".json")
        original = (json.dumps({"version": "http_client_call_spacing_v1", "host_name": host,
            "pending": kind == "pending", "last_call_end_seconds": 0}) + "\n").encode()
        if kind == "corrupt": original = b"{torn"
        if kind == "empty": original = b""
        if kind == "binary": original = b"\xff\xfe\x00\x81"
        target.write_bytes(original)
    result, value, ops = run(shell, tmp_path, "journal-read-failure" if kind == "read_failure" else "success", target)
    accepted = kind in ("none", "normal")
    assert result.returncode == (0 if accepted else 2), (result.stdout, result.stderr)
    if accepted:
        _verify_environment(value)
        assert value["blockers"] == [] and value["service_running"] is True
    elif kind == "read_failure":
        assert value["status"] == "ENVIRONMENT_INSPECTION_FAILED"
        assert value["service_running"] is None and value["service_check_status"] == "NOT_QUERIED"
        with pytest.raises(ValueError): _verify_environment(value)
    else:
        assert value["blockers"]
        with pytest.raises(ValueError): _verify_environment(value)
    if target is not None:
        assert target.read_bytes() == original
        if kind != "read_failure":
            group = value["locks"] if kind == "lock" else value["journals"]
            assert group[0]["sha256"] == hashlib.sha256(original).hexdigest()
            if not accepted:
                copies = list(ops.glob("*-" + target.name))
                assert len(copies) == 1 and copies[0].read_bytes() == original
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_inspection_exception_cannot_claim_observed_stopped_service(tmp_path, shell):
    result, value, _ = run(shell, tmp_path, "denied")
    assert result.returncode == 2
    assert value["status"] == "ENVIRONMENT_INSPECTION_FAILED"
    assert value["service_running"] is None and value["service_check_status"] == "NOT_QUERIED"
    assert value["inspection_succeeded"] is False
    with pytest.raises(ValueError): _verify_environment(value)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("mode,running,status", [("service-query-error", None, "QUERY_FAILED"),
    ("identity-query-error", True, "OBSERVED"), ("service-stopped", False, None)])
def test_failed_query_unknown_and_observed_service_states_remain_distinct(tmp_path, shell, mode, running, status):
    result, value, _ = run(shell, tmp_path, mode)
    assert result.returncode == 2
    assert value["service_running"] is running
    if status is not None:
        assert value["inspection_succeeded"] is False and value["service_check_status"] == status
    else:
        assert any("not Running" in item for item in value["blockers"])
    with pytest.raises(ValueError): _verify_environment(value)
