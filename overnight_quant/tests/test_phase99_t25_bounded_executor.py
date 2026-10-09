from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.t25_bounded_executor import (
    AuditRequest, AuditTimeConfiguration, BoundedAuditExecutor,
    integration_plan, schedule_budget, validate_plan,
)
from overnight_quant.data.t25_offline_rehearsal import run_local_chain


COMMAND = [sys.executable, "-m", "overnight_quant.data.t25_local_response_worker"]
ROOT = Path(__file__).resolve().parents[2]


def _job(key, host="push2.eastmoney.com", deps=(), release=None):
    return AuditRequest(key, f"https://{host}/test-only", {}, deps, release)


def _run(tmp_path, jobs, scenarios=None, seconds=15):
    return BoundedAuditExecutor()._run_for_test(
        jobs, deadline=datetime.now(CN_TZ) + timedelta(seconds=seconds), command=COMMAND,
        payloads=scenarios or {j.request_id: {"body": {"test_only": True}} for j in jobs},
        lock_path=tmp_path / "executor.lock")


def _safe(result):
    assert result["qualification"] == "0/3"
    assert result["data_ready"] is result["hard_gate_authorized"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_real_process_peak_host_spacing_queue_and_deterministic_sort(tmp_path):
    jobs = [_job(str(i)) for i in range(4)]
    result = _run(tmp_path, list(reversed(jobs)), {j.request_id: {"sleep_seconds": .8} for j in jobs})
    assert result["execution_ok"]
    assert result["max_in_flight"] == 2
    rows = result["results"]
    starts = [datetime.fromisoformat(r["request_started_at"]) for r in rows]
    assert all((b - a).total_seconds() >= .5 for a, b in zip(starts, starts[1:]))
    assert all(b["request_started_offset_ms"] - a["request_started_offset_ms"] >= 500 for a, b in zip(rows, rows[1:]))
    assert rows[2]["queue_elapsed_ms"] >= 1000
    assert all(r["worker_reaped"] for r in rows)
    assert result["residual_worker_count"] == 0
    assert result["retry_count"] == result["fallback_count"] == 0
    assert not (tmp_path / "executor.lock").exists()
    _safe(result)


def test_real_pagination_dependency_and_publication_wait(tmp_path):
    release = datetime.now(CN_TZ) + timedelta(seconds=1)
    jobs = [_job("page-2", deps=("page-1",)), _job("page-1"), _job("fund", release=release)]
    result = _run(tmp_path, jobs)
    rows = {r["request_id"]: r for r in result["results"]}
    assert result["execution_ok"]
    assert rows["page-2"]["request_started_at"] >= rows["page-1"]["request_completed_at"]
    assert datetime.fromisoformat(rows["fund"]["request_started_at"]) >= release


def test_real_blocking_worker_killed_no_late_file_or_residual(tmp_path):
    marker = tmp_path / "must-not-write"
    result = _run(tmp_path, [_job("blocked"), _job("dependent", deps=("blocked",))],
        {"blocked": {"sleep_seconds": 3, "late_marker": str(marker)}, "dependent": {}})
    assert result["status"] == "REQUEST_DEADLINE_EXCEEDED"
    assert result["dispatched_count"] == 1
    assert result["results"][0]["worker_terminated"]
    assert result["results"][0]["worker_reaped"]
    time.sleep(1.1)
    assert not marker.exists()
    _safe(result)


@pytest.mark.parametrize("code", ["MARKET_SOURCE_HTTP_429", "MARKET_SOURCE_HTTP_403", "MARKET_SOURCE_REQUEST_FAILED"])
def test_access_limits_and_errors_stop_dispatch_no_retry(tmp_path, code):
    result = _run(tmp_path, [_job("first"), _job("never", deps=("first",))],
                  {"first": {"error_code": code}, "never": {}})
    assert result["status"] == code
    assert result["dispatched_count"] == 1
    assert result["unstarted_request_ids"] == ["never"]
    _safe(result)


@pytest.mark.parametrize("scenario,reason", [
    ({"status_code": 429}, "AUDIT_HTTP_429"),
    ({"url": "https://example.com/"}, "AUDIT_RESPONSE_URL_INVALID"),
    ({"url": "http://push2.eastmoney.com/"}, "AUDIT_RESPONSE_URL_INVALID"),
])
def test_raw_http_result_still_fail_closed(tmp_path, scenario, reason):
    result = _run(tmp_path, [_job("first")], {"first": scenario})
    assert result["status"] == reason
    _safe(result)


def test_budget_insufficient_zero_spawn(tmp_path):
    result = _run(tmp_path, [_job("first")], seconds=.1)
    assert result["status"] == "AUDIT_BATCH_BUDGET_INSUFFICIENT"
    assert result["dispatched_count"] == 0


def test_pending_release_consumes_queue_budget_without_spawn(tmp_path):
    release = datetime.now(CN_TZ) + timedelta(seconds=10)
    result = _run(tmp_path, [_job("pending", release=release)], seconds=3.6)
    assert result["status"] == "AUDIT_BATCH_DEADLINE_EXCEEDED"
    assert result["dispatched_count"] == 0
    _safe(result)


def test_exclusive_lock_and_two_runs_do_not_pollute(tmp_path):
    lock = tmp_path / "executor.lock"
    lock.write_text("other audit", encoding="utf-8")
    assert _run(tmp_path, [_job("first")])["status"] == "AUDIT_EXECUTOR_ALREADY_RUNNING"
    assert lock.read_text() == "other audit"
    lock.unlink()
    assert _run(tmp_path, [_job("first")])["execution_ok"]
    assert _run(tmp_path, [_job("second")])["results"][0]["request_id"] == "second"


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_start_window_gate_zero_workers(monkeypatch, delta):
    import overnight_quant.data.t25_bounded_executor as module
    config = AuditTimeConfiguration("2026-10-09")
    current = config.at("14:40:00") + timedelta(milliseconds=delta)
    class Clock(datetime):
        @staticmethod
        def now(tz):
            return current
    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("network worker forbidden"))
    result = BoundedAuditExecutor().run([_job("first")], configuration=config, network=True)
    assert result["status"] == "AUDIT_WINDOW_MISSED"
    _safe(result)


def test_configuration_is_audit_only_formal_fixture_unchanged():
    from overnight_quant.data.t24_offline_rehearsal import FIXTURE_TIMES
    material = AuditTimeConfiguration("2026-10-09").material()
    assert material["feature_cutoff"].endswith("14:40:00+08:00")
    assert material["collection_deadline"].endswith("14:41:05+08:00")
    assert material["decision_time"].endswith("14:41:10+08:00")
    assert material["execution_start"].endswith("14:42:00+08:00")
    assert "14:50:00" in FIXTURE_TIMES["feature_cutoff"]


def test_budget_accounts_dependencies_hosts_and_release_not_request_count_divided_by_two():
    config = AuditTimeConfiguration("2026-10-09")
    jobs = integration_plan(config, industry_pages=2)
    result = schedule_budget(jobs, started_at=config.at("14:39:30"), deadline=config.at("14:41:05"))
    assert result["request_count"] == 23
    assert result["execution_ok"]
    rows = {r["request_id"]: r for r in result["trace"]}
    for job in jobs:
        row = rows[job.request_id]
        assert all(row["started_at"] >= rows[d]["completed_at"] for d in job.dependencies)
        if job.release_at:
            assert datetime.fromisoformat(row["started_at"]) >= job.release_at
    fund_end = max(r["completed_at"] for r in result["trace"] if r["request_id"].startswith("40-"))
    assert fund_end != (config.at("14:39:30") + timedelta(seconds=23)).isoformat()
    delayed = schedule_budget(integration_plan(config, publication_delay_ms=65000),
        started_at=config.at("14:39:30"), deadline=config.at("14:41:05"))
    assert delayed["status"] == "AUDIT_BATCH_BUDGET_INSUFFICIENT"


@pytest.mark.parametrize("jobs", [
    [_job("one"), _job("one")], [_job("one", deps=("missing",))],
    [_job("one", deps=("two",)), _job("two", deps=("one",))],
    [replace(_job("one"), url="https://example.com/")],
])
def test_malformed_plan_rejected_before_process(jobs):
    with pytest.raises(ValueError):
        validate_plan(jobs)


def test_local_process_provider_adapter_snapshot_features_chain(tmp_path, monkeypatch):
    import socket
    import requests
    def no_network(*a, **k):
        pytest.fail("external network forbidden")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    result = run_local_chain(lock_path=tmp_path / "executor.lock")
    assert result["status"] == "T25_OFFLINE_CHAIN_EXECUTABLE"
    assert result["executor"]["dispatched_count"] == 20
    assert result["chain"]["synthetic_readiness_passed"]
    assert len(result["chain"]["features"]) == 5
    assert result["network_requests_made"] == 0
    assert result["provider_contract_complete"] is False
    _safe(result)


def test_actual_cli_arbitrary_cwd_zero_network_and_no_overwrite(tmp_path):
    output = tmp_path / "synthetic.json"
    command = [sys.executable, str(ROOT / "overnight_quant/scripts/run_t25_offline_rehearsal.py"),
               "--synthetic-test-only", "--output", str(output)]
    first = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=60, encoding="utf-8")
    assert first.returncode == 0, first.stderr
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["status"] == "T25_OFFLINE_CHAIN_EXECUTABLE"
    raw = output.read_bytes()
    second = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=10, encoding="utf-8")
    assert second.returncode != 0
    assert output.read_bytes() == raw
    _safe(value)


def test_batch_failure_terminates_other_inflight_process(tmp_path):
    jobs = [_job("fail", "emweb.securities.eastmoney.com"), _job("inflight")]
    marker = tmp_path / "no-late-write"
    result = _run(tmp_path, jobs, {"fail": {"sleep_seconds": .3, "error_code": "MARKET_SOURCE_HTTP_429"},
                                 "inflight": {"sleep_seconds": 2.5, "late_marker": str(marker)}})
    assert result["dispatched_count"] == 2
    assert result["status"] == "MARKET_SOURCE_HTTP_429"
    assert all(row["worker_reaped"] for row in result["results"])
    assert next(row for row in result["results"] if row["request_id"] == "inflight")["worker_terminated"]
    time.sleep(.4)
    assert not marker.exists()
    _safe(result)


def test_default_run_does_not_construct_network_transport(monkeypatch):
    import overnight_quant.data.t25_bounded_executor as module
    monkeypatch.setattr(module, "MarketDotNetHttpClientTransport", lambda: pytest.fail("no implicit transport"))
    result = BoundedAuditExecutor().run([_job("first")], configuration=AuditTimeConfiguration("2026-10-09"))
    assert result["status"] == "EXPLICIT_NETWORK_REQUIRED"
    _safe(result)


def test_plan_order_and_provider_selection_result_are_deterministic(tmp_path):
    jobs = [_job("second"), _job("first")]
    first = _run(tmp_path, jobs)
    second = _run(tmp_path, list(reversed(jobs)))
    assert [r["request_id"] for r in first["results"]] == [r["request_id"] for r in second["results"]]
    assert [r["request_hash"] for r in first["results"]] == [r["request_hash"] for r in second["results"]]


def test_research_plan_has_19_unique_requests_and_no_retry_budget():
    from overnight_quant.data.t25_native_source_research import native_research_plan
    plan = native_research_plan()
    assert len(plan["requests"]) == plan["planned_source_http_request_count"] == 19
    assert len({r["request_id"] for r in plan["requests"]}) == 19
    assert len([r for r in plan["requests"] if r.get("board_code")]) == 4
    assert len([r for r in plan["requests"] if r.get("code")]) == 15
    assert plan["trade_date"] is None
    assert plan["network_requests_made"] == 0
    assert plan["fund_requires_completed_calendar_and_current_session"]
    _safe(plan)


def test_fund_observation_records_first_seen_not_worst_delay():
    import base64
    import hashlib
    from overnight_quant.data.t25_native_source_research import summarize_fund_observations
    captures = []
    for slot, label in [("14:39:55", "14:39"), ("14:40:05", "14:40"), ("14:40:35", "14:40")]:
        raw = json.dumps({"data": {"code": "000333", "klines": [f"2026-10-09 {label},1,2,3,4,5,6"]}}).encode()
        captures.append({"code": "000333", "observation_clock": slot,
            "content_base64": base64.b64encode(raw).decode(), "raw_hash": hashlib.sha256(raw).hexdigest(),
            "request_started_at": f"2026-10-09T{slot}+08:00", "available_at": f"2026-10-09T{slot}+08:00"})
    report = summarize_fund_observations(list(reversed(captures)), trade_date="2026-10-09")
    assert report["first_observed_target_available_at"]["000333"] == "2026-10-09T14:40:05+08:00"
    assert report["worst_publication_delay_proven"] is False
    assert report["fund_semantics"] == "FUND_FLOW_SEMANTICS_UNPROVEN"
    assert report == summarize_fund_observations(captures, trade_date="2026-10-09")
    captures[0]["raw_hash"] = "0" * 64
    with pytest.raises(ValueError, match="RAW_HASH_INVALID"):
        summarize_fund_observations(captures, trade_date="2026-10-09")


def test_process_startup_is_inside_hard_request_deadline(tmp_path, monkeypatch):
    import overnight_quant.data.t25_bounded_executor as module
    original = module.subprocess.Popen
    def slow_start(*args, **kwargs):
        if "overnight_quant.data.t25_local_response_worker" in args[0]:
            time.sleep(.3)
        return original(*args, **kwargs)
    monkeypatch.setattr(module.subprocess, "Popen", slow_start)
    marker = tmp_path / "too-late"
    result = _run(tmp_path, [_job("blocked")],
        {"blocked": {"sleep_seconds": 1.9, "late_marker": str(marker)}})
    assert result["status"] == "REQUEST_DEADLINE_EXCEEDED"
    assert result["results"][0]["worker_terminated"]
    assert not marker.exists()
    _safe(result)


@pytest.mark.parametrize("clock", ["14:37:00", "14:39:30"])
def test_approved_start_window_inclusive_and_fixed_deadline(monkeypatch, clock):
    import overnight_quant.data.t25_bounded_executor as module
    config = AuditTimeConfiguration("2026-10-09")
    current = config.at(clock)
    class Clock(datetime):
        @staticmethod
        def now(tz):
            return current
    monkeypatch.setattr(module, "datetime", Clock)
    calls = []
    executor = BoundedAuditExecutor()
    monkeypatch.setattr(executor, "_run", lambda jobs, **kwargs: calls.append(kwargs) or {"status": "local_spy_only"})
    assert executor.run([_job("first")], configuration=config, network=True)["status"] == "local_spy_only"
    assert calls[0]["deadline"] == config.at("14:41:05")
    assert calls[0]["test_payloads"] is None
