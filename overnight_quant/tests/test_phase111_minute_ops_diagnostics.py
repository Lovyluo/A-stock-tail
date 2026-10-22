"""Offline MINUTE-OPS-1 checks; synthetic inputs never authorize source capture."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import struct
import sys
import time

import pytest

from overnight_quant.data import minute_capture_audit as audit
from overnight_quant.data import minute_capture_worker as worker
from overnight_quant.data.probe_worker_process import run_probe_worker_process

FIXTURE = Path(__file__).parent / "fixtures/minute_ops_worker_fixture.py"


def run_native_fixture(tmp_path, mode):
    config = tmp_path / "config.json"
    if mode == "corrupt":
        config.write_text("{test_only invalid json", encoding="utf-8")
    elif mode != "missing":
        config.write_text(json.dumps({"SERVER": {"HQ": [["test-only", "59.36.5.11", 7709]]},
                                      "BESTIP": {"HQ": ["59.36.5.11", 7709]}}), encoding="utf-8")
    started = time.perf_counter()
    task = {"worker_version": worker.VERSION, "codes": list(worker.CODES),
            "endpoint": worker.ENDPOINT, "source_version": worker.SOURCE_VERSION,
            "trade_date": "2026-10-22", "wave": 0,
            "parent_started_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
            "parent_started_monotonic": started, "io_deadline_monotonic": started + 1.5}
    result = run_probe_worker_process(task, 2000,
        worker_command=[sys.executable, str(FIXTURE), mode, str(config)],
        retain_streams=True, bounded_cleanup_ms=500, hard_deadline_monotonic=started + 2)
    assert result["cleanup_complete"], result
    assert result["tree_empty_observed"] and result["pipes_closed"], result
    events = [json.loads(line) for line in result["stdout"].splitlines() if line.strip()]
    return result, events


def test_actual_parser_rejects_count_without_records():
    from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
    parser = GetSecurityBarsCmd(None)
    parser.setParams(8, 0, "000001", 0, 800)
    assert struct.unpack("<H", b"\x20\x03")[0] == 800
    with pytest.raises(struct.error, match="4 bytes"):
        parser.parseResponse(b"\x20\x03")


def test_real_child_complete_uses_installed_client_without_external_network(tmp_path):
    result, events = run_native_fixture(tmp_path, "complete")
    assert result["ok"], result
    assert result["payload"]["test_only"] is True
    assert result["payload"]["counts"] == {"external_network_attempts": 0, "connect": 1,
        "handshake": 3, "bars": 5, "discovery": 0}
    stages = [event for event in events if event.get("kind") == "client_stage"]
    assert {event["stage"] for event in stages} == {"dependency_versions", "dependency_imports",
        "factory_config_connect_handshake", "client_close"}
    assert all(event["elapsed_ms"] >= 0 for event in stages if event["state"] == "completed")
    assert all(event["raw_boundary"] == "tdxpy_parser_input_decompressed_response_body"
               for event in events if event.get("kind") == "response")


def test_real_child_preserves_vendor_original_exception_and_raw_body(tmp_path):
    result, events = run_native_fixture(tmp_path, "malformed")
    assert not result["ok"] and not result["request_timed_out"], result
    failed = next(event for event in events if event.get("kind") == "request_failed")
    assert [item["type"] for item in failed["exception_chain"]][:2] == [
        "tdxpy.exceptions.TdxFunctionCallError", "struct.error"]
    assert failed["exception_chain"][1]["relation"] == "original_exception"
    assert failed["exception_chain"][1]["frames"][-1]["function"] == "get_datetime"
    assert failed["wire_bodies"][0]["base64"] == "IAM="
    assert failed["failed_monotonic"] >= failed["call_monotonic"]
    assert failed["remaining_io_ms_at_failure"] >= 0
    terminal = next(event for event in reversed(events) if "ok" in event)
    assert terminal["counts"] == {"external_network_attempts": 0, "connect": 1,
        "handshake": 3, "bars": 1, "discovery": 0}


@pytest.mark.parametrize("mode", ["missing", "corrupt"])
def test_real_child_local_config_failure_never_enters_discovery(tmp_path, mode):
    result, events = run_native_fixture(tmp_path, mode)
    assert not result["ok"] and not result["request_timed_out"], result
    terminal = next(event for event in reversed(events) if "ok" in event)
    assert "native_local_config_unavailable_no_discovery" in terminal["error"]
    assert all(value == 0 for value in terminal["counts"].values())


def test_error_retained_when_close_blocks_and_parent_terminates(tmp_path):
    result, events = run_native_fixture(tmp_path, "slow_close")
    assert not result["ok"] and result["request_timed_out"] and result["worker_terminated"], result
    failed = next(event for event in events if event.get("kind") == "request_failed")
    assert failed["exception_chain"][1]["type"] == "struct.error"
    assert any(event.get("kind") == "fixture_close_block_started" for event in events)
    assert not any(event.get("stage") == "client_close" and event.get("state") == "completed"
                   for event in events)


def test_terminal_error_does_not_bypass_normal_exit_deadline(tmp_path):
    result, events = run_native_fixture(tmp_path, "slow_exit")
    assert not result["ok"] and result["request_timed_out"] and result["worker_terminated"], result
    terminal = next(event for event in reversed(events) if "ok" in event)
    assert terminal["ok"] is False and terminal["original_exception_type"] == "error"
    assert terminal["counts"]["external_network_attempts"] == 0
    assert any(event.get("stage") == "client_close" and event.get("state") == "completed"
               for event in events)


@pytest.mark.parametrize("failure", ["shape", "permission"])
def test_config_guard_restores_vendor_hooks_after_failure(tmp_path, monkeypatch, failure):
    import mootdx.config as config
    import mootdx.quotes as quotes
    from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
    path = tmp_path / "local-config.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config, "CONF", str(path))
    setup, parser = config.setup, GetSecurityBarsCmd.parseResponse
    def forbidden(*args, **kwargs):
        raise AssertionError("network/discovery forbidden")
    monkeypatch.setattr(config, "bestip", forbidden)
    def factory(**kwargs):
        config.setup()
        raise AssertionError("invalid local configuration must reject first")
    monkeypatch.setattr(quotes.Quotes, "factory", factory)
    if failure == "permission":
        original_read = Path.read_bytes
        def denied(self):
            if self == path:
                raise PermissionError("test-only denied")
            return original_read(self)
        monkeypatch.setattr(Path, "read_bytes", denied)
    with pytest.raises(ValueError, match="native_local_config_.*_no_discovery"):
        worker.NativeClient(emit=lambda event: None)
    assert config.setup is setup and GetSecurityBarsCmd.parseResponse is parser


def test_exception_chain_is_bounded_and_cycle_safe():
    error = ValueError("x" * 2000)
    error.original_exception = error
    chain = worker.exception_chain(error)
    assert len(chain) == 1 and len(chain[0]["message"]) == 512
    for _ in range(20):
        wrapped = RuntimeError("test-only wrapper")
        wrapped.original_exception, error = error, wrapped
    assert len(worker.exception_chain(error)) == 8


def test_worker_main_keeps_original_chain_on_failure(monkeypatch, capsys):
    import io
    from tdxpy.exceptions import TdxFunctionCallError
    original = struct.error("test-only missing body")
    wrapped = TdxFunctionCallError("calling function error")
    wrapped.original_exception = original
    def failed(task):
        raise wrapped
    monkeypatch.setattr(worker, "capture_wave", failed)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert worker.main() == 2
    value = json.loads(capsys.readouterr().out)
    assert value["ok"] is False
    assert value["exception_chain"][1]["type"] == "struct.error"


def test_no_execution_contract_or_qualification_change():
    plan = audit.frozen_plan()
    assert plan["plan_hash"] == "6049fba42098a6f616af898cb3fd2b98e63c5bd393b22bf702a10a5f503c2dae"
    assert plan["process_io_budget_ms"] == 1500
    assert plan["termination_cleanup_reserve_ms"] == 500
    assert plan["bars_requests_total"] == 20 and plan["transaction_requests"] == 0
    assert plan["concurrency"] == 1 and plan["remaining_budget_proven"] is False
