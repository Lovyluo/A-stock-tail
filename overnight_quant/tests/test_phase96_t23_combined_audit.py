from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import socket
import time

import pytest
import requests

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_STOCK_URL, EASTMONEY_ULIST_URL, PRODUCTION_TRANSPORT_VERSION,
    MarketHttpResponse, MarketSourceContractError,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data import t23_combined_audit as audit
from overnight_quant.scripts import run_t23_combined_audit as cli
from overnight_quant.scripts import run_t23_combined_audit_verify as verify_cli
from overnight_quant.scripts import run_market_source_validation as validation
from overnight_quant.tests.test_phase95_derived_industry_breadth import FixtureTransport
from overnight_quant.tests import test_phase92_calendar_session_contract as session_fixtures


DAY = "2026-09-30"


class Clock:
    def __init__(self, value=None):
        self.value = value or datetime(2026, 9, 30, 14, 37, tzinfo=CN_TZ)

    def __call__(self):
        return self.value


class Transport:
    transport_version = PRODUCTION_TRANSPORT_VERSION

    def __init__(self, name, clock, *, fail=False, late=False, future=False):
        self.name, self.clock = name, clock
        self.fail, self.late, self.future = fail, late, future
        self.calls = []
        self.industry = FixtureTransport()
        self.industry.board_rows["BK1239"].extend([
            self.industry._row("000016", None), self.industry._row("200016", None)])
        for rows in self.industry.board_rows.values():
            for row in rows:
                row["f124"] = int(datetime(2026, 9, 30, 14, 37, tzinfo=CN_TZ).timestamp())

    def request(self, method, url, *, params, headers, timeout_seconds):
        self.calls.append((url, dict(params), timeout_seconds))
        if self.fail:
            raise MarketSourceContractError("MARKET_SOURCE_REQUEST_FAILED")
        started = self.clock.value
        native = datetime(2026, 9, 30, 14, 37, tzinfo=CN_TZ)
        if self.future:
            native = native.replace(minute=40, second=1)
        timestamp = int(native.timestamp())
        if self.name == "derived_industry":
            raw = self.industry.request(method, url, params=params, headers=headers,
                                        timeout_seconds=timeout_seconds).content
        elif url == EASTMONEY_ULIST_URL:
            raw = json.dumps({"data": {"diff": [
                {"f12": code, "f14": code, "f104": 2, "f105": 1, "f106": 1, "f124": timestamp}
                for code in ("000001", "399001", "899050")]}}).encode()
        elif url == EASTMONEY_STOCK_URL:
            raw = json.dumps({"data": {"f57": "000001", "f58": "SSE", "f86": timestamp, "f170": 0.42}}).encode()
        else:
            raise AssertionError(url)
        self.clock.value += timedelta(milliseconds=20)
        if self.late:
            self.clock.value = datetime(2026, 9, 30, 14, 40, 10, 1000, tzinfo=CN_TZ)
        return MarketHttpResponse(raw, 200, url, {}, started.isoformat(),
            self.clock.value.isoformat(), PRODUCTION_TRANSPORT_VERSION,
            started.isoformat(), self.clock.value.isoformat(), 20.0, False)


def factory(clock, **options):
    instances = {}

    def create(name):
        instance = Transport(name, clock, **options.get(name, {}))
        instances[name] = instance
        return instance
    return create, instances


def assert_safe(result):
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["sampling_authorized"] is False
    assert result["qualification"] == "0/3"
    assert all(result[key] == [] for key in ("candidates", "tickets", "orders"))


@pytest.fixture(autouse=True)
def deny_real_network(monkeypatch, tmp_path):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(audit, "CACHE", tmp_path)
    def deny(*args, **kwargs):
        raise AssertionError("real_network_forbidden")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(requests.sessions.Session, "request", deny)


@pytest.mark.parametrize("deadline,valid", [("14:39:59", False), ("14:40:00", False), ("14:40:10", True)])
def test_cli_deadline_order_calls_actual_provider_precheck_without_transport(tmp_path, capsys, deadline, valid):
    clock = Clock()
    create, instances = factory(clock)
    exit_code = cli.main(["--date", DAY, "--validate-only", "--collection-deadline",
        f"{DAY}T{deadline}+08:00"], _clock=clock, _test_transport_factory=create)
    result = json.loads(capsys.readouterr().out)
    assert exit_code == (0 if valid else 2)
    assert result["network_requests_made"] == 0
    assert instances == {}
    assert_safe(result)


def test_precheck_exercises_both_provider_constructors(monkeypatch):
    original = audit.EastmoneyDerivedIndustryBreadthProvider
    calls = []

    def checked(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(audit, "EastmoneyDerivedIndustryBreadthProvider", checked)
    result = audit.precheck(audit.AuditParameters.for_date(DAY))
    assert len(calls) == 1
    assert calls[0]["decision_time"] > calls[0]["cutoff"]
    assert result["provider_constructor_checks"] == ["market_breadth", "derived_industry"]
    assert result["network_requests_made"] == 0


@pytest.mark.parametrize("overrides", [
    {"feature_cutoff": f"{DAY}T14:40:00"},
    {"collection_deadline": "2026-10-01T14:40:10+08:00"},
    {"start_at": f"{DAY}T14:37:00+00:00"},
    {"latest_start_at": f"{DAY}T14:40:00+08:00"},
    {"collection_deadline": f"{DAY}T14:40:01+08:00", "latest_start_at": f"{DAY}T14:39:59+08:00"},
])
def test_invalid_timezone_date_window_or_budget_zero_calls(overrides):
    clock = Clock()
    create, instances = factory(clock)
    try:
        parameters = audit.AuditParameters.for_date(DAY, **overrides)
        result = audit.run_combined_audit(parameters, network=True, output=None,
                                         clock=clock, _test_transport_factory=create)
        assert result["execution_ok"] is False
        assert_safe(result)
    except ValueError:
        pass
    assert instances == {}


@pytest.mark.parametrize("value,status", [
    (datetime(2026, 9, 30, 14, 36, 59, 999000, tzinfo=CN_TZ), "AUDIT_WINDOW_NOT_OPEN"),
    (datetime(2026, 9, 30, 14, 39, 30, 1000, tzinfo=CN_TZ), "AUDIT_WINDOW_MISSED"),
    (datetime(2026, 9, 30, 14, 40, tzinfo=CN_TZ), "AUDIT_WINDOW_MISSED"),
])
def test_cli_missed_or_early_window_never_constructs_network_transport(tmp_path, capsys, value, status):
    clock = Clock(value)
    create, instances = factory(clock)
    assert cli.main(["--date", DAY, "--network", "--output", str(tmp_path / "result.json")],
                    _clock=clock, _test_transport_factory=create) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status
    assert instances == {}
    assert result["network_requests_made"] == 0
    assert not (tmp_path / "result.json").exists()
    assert_safe(result)


def test_cli_complete_rehearsal_uses_two_plus_nine_and_preserves_invalid_count(tmp_path, capsys):
    clock = Clock()
    create, instances = factory(clock)
    path = tmp_path / "batch.json"
    assert cli.main(["--date", DAY, "--network", "--output", str(path)],
                    _clock=clock, _test_transport_factory=create) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "COMBINED_AUDIT_VALIDATED"
    assert result["selection_scope"] == "test_only"
    assert result["network_requests_made"] is None
    assert result["request_plan"]["planned_request_count"] == 11
    assert result["request_plan"]["derived_industry_minimum_requests"] == 9
    assert len(instances["market_breadth"].calls) == 2
    assert len(instances["derived_industry"].calls) == 9
    assert all(0 < row[2] <= 2 for instance in instances.values() for row in instance.calls)
    industry = result["module_results"]["derived_industry"]["records"]
    bk1239 = next(row["payload"] for row in industry if row["payload"]["code"] == "000333")
    assert bk1239["invalid_count"] == 2
    assert bk1239["flat_count"] == 1
    assert_safe(result)
    anchor = hashlib.sha256(path.read_bytes()).hexdigest()
    a = audit.verify_combined_audit_file(path, expected_file_sha256=anchor)
    b = audit.verify_combined_audit_file(path, expected_file_sha256=anchor)
    assert a["evidence_integrity_verified"] is True, a
    assert a["provider_contract_complete"] is True
    assert json.dumps(a, sort_keys=True).encode() == json.dumps(b, sort_keys=True).encode()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == anchor
    assert not path.with_name(path.name + ".running").exists()


def test_cli_offline_preflight_can_validate_another_date_without_clock_or_network(capsys):
    assert cli.main(["--date", DAY, "--validate-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["network_requests_made"] == 0
    assert result["parameters"]["trade_date"] == DAY
    assert_safe(result)


def test_full_verifier_cli_writes_identical_replays_and_preserves_input(tmp_path, capsys):
    clock = Clock()
    create, _ = factory(clock)
    path = tmp_path / "original.json"
    assert cli.main(["--date", DAY, "--network", "--output", str(path)],
                    _clock=clock, _test_transport_factory=create) == 0
    capsys.readouterr()
    anchor = hashlib.sha256(path.read_bytes()).hexdigest()
    outputs = [tmp_path / "replay_a.json", tmp_path / "replay_b.json"]
    for output in outputs:
        assert verify_cli.main([str(path), "--expected-file-sha256", anchor,
                                "--output", str(output)]) == 0
        capsys.readouterr()
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == anchor
    assert_safe(json.loads(outputs[0].read_bytes()))


@pytest.mark.parametrize("failed", ["market_breadth", "derived_industry"])
def test_single_module_failure_is_preserved_without_retry_or_partial_batch(tmp_path, failed):
    clock = Clock()
    create, instances = factory(clock, **{failed: {"fail": True}})
    path = tmp_path / "failed.json"
    result = audit.run_combined_audit(audit.AuditParameters.for_date(DAY), network=True,
        output=path, clock=clock, _test_transport_factory=create)
    assert result["status"] == "AUDIT_INCOMPLETE"
    assert result["provider_contract_complete"] is False
    assert result["module_results"][failed]["records"] == []
    assert len(instances[failed].calls) == 1
    assert_safe(result)
    anchor = hashlib.sha256(path.read_bytes()).hexdigest()
    replay = audit.verify_combined_audit_file(path, expected_file_sha256=anchor)
    assert replay["evidence_integrity_verified"] is True, replay
    assert replay["provider_contract_complete"] is False
    assert replay["module_checks"][failed]["failure_reason_verified"] is False


def test_batch_deadline_exhaustion_stops_remaining_module(tmp_path):
    clock = Clock()
    create, instances = factory(clock, market_breadth={"late": True})
    result = audit.run_combined_audit(audit.AuditParameters.for_date(DAY), network=True,
        output=tmp_path / "late.json", clock=clock, _test_transport_factory=create)
    assert result["status"] == "AUDIT_INCOMPLETE"
    assert instances.keys() == {"market_breadth"}
    assert len(instances["market_breadth"].calls) == 1
    assert result["module_results"]["derived_industry"]["error_code"] == "AUDIT_BATCH_DEADLINE_EXCEEDED"
    assert_safe(result)


def test_request_timeout_uses_remaining_batch_budget_and_never_retries():
    parameters = audit.AuditParameters.for_date(DAY)
    clock = Clock(parameters.collection_deadline - timedelta(milliseconds=250))
    transport = Transport("market_breadth", clock, fail=True)
    guarded = audit._BudgetTransport(transport, parameters, clock, 2, time.monotonic() + 5)
    with pytest.raises(MarketSourceContractError):
        guarded.request("GET", EASTMONEY_STOCK_URL, params={}, headers={}, timeout_seconds=2)
    assert len(transport.calls) == 1
    assert 0 < transport.calls[0][2] <= 0.25
    assert len(guarded.attempts) == 1


def test_request_elapsed_over_hard_limit_is_rejected():
    parameters = audit.AuditParameters.for_date(DAY)
    clock = Clock()
    original = Transport("market_breadth", clock)

    class SlowResponse:
        def request(self, *args, **kwargs):
            return replace(original.request(*args, **kwargs), process_elapsed_ms=2000.001)

    guarded = audit._BudgetTransport(SlowResponse(), parameters, clock, 2, time.monotonic() + 10)
    with pytest.raises(MarketSourceContractError, match="AUDIT_REQUEST_DEADLINE_EXCEEDED"):
        guarded.request("GET", EASTMONEY_STOCK_URL, params={}, headers={}, timeout_seconds=2)
    assert len(original.calls) == 1


def test_window_expiring_during_prechecks_has_zero_network_calls(tmp_path):
    values = iter([
        datetime(2026, 9, 30, 14, 39, 30, tzinfo=CN_TZ),
        datetime(2026, 9, 30, 14, 39, 30, 1000, tzinfo=CN_TZ),
    ])
    create, instances = factory(Clock())
    result = audit.run_combined_audit(audit.AuditParameters.for_date(DAY), network=True,
        output=tmp_path / "expired.json", clock=lambda: next(values), _test_transport_factory=create)
    assert result["status"] == "AUDIT_WINDOW_MISSED"
    assert instances == {}
    assert not (tmp_path / "expired.json.running").exists()
    assert_safe(result)


def test_write_failure_after_requests_never_claims_zero_network(tmp_path, monkeypatch):
    def fail_write(*args):
        raise PermissionError("isolated_write_failure")

    monkeypatch.setattr(audit, "write_json_atomic", fail_write)
    clock = Clock()
    create, _ = factory(clock)
    result = audit.run_combined_audit(audit.AuditParameters.for_date(DAY), network=True,
        output=tmp_path / "write_failed.json", clock=clock, _test_transport_factory=create)
    assert result["status"] == "AUDIT_EVIDENCE_WRITE_FAILED"
    assert result["network_requests_made"] is None
    assert result["request_attempt_count"] == 11
    assert not (tmp_path / "write_failed.json.running").exists()
    assert_safe(result)


def test_future_native_breadth_event_not_accepted_by_later_collection_deadline(tmp_path):
    clock = Clock()
    create, _ = factory(clock, market_breadth={"future": True})
    result = audit.run_combined_audit(audit.AuditParameters.for_date(DAY), network=True,
        output=tmp_path / "future.json", clock=clock, _test_transport_factory=create)
    assert result["module_results"]["market_breadth"]["error_code"] in {
        "AUDIT_NATIVE_EVENT_AFTER_CUTOFF", "MARKET_SOURCE_TIME_ORDER_INVALID"}
    assert result["module_results"]["market_breadth"]["records"] == []
    assert result["provider_contract_complete"] is False
    assert_safe(result)


def test_existing_evidence_refuses_second_execution_without_factory(tmp_path):
    path = tmp_path / "existing.json"
    path.write_bytes(b"original-evidence")
    anchor = hashlib.sha256(path.read_bytes()).hexdigest()
    clock = Clock()
    create, instances = factory(clock)
    result = audit.run_combined_audit(audit.AuditParameters.for_date(DAY), network=True,
        output=path, clock=clock, _test_transport_factory=create)
    assert result["status"] == "AUDIT_OUTPUT_EXISTS"
    assert instances == {}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == anchor


def test_running_batch_lock_refuses_duplicate_without_network(tmp_path):
    path = tmp_path / "locked.json"
    lock = path.with_name(path.name + ".running")
    lock.touch()
    clock = Clock()
    create, instances = factory(clock)
    result = audit.run_combined_audit(audit.AuditParameters.for_date(DAY), network=True,
        output=path, clock=clock, _test_transport_factory=create)
    assert result["status"] == "AUDIT_ALREADY_RUNNING"
    assert instances == {}
    assert lock.exists()


def test_confirmed_input_contracts_require_real_anchors_and_target_day(tmp_path, monkeypatch):
    monkeypatch.setattr(session_fixtures, "TRADE_DATE", DAY)
    calendar = session_fixtures._calendar()
    session = session_fixtures._confirmation()
    paths = [tmp_path / "calendar.json", tmp_path / "session.json"]
    for path, item in zip(paths, (calendar, session)):
        path.write_text(json.dumps(item), encoding="utf-8")
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    confirmed = audit.confirm_inputs(audit.AuditParameters.for_date(DAY), paths[0], hashes[0], paths[1], hashes[1])
    assert confirmed["session_confirmation_file_sha256"] == hashes[1]
    with pytest.raises(ValueError, match="EXTERNAL_SHA_MISMATCH"):
        audit.confirm_inputs(audit.AuditParameters.for_date(DAY), paths[0], hashes[0], paths[1], "0" * 64)


def test_re_signed_request_count_tampering_and_anchor_mismatch_are_rejected(tmp_path):
    clock = Clock()
    create, _ = factory(clock)
    path = tmp_path / "tamper.json"
    result = audit.run_combined_audit(audit.AuditParameters.for_date(DAY), network=True,
        output=path, clock=clock, _test_transport_factory=create)
    anchor = hashlib.sha256(path.read_bytes()).hexdigest()
    assert not audit.verify_combined_audit_file(path, expected_file_sha256="0" * 64)["evidence_integrity_verified"]
    result["request_attempt_count"] = 13
    result.pop("evidence_hash")
    result["evidence_hash"] = stable_hash(result)
    path.write_text(json.dumps(result), encoding="utf-8")
    assert not audit.verify_combined_audit_file(path, expected_file_sha256=anchor)["evidence_integrity_verified"]
    updated_anchor = hashlib.sha256(path.read_bytes()).hexdigest()
    replay = audit.verify_combined_audit_file(path, expected_file_sha256=updated_anchor)
    assert replay["errors"] == ["aggregate_request_count_invalid"]
