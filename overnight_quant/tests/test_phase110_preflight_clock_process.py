"""Real PS5.1/7 -> real local process -> original inspector receipts, no NTP."""
import base64
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

import pytest

from overnight_quant.data import minute_capture_preflight as pre
from overnight_quant.data.probe_worker_process import run_probe_worker_process


FIXTURES = Path(__file__).parent / "fixtures"
SHELLS = list(dict.fromkeys(p for p in (shutil.which("powershell.exe"), shutil.which("pwsh.exe")) if p))


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("mode", ["zero", "nonzero", "timeout", "output", "start-failure", "null"])
def test_actual_clock_process_exit_streams_budget_and_reaping(tmp_path, shell, mode):
    ops, temp = tmp_path / "synthetic ops", tmp_path / "synthetic temp"
    ops.mkdir(); temp.mkdir()
    (ops / "intent.json").write_text(json.dumps({"created_at": datetime.now(pre.audit.CN).isoformat(),
        "environment_observation_since": datetime.now(pre.audit.CN).isoformat(), "test_only": True}), encoding="utf-8")
    command = [shell, "-NoProfile", "-NonInteractive", "-File", str(FIXTURES / "t31_environment_behavior_harness.ps1"),
        "-Script", str(pre.ENV_SCRIPT), "-OpsDirectory", str(ops), "-OutputPath", str(ops / "environment.json"),
        "-TempDirectory", str(temp), "-Mode", "clock-null" if mode == "null" else "success", "-MinuteCapture",
        "-ProjectRoot", str(tmp_path), "-MinuteCaptureRoot", str(tmp_path / "capture"), "-TradeDate", "2026-10-21"]
    if mode != "null":
        command += ["-ClockExecutable", str(tmp_path / "absent.exe") if mode == "start-failure" else sys.executable,
            "-ClockArguments", f'"{FIXTURES / "clock_status_process_fixture.py"}" {mode} "{tmp_path / "child.pid"}"']
    result = run_probe_worker_process({}, 10500, worker_command=command, retain_streams=True, bounded_cleanup_ms=500)
    (ops / "process-receipt.json").write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
    assert result["cleanup_complete"] and result["returncode"] is not None, result
    assert result["tree_empty_observed"] and result["pipes_closed"], result
    assert (ops / "environment.json").exists(), (result["returncode"], result.get("stdout"), result.get("stderr"))
    value = json.loads((ops / "environment.json").read_text(encoding="utf-8-sig"))
    if mode in ("zero", "nonzero", "output"):
        assert result["returncode"] == 0, result
        query = value["clock_query"]
        assert query["exit_code"] == (7 if mode == "nonzero" else 0)
        stdout, stderr = (base64.b64decode(query[key + "_base64"]) for key in ("stdout", "stderr"))
        assert stdout == (ops / "clock-status.stdout.bin").read_bytes()
        assert stderr == (ops / "clock-status.stderr.bin").read_bytes()
        assert b"Leap Indicator: 0" in stdout and b"synthetic stderr retained" in stderr
        if mode == "nonzero":
            with pytest.raises(ValueError, match="clock_status_query_failed"): pre.clock_status(value)
        else:
            assert pre.clock_status(value)["error"] == 0
        if mode == "output":
            assert stdout.count(b"synthetic output line") == stderr.count(b"synthetic error line") == 1000
    else:
        assert result["returncode"] == 2, result
        assert value["status"] == "ENVIRONMENT_INSPECTION_FAILED" and value["service_running"] is True
        assert value["inspection_succeeded"] is False
        if mode == "timeout":
            assert any("timed out" in b for b in value["blockers"])
            assert b"Leap Indicator" in (ops / "clock-status.stdout.bin").read_bytes()
        if mode == "null":
            assert any("exit code unavailable" in b for b in value["blockers"])
    assert not (tmp_path / "capture").exists()
