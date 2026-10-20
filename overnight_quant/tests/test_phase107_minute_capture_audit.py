from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from overnight_quant.data import minute_capture_audit as audit
from overnight_quant.data import minute_capture_worker as worker
from overnight_quant.data import probe_worker_process as process
from overnight_quant.scripts.run_minute_capture_audit import main

FIXTURE = Path(__file__).parent / "fixtures/minute_capture_worker_fixture.py"
DAY = "2026-10-21"  # synthetic test date; never a trading-day assertion


class Clock:
    def __init__(self, text="14:49:55"):
        self.base = datetime.fromisoformat(f"{DAY}T{text}+08:00")
        self.start = time.monotonic()

    def __call__(self):
        return self.base + timedelta(seconds=time.monotonic() - self.start)

    def sleep(self, seconds):
        self.base += timedelta(seconds=seconds)


def context(mode="complete", clock=None, extra=()):
    clock = clock or Clock()
    return {"test_only": True, "clock": clock, "sleep": clock.sleep,
            "worker_command": [sys.executable, str(FIXTURE), mode, *map(str, extra)]}


def run(tmp_path, mode="complete", **kwargs):
    return audit.capture(audit.frozen_plan(), DAY, authorization={"synthetic_test_only": True},
                         audit_root=tmp_path, _test=context(mode, **kwargs))


def assert_safe(value):
    assert value["data_ready"] is False
    assert value["hard_gate_authorized"] is False
    assert value["sampling_authorized"] is False
    for key in ("records", "research_ranking", "candidates", "tickets", "orders"):
        assert value[key] == []


def test_frozen_undated_plan_exact_budget():
    plan = audit.frozen_plan()
    assert plan["trade_date"] is None and plan["execution_authorized"] is False
    assert plan["bars_requests_total"] == 20 and plan["transaction_requests"] == 0
    assert plan["process_io_budget_ms"] + plan["termination_cleanup_reserve_ms"] == 2000
    assert plan["concurrency"] == 1 and plan["eastmoney_500ms_rule_applied"] is False
    assert plan["remaining_budget_proven"] is False
    assert plan["plan_hash"] == worker.digest({k: v for k, v in plan.items() if k != "plan_hash"})


@pytest.mark.parametrize("args", [[], ["--validate-only"]])
def test_default_and_validate_no_children_or_network(monkeypatch, capsys, args):
    def forbidden(*args, **kwargs):
        raise AssertionError("child/network forbidden")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(worker, "NativeClient", forbidden)
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["network_requests_made"] == result["child_processes_started"] == 0
    assert_safe(result)


def test_real_child_complete_all_native_rows_and_two_replays(tmp_path):
    evidence, path = run(tmp_path)
    assert evidence["summary"]["status"] == "MINUTE_CAPTURE_AUDIT_COMPLETED_SEMANTICS_UNPROVEN"
    assert evidence["summary"]["requests_attempted"] == 20
    assert len(evidence["summary"]["row_stability"]) == 60
    assert all(row["observed_stable"] and not row["is_final"] and row["finality"] == "UNPROVEN"
               for row in evidence["summary"]["row_stability"])
    for wave in evidence["waves"]:
        events = audit.events_from_result(wave["result"])
        responses = [e for e in events if e["kind"] == "response"]
        assert len(responses) == 5
        assert all(len(e["native_rows"]) == 13 and e["native_rows"][0]["native_extra"]["retained"] for e in responses)
        assert all(e["native_rows"][-1]["datetime"].endswith("14:51") for e in responses)
        assert all(e["wire_bodies"][0]["base64"] for e in responses)
    before = path.read_bytes()
    anchor = sha256(before).hexdigest()
    first = audit.verify(evidence, anchor, anchor)
    assert first["integrity_verified"] is True
    assert first == audit.verify(evidence, anchor, anchor)
    assert path.read_bytes() == before
    for result in (evidence, evidence["summary"], first):
        assert_safe(result)
    assert json.loads((path.parent / "seal-receipt.json").read_text())["late_seal"] is False
    assert all("process_startup_ms" in wave["result"] for wave in evidence["waves"])


@pytest.mark.parametrize("mode,reason", [("missing", "target_labels_missing"), ("pagination", "target_labels_missing"),
    ("duplicate", "duplicate_or_overlapping"), ("overlap", "annotations_overlap"), ("invalid", "native_values_invalid")])
def test_missing_duplicate_overlap_page_and_invalid_stop_first_wave(tmp_path, mode, reason):
    evidence, path = run(tmp_path, mode)
    assert evidence["summary"]["status"] == "MINUTE_CAPTURE_AUDIT_FAILED"
    assert len(evidence["waves"]) == 1
    assert any(reason in issue for issue in evidence["summary"]["issues"])
    anchor = sha256(path.read_bytes()).hexdigest()
    assert audit.verify(evidence, anchor, anchor)["integrity_verified"] is True
    assert_safe(evidence)


def test_revision_is_not_final_or_last_observation_success(tmp_path):
    evidence, _ = run(tmp_path, "revision")
    changed = [r for r in evidence["summary"]["row_stability"] if r["native_minute_label"] == "14:50"]
    assert len(changed) == 5
    assert all(r["changes_observed"] == 1 and not r["observed_stable"] and not r["is_final"] for r in changed)
    assert all(r["finality"] == "UNPROVEN" for r in changed)


@pytest.mark.parametrize("mode", ["late", "block"])
def test_real_process_timeout_keeps_completed_raw_stream_and_kills_tree(tmp_path, mode):
    pid_path = tmp_path / "child.pid"
    started = time.monotonic()
    evidence, path = run(tmp_path / "audit", mode, extra=(pid_path,))
    assert time.monotonic() - started < 6
    assert len(evidence["waves"]) == 1
    result = evidence["waves"][0]["result"]
    assert result["worker_terminated"] is True and result["request_timed_out"] is True
    assert result["returncode"] is not None
    if mode == "block":
        assert any(e.get("kind") == "response" for e in audit.events_from_result(result))
        assert pid_path.exists()
        pid = int(pid_path.read_text())
        if os.name == "nt":
            listing = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"], text=True)
            assert f'"{pid}"' not in listing
        else:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    anchor = sha256(path.read_bytes()).hexdigest()
    assert audit.verify(evidence, anchor, anchor)["integrity_verified"]
    assert_safe(evidence)


def test_missed_window_zero_new_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "run_probe_worker_process", lambda *a, **k: pytest.fail("must not start"))
    evidence, _ = run(tmp_path, clock=Clock("14:51:05"))
    assert evidence["waves"] == []
    assert "observation_window_missed" in evidence["capture_error"]
    assert_safe(evidence)


def test_duplicate_run_cannot_overwrite_failure_or_intent(tmp_path):
    evidence, path = run(tmp_path, "missing")
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        run(tmp_path)
    assert path.read_bytes() == before


def test_simultaneous_write_once_single_winner(tmp_path):
    target = tmp_path / "intent.json"
    code = ("from overnight_quant.data.mootdx_node_benchmark import write_benchmark_json_atomic as w;"
            "from pathlib import Path;import sys;w(Path(sys.argv[1]),{'synthetic_test_only':True})")
    processes = [subprocess.Popen([sys.executable, "-c", code, str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for p in processes:
        p.communicate(timeout=15)
    assert sorted(p.returncode for p in processes) == [0, 1]
    assert json.loads(target.read_text())["synthetic_test_only"] is True


def test_actual_simultaneous_capture_only_one_process_starts_waves(tmp_path):
    driver = FIXTURE.with_name("minute_capture_driver_fixture.py")
    barrier = tmp_path / "go"
    ready = [tmp_path / "ready-0", tmp_path / "ready-1"]
    processes = [subprocess.Popen([sys.executable, str(driver), str(tmp_path / "capture"), str(barrier), str(path)],
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for path in ready]
    try:
        limit = time.monotonic() + 15
        while not all(path.exists() for path in ready):
            assert time.monotonic() < limit
            time.sleep(.01)
        barrier.write_text("synthetic_test_only")
        for child in processes:
            _, stderr = child.communicate(timeout=20)
            assert child.returncode in (0, 3), stderr
        assert sorted(p.returncode for p in processes) == [0, 3]
        evidence = json.loads((tmp_path / "capture" / DAY / "evidence.json").read_text())
        assert len(evidence["waves"]) == 4 and evidence["summary"]["requests_attempted"] == 20
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill()
                child.communicate()


def test_native_missing_and_unrecognized_values_preserved_without_zero():
    value = worker.json_value({"amount": None, "vol": float("nan"), "unknown": object()})
    assert value["amount"] is None and value["vol"] == {"nonfinite_float": "nan"}
    assert "native_python_type" in value["unknown"]


def test_external_sha_tamper_and_resigned_summary_cannot_promote(tmp_path):
    evidence, path = run(tmp_path)
    anchor = sha256(path.read_bytes()).hexdigest()
    assert not audit.verify(evidence, "f" * 64, anchor)["integrity_verified"]
    altered = deepcopy(evidence)
    altered["summary"]["row_stability"][0]["is_final"] = True
    altered["evidence_hash"] = worker.digest({k: v for k, v in altered.items() if k != "evidence_hash"})
    assert not audit.verify(altered, anchor, anchor)["integrity_verified"]
    altered["records"] = [{"forged": True}]
    altered["evidence_hash"] = worker.digest({k: v for k, v in altered.items() if k != "evidence_hash"})
    assert not audit.verify(altered, anchor, anchor)["integrity_verified"]


def test_startup_is_subtracted_from_deadline_and_streams_retained(monkeypatch):
    class Process:
        returncode = 0
        def communicate(self, value, timeout):
            assert timeout == pytest.approx(.6)
            return '{"ok":true,"payload":{}}', ""
    ticks = iter([10., 10.9, 11.])
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **k: Process())
    result = process.run_probe_worker_process({}, 1500, monotonic=lambda: next(ticks),
        include_startup_in_deadline=True, retain_streams=True)
    assert result["ok"] and result["elapsed_ms"] == 1000 and "stdout" in result


def test_network_missing_authorization_is_closed(capsys):
    assert main(["--network"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "MINUTE_CAPTURE_REQUEST_REJECTED"
    assert_safe(result)


def test_actual_cli_independent_byte_equal_replays_and_no_overwrite(tmp_path):
    evidence, path = run(tmp_path / "input")
    raw = path.read_bytes()
    anchor = sha256(raw).hexdigest()
    script = audit.ROOT / "overnight_quant/scripts/run_minute_capture_audit.py"
    outputs = [tmp_path / "replay-a.json", tmp_path / "replay-b.json"]
    for output in outputs:
        result = subprocess.run([sys.executable, str(script), "--verify", str(path),
            "--expected-file-sha256", anchor, "--replay-output", str(output)],
            capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert path.read_bytes() == raw
    result = subprocess.run([sys.executable, str(script), "--verify", str(path),
        "--expected-file-sha256", anchor, "--replay-output", str(outputs[0])],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and outputs[0].read_bytes() == outputs[1].read_bytes()


def test_native_client_capture_hook_and_no_retry_flags(monkeypatch):
    import mootdx.quotes
    from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
    original = GetSecurityBarsCmd.parseResponse
    seen = {}
    class Client:
        def bars(self, **request):
            parser = GetSecurityBarsCmd(None)
            parser.parseResponse(b"\x00\x00")
            return "synthetic_frame"
        def close(self):
            seen["closed"] = True
    def factory(**kwargs):
        seen.update(kwargs)
        return Client()
    monkeypatch.setattr(mootdx.quotes.Quotes, "factory", factory)
    client = worker.NativeClient()
    try:
        assert client.bars(symbol="000001", frequency="1m", start=0, offset=800) == "synthetic_frame"
        assert client.bodies == [b"\x00\x00"] and client.body_times[0]["parser_received_at"]
        assert seen["server"] == ("59.36.5.11", 7709)
        assert seen["auto_retry"] is seen["heartbeat"] is seen["bestip"] is False
    finally:
        client.close()
    assert GetSecurityBarsCmd.parseResponse is original and seen["closed"]


def test_late_seal_fails_without_rewriting_original(tmp_path, monkeypatch):
    clock = Clock()
    original = audit.write
    def delayed(path, payload):
        result = original(path, payload)
        if Path(path).name == "file-anchor.json":
            clock.base += timedelta(seconds=20)
        return result
    monkeypatch.setattr(audit, "write", delayed)
    evidence, path = run(tmp_path, clock=clock)
    receipt = json.loads((path.parent / "seal-receipt.json").read_text())
    assert receipt["late_seal"] is True and receipt["status"] == "MINUTE_CAPTURE_AUDIT_FAILED"
    assert evidence["source_finality"] == "UNPROVEN"
    anchor = sha256(path.read_bytes()).hexdigest()
    assert audit.verify(evidence, anchor, anchor)["integrity_verified"]


@pytest.mark.parametrize("field,value", [("endpoint", {"host": "elsewhere"}), ("codes", ["000001"]),
    ("transaction_requests", 5), ("wave_total_deadline_ms", 3000), ("observations", ["14:51:05"])])
def test_plan_changes_rejected_before_worker(tmp_path, monkeypatch, field, value):
    plan = audit.frozen_plan()
    plan[field] = value
    monkeypatch.setattr(audit, "run_probe_worker_process", lambda *a, **k: pytest.fail("worker forbidden"))
    with pytest.raises(ValueError, match="frozen_plan_mismatch"):
        audit.capture(plan, DAY, authorization={}, audit_root=tmp_path, _test=context())
    assert not (tmp_path / DAY).exists()
