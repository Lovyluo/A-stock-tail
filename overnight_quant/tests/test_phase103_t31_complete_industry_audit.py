"""Synthetic/test-only same-round audits; HttpClient uses an in-memory handler."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from overnight_quant.data import complete_industry_audit as audit
from overnight_quant.data import t25_bounded_executor as bounded
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.scripts.run_complete_industry_audit import main, _main_for_test
from overnight_quant.data.source_capability_adapters import SourceProviderEnvelope, execute_source_adapter
from overnight_quant.tests.test_phase102_t30_complete_industry_candidate import synthetic_batch

ROOT = Path(__file__).resolve().parents[2]
COMMAND = [shutil.which("pwsh.exe") or "powershell.exe", "-NoProfile", "-NonInteractive", "-File",
           str(ROOT / "overnight_quant/tests/fixtures/t29_http_client_worker.ps1")]
CAPS = {"BK0474": 2, "BK0475": 2, "BK1239": 1, "BK1277": 2}


@pytest.fixture(autouse=True)
def no_source_network(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Source network forbidden in T3.1 tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def local_context(tmp_path, monkeypatch, prepared_handler):
    real_now = datetime.now(CN_TZ)
    target = real_now.replace(hour=14, minute=37, second=0, microsecond=0)
    delta = target - real_now
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + delta
    monkeypatch.setattr(audit, "datetime", Clock)
    monkeypatch.setattr(bounded, "datetime", Clock)
    day = target.date().isoformat()
    base = synthetic_batch()
    plan = audit.frozen_plan(day, CAPS)
    payloads = {}
    native = int((target - timedelta(seconds=10)).timestamp())
    for job in plan["requests"]:
        key = job["request_id"]
        if key.startswith("10-mapping-"):
            code = key.removeprefix("10-mapping-")
            board, name = audit.EXPECTED_INDUSTRIES[code]
            body = {"ssbk": [{"SECURITY_CODE": code, "BOARD_CODE": board[2:], "BOARD_NAME": name, "BOARD_RANK": 2}]}
        elif key.startswith("20-page-"):
            _, _, board, page = key.split("-")
            raw = base["breadth"]["request_tree"]["boards"][board][0]["content_base64"]
            import base64
            body = json.loads(base64.b64decode(raw))
            for row in body["data"]["diff"]:
                row["f124"] = native
        else:
            board = key.removeprefix("30-strength-")
            body = {"rc": 0, "data": {"f57": board, "f58": audit.strength.BOARD_NAMES[board],
                                     "f170": -55, "f152": 2, "f86": native}}
        payloads[key] = {"body": body, "call_marker": str(tmp_path / (key + ".call")),
                         "diagnostic_path": str(tmp_path / (key + ".diagnostic.json")),
                         "capture_offset_seconds": delta.total_seconds()}
    return day, audit._TestContext([*COMMAND, "-HandlerAssembly", str(prepared_handler)], payloads,
                                  tmp_path / "executor.lock", lambda: Clock.now(CN_TZ))


@pytest.fixture(scope="session")
def prepared_handler(tmp_path_factory):
    directory = tmp_path_factory.mktemp("t29-handler-preparation")
    assembly = directory / "T29Handler.dll"
    # Only test C# compilation is preparation. Every request still launches a fresh
    # real PowerShell process; assembly loading, gate waiting and body I/O stay timed.
    completed = subprocess.run([*COMMAND, "-CompileHandlerOnly", "-HandlerAssembly", str(assembly)],
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert completed.returncode == 0, completed.stderr
    prepared = json.loads(completed.stdout.lstrip("\ufeff"))
    assert prepared["test_only"] is True and prepared["stage"] == "fixture_preparation_only"
    assert prepared["source_sha256"] == hashlib.sha256((ROOT / "overnight_quant/tests/fixtures/t29_http_handler.cs").read_bytes()).hexdigest()
    assert assembly.is_file()
    (directory / "preparation.json").write_text(json.dumps(prepared, sort_keys=True), encoding="utf-8")
    return assembly


def safety(result):
    assert result["data_ready"] is result["hard_gate_authorized"] is False
    assert result["records"] == result["candidates"] == result["tickets"] == result["orders"] == []
    assert result["qualification"] == "0/3" and result["provider_contract_complete"] is False
    assert result["sampling_authorized"] is False


def cli_run(tmp_path, capsys, day, context, *, limits=CAPS):
    output = tmp_path / "audit.json"
    plan = audit.frozen_plan(day, limits)
    rc = _main_for_test(["--network", "--date", day, "--page-limits", json.dumps(limits),
        "--expected-plan-hash", plan["plan_hash"], "--output", str(output)], context)
    result = json.loads(output.read_bytes())
    capsys.readouterr()
    safety(result)
    return rc, result, output


def failure_diagnostics(result, directory):
    details = []
    for row in result.get("request_trace", []):
        if row.get("ok"):
            continue
        path = directory / (row["request_id"] + ".diagnostic.json")
        try:
            stored = json.loads(path.read_text(encoding="utf-8-sig"))
            checkpoint = {"fixture_entry_seconds": stored.get("fixture_entry_seconds"),
                "parent_deadline_monotonic_seconds": stored.get("parent_deadline_monotonic_seconds"), "phases": [
                {"stage": phase.get("stage"), "monotonic_seconds": phase.get("monotonic_seconds")}
                for phase in stored.get("phases", [])
            ]}
        except (OSError, ValueError):
            checkpoint = {"stage": "UNKNOWN_CHECKPOINT_MISSING_OR_PARTIAL"}
        details.append({**{key: row.get(key) for key in (
            "request_id", "stage", "request_elapsed_ms", "worker_exit_code", "worker_terminated", "worker_reaped", "error_code",
        )}, "fixture": checkpoint,
            "client_call_seen": (directory / (row["request_id"] + ".call")).exists(),
            "raw_error": (row.get("failure_response") or {}).get("raw_error_message")})
    return json.dumps({"errors": result.get("errors"), "failed_requests": details}, ensure_ascii=True)


@pytest.mark.parametrize("page_case,count", [("single", 13), ("full", 16), ("reserved_unused", 13)])
def test_real_cli_executor_capture_composer_paths_and_double_replay(tmp_path, capsys, local_context, page_case, count):
    day, context = local_context
    limits = dict.fromkeys(audit.candidate.BOARDS, 1) if page_case == "single" else CAPS
    if page_case == "full":
        for board in ("BK0474", "BK0475", "BK1277"):
            rows = context.payloads[f"20-page-{board}-1"]["body"]["data"]["diff"]
            rows.extend({"f12": f"{700000+i:06d}", "f14": "synthetic", "f3": 1, "f124": rows[0]["f124"]}
                        for i in range(101 - len(rows)))
            context.payloads[f"20-page-{board}-1"]["body"]["data"] = {"total": 101, "diff": rows[:100]}
            context.payloads[f"20-page-{board}-2"]["body"]["data"] = {"total": 101, "diff": rows[100:]}
    rc, result, source = cli_run(tmp_path, capsys, day, context, limits=limits)
    assert rc == 0, failure_diagnostics(result, tmp_path)
    assert result["candidate_batch_complete"] and result["dispatched_count"] == count
    assert len(result["audit_records"]) == 5 and result["candidate_result"]["strength_request_count"] == 4
    assert result["network_requests_made"] == 0  # in-memory handler, not a real source
    assert len(result["actual_client_call_order"]) == count
    assert len(list(tmp_path.glob("*.call"))) == count
    assert all(row["request_elapsed_ms"] <= 2000 for row in result["request_trace"])
    for checkpoint in tmp_path.glob("*.diagnostic.json"):
        phases = json.loads(checkpoint.read_text(encoding="utf-8-sig"))["phases"]
        assert "fixture_compile" not in [p["stage"] for p in phases]
        assert "fixture_assembly_load" in [p["stage"] for p in phases]
    assert not context.lock_path.exists() and not context.lock_path.with_suffix(".stage").exists()
    if count == 13:
        assert result["stages"][2]["status"] == "UNUSED_RESERVED_PAGES_SKIPPED"
    for stage in result["stages"]:
        assert stage.get("max_in_flight", 0) <= 2
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    outputs = []
    for number in (1, 2):
        out = tmp_path / f"replay-{number}.json"
        assert main(["--replay", str(source), "--expected-file-sha256", digest, "--output", str(out)]) == 0
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1] and source.read_bytes() == raw
    assert json.loads(outputs[0])["candidate_batch_complete"]
    for change in ("batch", "client_order", "stage", "candidate", "page_cap", "test_scope", "qualification"):
        bad = deepcopy(result)
        if change == "batch": bad["captured_batch"]["breadth"]["batch_id"] = "0" * 64
        elif change == "client_order": bad["actual_client_call_order"].pop()
        elif change == "stage": bad["request_trace"][0]["stage"] = "strength"
        elif change == "candidate": bad["candidate_result"]["audit_records"][0]["payload"]["change_pct"] = 99
        elif change == "page_cap": bad["plan"]["page_limits"]["BK0474"] = 4
        elif change == "test_scope": bad["test_only"] = False
        else: bad["qualification_eligible"] = True
        bad["evidence_hash"] = stable_hash({k: v for k, v in bad.items() if k != "evidence_hash"})
        digest = hashlib.sha256(json.dumps(bad, sort_keys=True).encode()).hexdigest()
        rejected = audit.replay_evidence(bad, expected_file_sha256=digest, actual_file_sha256=digest)
        assert not rejected["evidence_integrity_verified"], change
        safety(rejected)
    capsys.readouterr()


@pytest.mark.parametrize("scenario,expected_call", [({"cold_ms": 2500}, False), ({"body_ms": 3000}, True)])
def test_prepared_fixture_keeps_real_startup_and_body_in_2000ms_budget(tmp_path, prepared_handler, scenario, expected_call):
    jobs = [bounded.AuditRequest("blocked", "https://push2.eastmoney.com/test-only", {}),
            bounded.AuditRequest("dependent", "https://push2.eastmoney.com/test-only", {}, ("blocked",))]
    result = bounded.BoundedAuditExecutor()._run_for_test(
        jobs, deadline=datetime.now(CN_TZ) + timedelta(seconds=10),
        command=[*COMMAND, "-HandlerAssembly", str(prepared_handler)],
        payloads={"blocked": {"call_marker": str(tmp_path / "blocked.call"),
                              "diagnostic_path": str(tmp_path / "blocked.diagnostic.json"), **scenario}, "dependent": {}},
        lock_path=tmp_path / "executor.lock")
    assert result["status"] == "REQUEST_DEADLINE_EXCEEDED", failure_diagnostics(
        {"errors": [result["status"]], "request_trace": result["results"]}, tmp_path)
    assert result["dispatched_count"] == 1
    assert result["results"][0]["worker_reaped"]
    if not expected_call:
        assert result["results"][0]["worker_terminated"]
    else:
        assert result["results"][0]["worker_terminated"] or result["results"][0]["worker_exit_code"] == 2
    assert result["results"][0]["request_deadline_ms"] == 2000
    assert (tmp_path / "blocked.call").exists() is expected_call
    assert not (tmp_path / "executor.lock").exists()
    assert result["residual_worker_count"] == result["retry_count"] == result["fallback_count"] == 0
    assert bounded.MAX_WORKERS == 2 and bounded.HOST_INTERVAL_MS == 500 and bounded.REQUEST_MS == 2000
    assert result["data_ready"] is result["hard_gate_authorized"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []
    assert result.get("records", []) == [] and result["qualification"] == "0/3"


def test_failure_diagnostic_is_compact_and_does_not_dump_response(tmp_path):
    (tmp_path / "request.diagnostic.json").write_text(json.dumps({
        "fixture_entry_seconds": 1.0, "phases": [{"stage": "fixture_assembly_load", "monotonic_seconds": 1.1}],
        "body": "must-not-print", "content_base64": "must-not-print",
    }), encoding="utf-8")
    message = failure_diagnostics({"errors": ["REQUEST_DEADLINE_EXCEEDED"], "request_trace": [{
        "request_id": "request", "stage": "mapping", "request_elapsed_ms": 2001,
        "worker_terminated": True, "worker_reaped": True, "error_code": "REQUEST_DEADLINE_EXCEEDED",
        "failure_response": {"raw_error_message": "original-error", "body": "must-not-print"},
    }]}, tmp_path)
    assert "must-not-print" not in message and "original-error" in message
    value = json.loads(message)["failed_requests"][0]
    assert value["request_id"] == "request" and value["stage"] == "mapping"
    assert value["worker_terminated"] and value["fixture"]["phases"][0]["stage"] == "fixture_assembly_load"


@pytest.mark.parametrize("bad", ["page_limit", "duplicate", "missing_member", "total_drift", "worker_failed"])
def test_semantic_or_child_failure_stops_whole_round_without_strength(tmp_path, capsys, local_context, bad):
    day, context = local_context
    body = context.payloads["20-page-BK0474-1"]["body"]["data"]
    if bad == "page_limit":
        body["total"] = 301
    elif bad == "duplicate":
        body["diff"][1] = deepcopy(body["diff"][0])
    elif bad == "missing_member":
        body["diff"][0]["f12"] = "000002"
    elif bad == "worker_failed":
        context.payloads["10-mapping-000001"]["block_ms"] = 3000
    else:
        rows = body["diff"]
        rows.extend({"f12": f"{700000+i:06d}", "f14": "synthetic", "f3": 1, "f124": rows[0]["f124"]}
                    for i in range(101 - len(rows)))
        context.payloads["20-page-BK0474-1"]["body"]["data"] = {"total": 101, "diff": rows[:100]}
        context.payloads["20-page-BK0474-2"]["body"]["data"] = {"total": 102, "diff": rows[100:]}
    rc, result, source = cli_run(tmp_path, capsys, day, context)
    assert rc == 2 and result["audit_records"] == [] and not result["candidate_batch_complete"]
    assert not any(p.name.startswith("30-strength-") for p in tmp_path.glob("*.call"))
    assert result["retry_count"] == result["fallback_count"] == result["residual_worker_count"] == 0
    raw = source.read_bytes()
    replay = audit.replay_evidence(result, expected_file_sha256=hashlib.sha256(raw).hexdigest(), actual_file_sha256=hashlib.sha256(raw).hexdigest())
    assert replay["evidence_integrity_verified"] and not replay["candidate_batch_complete"]
    assert replay["failure_reason_verified"] is False


@pytest.mark.parametrize("offset,accepted", [(0, True), (1, False), (-1, True)])
def test_latest_143900_millisecond_boundary_before_any_worker(tmp_path, monkeypatch, local_context, offset, accepted):
    day, context = local_context
    current = datetime.fromisoformat(f"{day}T14:39:00+08:00") + timedelta(milliseconds=offset)
    context = replace(context, gate_clock=lambda: current)
    calls = []
    def stop_at_executor(self, *a, **k):
        calls.append(1)
        return {"execution_ok": False, "status": "SYNTHETIC_STOP", "results": []}
    monkeypatch.setattr(audit._IndustryExecutor, "_run", stop_at_executor)
    result = audit._run(day, expected_plan_hash=audit.frozen_plan(day)["plan_hash"], test=context)
    assert bool(calls) is accepted
    if not accepted:
        assert result["errors"] == ["AUDIT_WINDOW_MISSED"] and result["dispatched_count"] == 0
    safety(result)


def test_initial_and_each_dispatch_budget_insufficient_zero_spawn(tmp_path, monkeypatch, local_context):
    day, context = local_context
    plan = audit.frozen_plan(day)
    result = audit._run(day, expected_plan_hash=plan["plan_hash"], test=replace(context, budget_seconds=.001))
    assert result["errors"] == ["AUDIT_BATCH_BUDGET_INSUFFICIENT"] and result["dispatched_count"] == 0
    calls = []
    def budget(self, *a):
        calls.append(1)
        return 3500 if len(calls) == 1 else 1000000
    monkeypatch.setattr(audit._IndustryExecutor, "_required_dispatch_budget_ms", budget)
    result = audit._run(day, expected_plan_hash=plan["plan_hash"], test=context)
    assert result["errors"] == ["AUDIT_BATCH_BUDGET_INSUFFICIENT"] and result["dispatched_count"] == 0
    assert not list(tmp_path.glob("*.call"))


@pytest.mark.parametrize("mode", [[], ["--validate-only"]])
def test_default_and_validate_only_do_not_construct_transport_or_worker(monkeypatch, capsys, mode):
    def forbidden(*a, **k):
        pytest.fail("transport/worker forbidden")
    monkeypatch.setattr(audit, "MarketDotNetHttpClientTransport", forbidden)
    monkeypatch.setattr(audit._IndustryExecutor, "_run", forbidden)
    assert main(["--date", "2026-10-16", *mode]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["network_requests_made"] == result["dispatched_count"] == 0
    assert result["plan"]["time_configuration"]["latest_start_at"].endswith("14:39:00+08:00")
    assert result["plan"]["time_configuration"]["version"] == audit.TIME_VERSION
    assert result["plan"]["conservative_budget_ms"] == 35000
    safety(result)


def test_plan_caps_and_production_admission_remain_fixed():
    plan = audit.frozen_plan("2026-10-16", CAPS)
    assert plan["maximum_requests"] == 16 and plan["conservative_budget_ms"] == 42500
    with pytest.raises(ValueError):
        audit.frozen_plan("2026-10-16", dict.fromkeys(audit.candidate.BOARDS, 2))
    calls = []
    result = execute_source_adapter("industry_snapshot", origin_source="eastmoney", adapter="direct_http",
        source_version=audit.candidate.SOURCE_VERSION, provider_envelope=SourceProviderEnvelope(
            audit.candidate.PROVIDER_KEY, lambda: calls.append(1)), environ={})
    assert not result["provider_called"] and not result["hard_gate_authorized"] and calls == []


def test_frozen_caps_file_validation_and_hash_mismatch_zero_workers(tmp_path, monkeypatch, capsys):
    caps = tmp_path / "approved-caps.json"
    caps.write_text(json.dumps(CAPS), encoding="utf-8")
    monkeypatch.setattr(audit._IndustryExecutor, "_run", lambda *a, **k: pytest.fail("worker forbidden"))
    assert main(["--date", "2026-10-16", "--page-limits-file", str(caps)]) == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    assert plan["maximum_requests"] == 16 and plan["page_limits"] == CAPS
    output = tmp_path / "never.json"
    assert main(["--network", "--date", "2026-10-16", "--expected-plan-hash", "0" * 64,
                 "--page-limits-file", str(caps), "--output", str(output)]) == 2
    failure = json.loads(capsys.readouterr().out)
    safety(failure)
    assert failure["network_requests_made"] == 0


def test_malformed_caps_file_is_fail_closed_without_transport(tmp_path, monkeypatch, capsys):
    path = tmp_path / "invalid-caps.json"
    path.write_bytes(b"not JSON")
    monkeypatch.setattr(audit, "MarketDotNetHttpClientTransport", lambda *a: pytest.fail("transport forbidden"))
    assert main(["--date", "2026-10-16", "--page-limits-file", str(path)]) == 2
    safety(json.loads(capsys.readouterr().out))


def test_existing_coordination_lock_is_preserved_not_reset(tmp_path, local_context):
    day, context = local_context
    context.lock_path.write_bytes(b"existing-pending-audit")
    before = context.lock_path.read_bytes()
    result = audit._run(day, expected_plan_hash=audit.frozen_plan(day)["plan_hash"], test=context)
    assert result["errors"] == ["AUDIT_EXECUTOR_ALREADY_RUNNING"]
    assert context.lock_path.read_bytes() == before and not list(tmp_path.glob("*.call"))
    safety(result)


def test_public_fixed_sha_and_confirmed_inputs_before_any_dispatch(tmp_path, monkeypatch, local_context):
    import subprocess
    day, context = local_context
    sha = "a" * 40
    monkeypatch.setattr(subprocess, "check_output", lambda args, **k: sha if args[1] == "rev-parse" else "")
    confirmed = []
    monkeypatch.setattr(audit, "confirm_inputs", lambda *args: confirmed.append(1) or {"synthetic_unit_spy": True})
    monkeypatch.setattr(audit, "LOCK", context.lock_path)
    constructed = []
    class Transport:
        def __init__(self):
            constructed.append(1)
            self.worker_command = COMMAND
    monkeypatch.setattr(audit, "MarketDotNetHttpClientTransport", Transport)
    dispatched = []
    def stop(self, *args, **kwargs):
        dispatched.append(1)
        return {"execution_ok": False, "status": "SYNTHETIC_STOP_NO_PROCESS", "results": []}
    monkeypatch.setattr(audit._IndustryExecutor, "_run", stop)
    plan = audit.frozen_plan(day)
    for expected in (None, "b" * 40, "a" * 64):
        result = audit.run_audit(day, network=True, expected_head=expected, expected_plan_hash=plan["plan_hash"])
        assert result["errors"] == ["AUDIT_FIXED_HEAD_OR_WORKTREE_INVALID"]
        safety(result)
    assert not confirmed and not constructed and not dispatched
    result = audit.run_audit(day, network=True, expected_head=sha, expected_plan_hash=plan["plan_hash"])
    assert confirmed == constructed == dispatched == [1]
    assert result["producer_commit_sha"] == sha and result["dispatched_count"] == 0
    safety(result)


def test_partial_failure_receipt_never_claims_zero_network_or_breaks_failure_capture():
    evidence = audit.safe(test_only=False, request_trace=[{"request_id": "failed",
        "captured_worker_response": {"response": {"client_call_started_monotonic_seconds": 1.25}}}])
    result = audit._finish(evidence)
    assert result["network_requests_made"] is None and result["dispatched_count"] == 1
    assert result["actual_client_call_order"] == [] and result["evidence_hash"]
    safety(result)


def test_existing_evidence_and_plan_sidecar_are_never_overwritten(tmp_path, capsys, local_context):
    day, context = local_context
    output = tmp_path / "audit.json"
    output.write_bytes(b"old evidence")
    assert _main_for_test(["--network", "--date", day, "--output", str(output)], context) == 2
    assert output.read_bytes() == b"old evidence" and not list(tmp_path.glob("*.call"))
    capsys.readouterr()
    output.unlink()
    sidecar = output.with_suffix(".plan.json")
    sidecar.write_bytes(b"existing plan")
    plan = audit.frozen_plan(day)
    assert _main_for_test(["--network", "--date", day, "--expected-plan-hash", plan["plan_hash"], "--output", str(output)], context) == 2
    assert sidecar.read_bytes() == b"existing plan" and not output.exists()
    capsys.readouterr()
