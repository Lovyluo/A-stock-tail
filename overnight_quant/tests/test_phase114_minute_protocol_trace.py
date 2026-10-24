"""Finite local-only protocol comparisons; never source qualification evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import time

import pytest

from overnight_quant.data import minute_capture_audit as legacy
from overnight_quant.data import minute_protocol_audit as audit
from overnight_quant.data import minute_protocol_contract as contract
from overnight_quant.data.minute_protocol_trace import BoundedEmitter, MAX_OUTPUT_BYTES, ProtocolTrace, TraceLimitError
from overnight_quant.data.minute_local_prepare_process import run_prepared_wave
from overnight_quant.tests.fixtures.minute_protocol_fixture import SegmentSocket, body, cases

FIXTURE = Path(__file__).parent / "fixtures/minute_protocol_fixture.py"
CLI = Path(__file__).parents[1] / "scripts/run_minute_protocol_diagnostic.py"


@pytest.fixture(autouse=True)
def no_source_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("phase114 forbids real network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def parser_class():
    from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
    return GetSecurityBarsCmd


def exercise(name, enabled):
    cls = parser_class()
    chunks = cases()[name] if name != "max_material" else [
        struct.pack("<IIIHH", 0, 0, 0, 65535, 65535), body()+b"\x00"*(65535-len(body()))]
    sock = SegmentSocket(chunks)
    parser = cls(sock)
    parser.setParams(8, 0, "000001", 0, 800)
    output = io.BytesIO()
    emitter = BoundedEmitter(output.write, deadline=lambda: float("inf"))
    trace = ProtocolTrace(deadline=float("inf"), emit=emitter)
    from contextlib import nullcontext
    with trace.hooks(SegmentSocket, cls) if enabled else nullcontext():
        try:
            result = ("ok", parser.call_api())
        except Exception as exc:
            result = (type(exc).__name__, str(exc))
    return result, sock.calls, trace, output.getvalue()


@pytest.mark.parametrize("name", list(cases()))
def test_nine_fixtures_send_recv_result_and_exception_equivalence(name):
    cls = parser_class()
    before = (SegmentSocket.send, SegmentSocket.recv, cls.parseResponse)
    off, old_calls, _, _ = exercise(name, False)
    on, new_calls, trace, raw = exercise(name, True)
    assert off == on and old_calls == new_calls
    assert (SegmentSocket.send, SegmentSocket.recv, cls.parseResponse) == before
    assert trace.hooks_restored and len(raw) < MAX_OUTPUT_BYTES
    summary = trace.summary()["interactions"][0]
    if name == "split_header":
        assert summary["header_captured_length"] == 8 and not summary["header_complete"]
        assert summary["declared_zip_length"] is None and summary["body_classification"] == "UNKNOWN"
        assert summary["parser_input_length"] is None
    if name == "short":
        assert on == ("error", "unpack requires a buffer of 4 bytes")
        assert summary["declared_zip_length"] == summary["received_body_length"] == summary["parser_input_length"] == 2
    if name == "wrong_unzip":
        assert summary["declared_unzip_length"] == len(body()) + 1
        assert summary["parser_input_length"] == len(body())


def test_limit_calls_materials_and_hooks_restore():
    cls = parser_class()
    original = SegmentSocket.send, SegmentSocket.recv, cls.parseResponse
    trace = ProtocolTrace(deadline=float("inf"))
    sock = SegmentSocket([])
    with pytest.raises(TraceLimitError, match="interaction_limit"), trace.hooks(SegmentSocket, cls):
        for _ in range(5):
            sock.send(b"x")
    assert len(sock.calls) == 4 and not trace.summary()["complete"]
    trace = ProtocolTrace(deadline=float("inf"))
    sock = SegmentSocket([b"a"] * 40)
    with pytest.raises(TraceLimitError, match="recv_metadata_limit"), trace.hooks(SegmentSocket, cls):
        sock.send(b"x")
        for _ in range(33):
            sock.recv(1)
    assert len(sock.calls) == 33  # one send and exactly 32 real recv calls
    assert (SegmentSocket.send, SegmentSocket.recv, cls.parseResponse) == original
    trace = ProtocolTrace(deadline=float("inf"))
    sock = SegmentSocket([b"x" * 70000])
    with pytest.raises(TraceLimitError, match="received_material_limit"), trace.hooks(SegmentSocket, cls):
        sock.send(b"x")
        sock.recv(70000)
    blob = trace.interactions[0]["recv"][0]["data"]
    assert not blob["capture_complete"] and blob["returned_length"] == 70000
    assert blob["captured_length"] == 65551 and not trace.summary()["complete"]
    trace = ProtocolTrace(deadline=float("inf"))
    sock = SegmentSocket([])
    with pytest.raises(TraceLimitError, match="send_material_limit"), trace.hooks(SegmentSocket, cls):
        sock.send(b"x"*4097)
    assert not sock.calls
    trace = ProtocolTrace(deadline=float("inf"))
    sock = SegmentSocket([])
    with pytest.raises(TraceLimitError, match="parser_material_limit"), trace.hooks(SegmentSocket, cls):
        sock.send(b"x")
        parser = cls(sock)
        parser.parseResponse(b"x"*65536)
    assert not trace.interactions[0]["parser_inputs"][0]["data"]["capture_complete"]


def test_output_limit_write_failure_and_deadline_do_not_succeed():
    stream = io.BytesIO()
    emit = BoundedEmitter(stream.write, deadline=lambda: float("inf"))
    with pytest.raises(TraceLimitError, match="total_output_limit"):
        emit({"large": "x" * MAX_OUTPUT_BYTES})
    assert not stream.getvalue() and emit.failed
    def broken(raw):
        raise OSError("test-only write failure")
    cls = parser_class()
    original = SegmentSocket.send, SegmentSocket.recv, cls.parseResponse
    trace = ProtocolTrace(deadline=float("inf"), emit=BoundedEmitter(broken, deadline=lambda: float("inf")))
    sock = SegmentSocket([])
    with pytest.raises(OSError, match="write failure"), trace.hooks(SegmentSocket, cls):
        sock.send(b"x")
    assert not sock.calls and trace.failure == "protocol_trace_output_failed"
    assert (SegmentSocket.send, SegmentSocket.recv, cls.parseResponse) == original
    tick = iter([0, 2])
    emit = BoundedEmitter(stream.write, deadline=lambda: 1, monotonic=lambda: next(tick))
    with pytest.raises(TimeoutError, match="completed_after_work_deadline"):
        emit({"test_only": True})
    assert emit.failed


def test_original_send_and_recv_exceptions_are_not_replaced():
    cls = parser_class()
    error = OSError("test-only original")
    class Broken(SegmentSocket):
        def recv(self, count):
            raise error
    sock = Broken([])
    trace = ProtocolTrace(deadline=float("inf"))
    with trace.hooks(Broken, cls):
        sock.send(b"x")
        with pytest.raises(OSError) as captured:
            sock.recv(16)
        assert captured.value is error
    assert trace.interactions[0]["recv"][0]["exception_type"] == "OSError"
    assert "send" not in Broken.__dict__  # inherited descriptor is restored, not replaced
    class BrokenSend(SegmentSocket):
        def send(self, packet):
            raise error
    trace = ProtocolTrace(deadline=float("inf"))
    with trace.hooks(BrokenSend, cls):
        with pytest.raises(OSError) as captured:
            BrokenSend([]).send(b"x")
        assert captured.value is error
    assert trace.interactions[0]["send"]["exception_type"] == "OSError"


def test_finite_paired_overhead_report(tmp_path):
    measurements = []
    peak, size = 0, 0
    for _ in range(3):
        for name in [*cases(), "max_material"]:
            started = time.perf_counter_ns()
            off = exercise(name, False)
            baseline = time.perf_counter_ns() - started
            started = time.perf_counter_ns()
            on = exercise(name, True)
            enabled = time.perf_counter_ns() - started
            assert off[:2] == on[:2]
            measurements.append({"fixture": name, "disabled_ms": baseline/1e6, "enabled_ms": enabled/1e6,
                                 "extra_ms": (enabled-baseline)/1e6})
            peak = max(peak, on[2].retained_bytes)
            size = max(size, len(on[3]))
    result = {"test_only": True, "fixed_pairs": len(measurements), "source_requests": 0,
              "p50_extra_ms": statistics.median(x["extra_ms"] for x in measurements),
              "max_extra_ms": max(x["extra_ms"] for x in measurements), "peak_material_bytes": peak,
              "maximum_io_output_bytes": size, "measurements": measurements,
              "real_TCP_budget": "CAPTURE_BUDGET_FEASIBILITY_UNPROVEN"}
    (tmp_path / "overhead.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def task(tmp_path):
    raw = json.dumps({"SERVER": {"HQ": [["test-only", "59.36.5.11", 7709]]},
                      "BESTIP": {"HQ": ["59.36.5.11", 7709]}}).encode()
    config = tmp_path / "config.json"
    config.write_bytes(raw)
    start = time.perf_counter()
    now = datetime.now(timezone(timedelta(hours=8)))
    return contract.task_for(now+timedelta(seconds=4.99), actual=now, started=start,
        batch_id="synthetic-protocol-only", code_sha="a"*40, config={"path": str(config), "sha256": sha256(raw).hexdigest()},
        test_only=True)


@pytest.mark.parametrize("mode", ["complete", "large", "short", "timeout", "flood"])
def test_real_child_prepare_GO_capture_and_bounded_cleanup(tmp_path, mode):
    result = run_prepared_wave(task(tmp_path), contract_profile=contract,
                              worker_command=[sys.executable, str(FIXTURE), mode])
    (tmp_path / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert result["cleanup_complete"] and result["tree_empty_observed"]
    assert result["pipes_closed"] and result["stdout_eof"] and result["stderr_eof"]
    assert result["cleanup_ms"] < 500 and result["total_residence_ms"] < 7000
    assert len(result["stdout"].encode())+len(result["stderr"].encode()) <= MAX_OUTPUT_BYTES
    value = audit.make_evidence(result, test_only=True)
    verified = audit.verify(value)
    assert verified["evidence_integrity_verified"], verified
    assert verified == audit.verify(value)
    if mode in {"complete", "short"}:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        anchor = sha256(path.read_bytes()).hexdigest()
        replays = []
        for name in ("a", "b"):
            replay = tmp_path / f"replay-{name}.json"
            cli = subprocess.run([sys.executable, str(CLI), "--verify", str(path),
                "--expected-file-sha256", anchor, "--replay-output", str(replay)],
                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            assert cli.returncode == (0 if mode == "complete" else 2), cli.stderr
            assert sha256(path.read_bytes()).hexdigest() == anchor
            replays.append(replay.read_bytes())
        assert replays[0] == replays[1]
    events = legacy.events_from_result(result)
    ready = next(e for e in events if e["kind"] == "READY_LOCAL_ONLY")
    assert ready["network_attempts_before_go"] == 0 and result["go_sent_monotonic"] is not None
    if mode in {"complete", "large"}:
        assert result["ok"] and verified["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_CAPTURED", result
        assert len(verified["trace_summary"]["interactions"]) == 4
        assert result["work_elapsed_ms"] < 1500
        assert result["output_frozen_before_work_deadline"] and result["output_freeze_ms"] >= 0
        altered = deepcopy(value)
        terminal = json.loads(altered["result"]["stdout"].splitlines()[-1])
        terminal["payload"]["trace_summary"]["interactions"][-1]["received_body_length"] += 1
        lines = altered["result"]["stdout"].splitlines()
        lines[-1] = json.dumps(terminal)
        altered["result"]["stdout"] = "\n".join(lines)+"\n"
        altered["result"]["stdout_sha256"] = sha256(altered["result"]["stdout"].encode()).hexdigest()
        altered["evidence_hash"] = legacy.digest({k:v for k,v in altered.items() if k!='evidence_hash'})
        assert not audit.verify(altered)["evidence_integrity_verified"]
        malformed = deepcopy(value)
        malformed["result"]["stdout"] = 'invalid JSON\n'+malformed["result"]["stdout"]
        malformed["result"]["stdout_sha256"] = sha256(malformed["result"]["stdout"].encode()).hexdigest()
        malformed["evidence_hash"] = legacy.digest({k:v for k,v in malformed.items() if k!='evidence_hash'})
        assert not audit.verify(malformed)["evidence_integrity_verified"]
    else:
        assert not result["ok"] and verified["diagnostic_result"] == "PROTOCOL_DIAGNOSTIC_FAILED"
        if mode == "short":
            assert verified["trace_summary"]["interactions"][-1]["parser_input_length"] == 2
        if mode == "timeout":
            assert result["request_timed_out"] and result["worker_terminated"]
            assert result["output_complete"] is False
        if mode == "flood":
            assert result["output_complete"] is False


def test_default_and_validate_only_cli_never_start_workers(tmp_path, monkeypatch, capsys):
    from overnight_quant.scripts.run_minute_protocol_diagnostic import main
    def forbidden(*a, **k):
        raise AssertionError("CLI may not start a worker")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert main([]) == main(["--validate-only"]) == 0
    assert main(["--validate-only", "--plan-output", str(tmp_path/'plan.json'),
                 "--authorization-draft-output", str(tmp_path/'authorization.json')]) == 0
    assert json.loads((tmp_path/'authorization.json').read_text())["status"] == "DRAFT_NOT_AUTHORIZED"
    assert main(["--validate-only", "--plan-output", str(tmp_path/'plan.json')]) == 2
    assert contract.frozen_plan()["codes"] == ["000001"]
    assert legacy.frozen_plan()["bars_requests_total"] == 20
    assert 'source_requests' in capsys.readouterr().out
    with pytest.raises(SystemExit) as stopped:
        main(["--network"])
    assert stopped.value.code == 2  # no real-network route is shipped in this draft


def test_old_authorization_plan_and_tasks_cannot_select_diagnostic(tmp_path):
    with pytest.raises(ValueError, match="plan_mismatch"):
        contract.check_plan(legacy.frozen_plan())
    with pytest.raises(ValueError, match="independent_protocol_PM_authorization_required"):
        audit.validate_authorization({"schema":"minute_capture_manual_authorization_v3"}, code_sha='a'*40,
            now=datetime.now(timezone(timedelta(hours=8))))
    value = task(tmp_path)
    value["io_deadline_monotonic"] += .001
    with pytest.raises(ValueError, match="deadline_contract"):
        run_prepared_wave(value, contract_profile=contract)


def test_plan_is_detached_and_new_worker_default_rejects_synthetic_scope(tmp_path):
    plan = contract.frozen_plan()
    plan["codes"].append("600519")
    plan["endpoint"]["host"] = "test-only.invalid"
    assert contract.frozen_plan()["codes"] == ["000001"]
    assert legacy.ENDPOINT["host"] == "59.36.5.11"
    result = run_prepared_wave(task(tmp_path), contract_profile=contract)
    assert not result["ok"] and "test_scope_mismatch" in result["error"]
    assert result["cleanup_complete"] and result["go_sent_monotonic"] is None
    assert not any(e.get("stage") == "local_dependency_imports" for e in legacy.events_from_result(result))


def test_future_authorization_binding_never_replaces_operational_review(tmp_path):
    now = datetime.now(timezone(timedelta(hours=8)))
    material = tmp_path / "binding-only.json"
    material.write_text('{"test_only":true}', encoding="utf-8")
    reference = {"path": str(material), "sha256": sha256(material.read_bytes()).hexdigest()}
    value = {**audit.authorization_draft('a'*40), "status":"PM_APPROVED_SINGLE_PROTOCOL_DIAGNOSTIC",
             "trade_date":now.date().isoformat(), "target_at":(now+timedelta(minutes=1)).isoformat(),
             "approval_source":"synthetic binding test only", "preflight":reference, "local_config":reference}
    result = audit.validate_authorization(value, code_sha='a'*40, now=now)
    assert result["bindings_valid"] and not result["sampling_authorized"]
    assert result["operational_preflight_review_still_required"]
    for mutation in [dict(value, trade_date="2000-01-01"), dict(value, target_at=now.isoformat()),
                     dict(value, preflight={**reference,"sha256":"0"*64}), audit.authorization_draft('a'*40)]:
        with pytest.raises(ValueError):
            audit.validate_authorization(mutation, code_sha='a'*40, now=now)
