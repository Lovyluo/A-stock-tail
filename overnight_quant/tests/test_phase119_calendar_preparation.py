"""Test-only calendar/session CLI -> independent verifiers -> actual preflight CLI.

All source transports are fixtures. Socket connections and NTP are forbidden.
"""
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from overnight_quant.data import static_source_providers as providers
from overnight_quant.data.market_source_go_nogo import _completed_calendar_errors
from overnight_quant.scripts import completed_calendar_preparation as calendar
from overnight_quant.scripts import run_static_source_validation as cli
from overnight_quant.scripts import run_static_source_evidence_verify as verifier
from overnight_quant.scripts import run_market_session_confirmation as session_cli
from overnight_quant.scripts import run_market_session_confirmation_verify as session_verify
from overnight_quant.tests import test_phase82_tencent_provider_candidates as quotes
from overnight_quant.tests import test_phase88_static_source_contracts as static
from overnight_quant.tests import test_phase108_minute_preflight as downstream
from overnight_quant.tests.test_phase108_minute_preflight import case  # isolated, test-only repository


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("external network/NTP forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("collect_qfq_daily_batch", "collect_stock_news_batch",
                 "collect_global_news_batch", "collect_announcement_batch"):
        monkeypatch.setattr(providers.StaticSourceProviders, name, forbidden)


@pytest.fixture
def prepared_case(case, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return case["clock"]()

    monkeypatch.setattr(calendar, "datetime", FixedDatetime)
    monkeypatch.setattr(cli, "_git_head", lambda: case["head"])
    monkeypatch.setattr(cli, "CACHE_ROOT", case["root"] / "overnight_quant/data/cache")
    body = {"test_only": True, "data": {"sh000001": {"qfqday": static.kline_rows(static.weekdays())}}}
    response = static.response(body, providers.TENCENT_KLINE_URL)
    transport = static.FakeTransport([response])
    monkeypatch.setattr(providers, "StaticUrllibTransport", lambda: transport)
    write = calendar._write
    # No production bypass. Synthetic markers are injected only by this harness.
    monkeypatch.setattr(calendar, "_write", lambda path, value: write(path, {**value, "test_only": True}))
    case.update(transport=transport, response=response, output=cli.CACHE_ROOT / "calendar-test-only")
    return case


def arguments(case):
    return ["--calendar-only", "--network", "--date", downstream.DAY,
            "--codes", ",".join(cli.DEFAULT_CODES), "--approved-code-sha", case["head"],
            "--output", str(case["output"])]


def invoke(case, capsys, args=None):
    code = cli.main(arguments(case) if args is None else args)
    return code, json.loads(capsys.readouterr().out)


def safe(value):
    assert value["data_ready"] is value["hard_gate_authorized"] is False
    assert value["candidates"] == value["tickets"] == value["orders"] == []


def test_actual_calendar_session_preflight_entries(prepared_case, monkeypatch, capsys):
    c = prepared_case
    code, result = invoke(c, capsys)
    assert code == 0 and result["status"] == calendar.SUCCESS, result
    assert c["transport"].request_count == result["network_requests_made"] == 1
    path = Path(result["result_path"])
    files = {p: p.read_bytes() for p in c["output"].iterdir()}
    raw = json.loads((c["output"] / "raw-response.json").read_bytes())
    assert base64.b64decode(raw["content_base64"]) == c["response"].content
    assert raw["raw_hash"] == hashlib.sha256(c["response"].content).hexdigest()
    assert result["trade_dates"] == sorted(set(result["trade_dates"]))
    assert all(day < downstream.DAY for day in result["trade_dates"])
    replay_bytes = []
    for index in range(2):
        replay = c["output"].parent / f"replay-{index}.json"
        assert verifier.main([str(path), "--calendar-only", "--expected-file-sha256", result["file_sha256"],
                              "--output", str(replay)]) == 0
        checked = json.loads(capsys.readouterr().out)
        assert checked["evidence_integrity_verified"] and checked["provider_validation_passed"]
        assert not checked["sampling_authorized"]
        replay_bytes.append(replay.read_bytes())
    assert replay_bytes[0] == replay_bytes[1]
    assert all(p.read_bytes() == value for p, value in files.items())

    monkeypatch.setattr(quotes, "SOURCE_TIME", downstream.DAY.replace("-", "") + "143000")
    quote_transport = quotes.FakeTransport(quotes._response_bytes())
    monkeypatch.setattr(session_cli, "TencentUrllibTransport", lambda: quote_transport)
    monkeypatch.setattr(session_cli, "datetime", calendar.datetime)
    session = c["output"].parent / "session-test-only.json"
    assert session_cli.main(["--network", "--date", downstream.DAY, "--codes", ",".join(cli.DEFAULT_CODES),
                             "--output", str(session)]) == 0
    safe(json.loads(capsys.readouterr().out))
    assert quote_transport.request_count == 1
    assert session_verify.main([str(session), "--expected-file-sha256", calendar._sha(session)]) == 0
    assert json.loads(capsys.readouterr().out)["evidence_integrity_verified"]
    c["inputs"].update(calendar=downstream.pre.ref(path), session=downstream.pre.ref(session))
    real_prepare = downstream.pre.prepare
    monkeypatch.setattr(downstream.pre, "prepare", lambda **kw: real_prepare(**kw, _test=c["test"]))
    args = ["--prepare", "--date", downstream.DAY, "--approved-code-sha", c["head"],
            "--plan", c["plan_ref"]["path"], "--expected-plan-sha256", c["plan_ref"]["sha256"],
            "--output-dir", str(c["tmp"] / "real-cli-preflight-test-only")]
    for key, ref in c["inputs"].items():
        name = key.replace("_", "-")
        args.extend(["--" + name, ref["path"], "--" + name + "-sha256", ref["sha256"]])
    assert downstream.cli.main(args) == 0
    preflight = json.loads(capsys.readouterr().out)
    assert preflight["test_only"] and not preflight["pm_approval_generated"]
    assert downstream.verify(c, Path(preflight["result_path"]))["integrity_verified"]
    assert c["transport"].request_count == 1 and quote_transport.request_count == 1
    safe(preflight)


@pytest.mark.parametrize("flag", ["--date", "--codes", "--approved-code-sha", "--output"])
def test_required_arguments_zero_requests(prepared_case, capsys, flag):
    c = prepared_case
    args = arguments(c)
    start = args.index(flag)
    del args[start:start + 2]
    if flag == "--date":
        with pytest.raises(SystemExit) as error:
            cli.main(args)
        assert error.value.code == 2
        capsys.readouterr()
    else:
        code, result = invoke(c, capsys, args)
        assert code == 2 and result["failure_stage"] == "input_validation"
    assert c["transport"].request_count == 0 and not c["output"].exists()


@pytest.mark.parametrize("flag,value", [
    ("--date", "2026-10-20"), ("--date", "20261021"), ("--date", "bad"),
    ("--codes", ""), ("--codes", "000001"), ("--codes", ",".join(cli.DEFAULT_CODES) + ",000001"),
    ("--approved-code-sha", "a" * 40), ("--output", "../outside-cache"),
])
def test_invalid_input_zero_requests(prepared_case, capsys, flag, value):
    c = prepared_case
    args = arguments(c)
    args[args.index(flag) + 1] = value
    code, result = invoke(c, capsys, args)
    assert code == 2 and result["network_requests_made"] == c["transport"].request_count == 0
    assert not c["output"].exists()
    safe(result)


def test_default_calendar_mode_does_not_request(prepared_case, capsys):
    args = arguments(prepared_case)
    args.remove("--network")
    _, result = invoke(prepared_case, capsys, args)
    assert result["status"] == "CALENDAR_NETWORK_NOT_REQUESTED"
    assert prepared_case["transport"].request_count == 0
    assert not prepared_case["output"].exists()


def test_validator_call_shape_checked_before_network(prepared_case, monkeypatch, capsys):
    def changed_contract(capability, records, *, target_trade_date, codes, additional_required):
        pytest.fail("must not reach validator")
    monkeypatch.setattr(providers, "validate_static_provider_records", changed_contract)
    code, value = invoke(prepared_case, capsys)
    assert code == 2 and value["error_type"] == "TypeError"
    assert value["failure_stage"] == "input_validation" and prepared_case["transport"].request_count == 0


def test_response_sealed_before_business_exception(prepared_case, monkeypatch, capsys):
    c = prepared_case
    def broken(capability, records, *, target_trade_date, codes):
        sealed = c["output"] / "raw-response.json"
        assert sealed.exists() and codes == cli.DEFAULT_CODES
        assert base64.b64decode(json.loads(sealed.read_bytes())["content_base64"]) == c["response"].content
        raise TypeError("synthetic validator failure after response")
    monkeypatch.setattr(providers, "validate_static_provider_records", broken)
    code, value = invoke(c, capsys)
    assert code == 2 and value["network_requests_made"] == c["transport"].request_count == 1
    assert value["failure_stage"] == "business_validation" and value["failure_receipt_persisted"]
    assert not (c["output"] / "completed-calendar.json").exists()
    assert _completed_calendar_errors(value, downstream.DAY, expected_file_sha256=value["file_sha256"],
                                      actual_file_sha256=value["file_sha256"])
    assert verifier.main([value["result_path"], "--calendar-only", "--expected-file-sha256", value["file_sha256"]]) == 2
    checked = json.loads(capsys.readouterr().out)
    assert checked["evidence_integrity_verified"] and not checked["provider_validation_passed"]
    assert checked["preparation_result"] == calendar.FAILED


@pytest.mark.parametrize("outcome", ["bad_json", "empty", "no_dates", "http_error", "request_exception"])
def test_invalid_response_retained_and_never_retried(prepared_case, capsys, outcome):
    c = prepared_case
    response = providers.StaticHttpResponse(
        b"invalid" if outcome == "bad_json" else b"" if outcome == "empty" else b"{}",
        503 if outcome == "http_error" else 200, providers.TENCENT_KLINE_URL)
    c["transport"].responses = [OSError("synthetic connection failure") if outcome == "request_exception" else response]
    code, value = invoke(c, capsys)
    assert code == 2 and c["transport"].request_count == value["network_requests_made"] == 1
    assert value["failure_receipt_persisted"]
    assert not (c["output"] / "completed-calendar.json").exists()
    raw = c["output"] / "raw-response.json"
    if outcome == "request_exception":
        assert not raw.exists() and value["raw_capture"] is None and value["failure_stage"] == "request"
    else:
        assert base64.b64decode(json.loads(raw.read_bytes())["content_base64"]) == response.content
        assert calendar._sha(raw) == value["raw_capture"]["sha256"]
        assert value["failure_stage"] == "provider_parse"


@pytest.mark.parametrize("filename,requests,stage", [
    ("intent.json", 0, "output_reservation"), ("raw-response.json", 1, "raw_seal"),
    ("completed-calendar.json", 1, "contract_write"),
])
def test_write_failure_stops_without_retry(prepared_case, monkeypatch, capsys, filename, requests, stage):
    c = prepared_case
    write = calendar._write
    def denied(path, value):
        if path.name == filename:
            raise PermissionError("synthetic write failure")
        return write(path, value)
    monkeypatch.setattr(calendar, "_write", denied)
    code, value = invoke(c, capsys)
    assert code == 2 and c["transport"].request_count == value["network_requests_made"] == requests
    assert value["failure_stage"] == stage and value["failure_receipt_persisted"]
    assert not (c["output"] / "completed-calendar.json").exists()
    assert "trade_dates" not in value and "qualification_status" not in value


def test_failure_receipt_write_error_is_not_success(prepared_case, monkeypatch, capsys):
    c = prepared_case
    c["transport"].responses = [ValueError("synthetic request failure")]
    write = calendar._write
    def denied(path, value):
        if path.name == "failure-receipt.json":
            raise OSError("synthetic disk full")
        return write(path, value)
    monkeypatch.setattr(calendar, "_write", denied)
    code, value = invoke(c, capsys)
    assert code == 2 and not value["failure_receipt_persisted"] and "disk full" in value["receipt_write_error"]
    assert c["transport"].request_count == 1


def test_existing_and_duplicate_outputs_zero_additional_requests(prepared_case, capsys):
    c = prepared_case
    assert invoke(c, capsys)[0] == 0
    before = {p: p.read_bytes() for p in c["output"].iterdir()}
    code, value = invoke(c, capsys)
    assert code == 2 and value["network_requests_made"] == 0 and c["transport"].request_count == 1
    assert {p: p.read_bytes() for p in c["output"].iterdir()} == before


def test_simultaneous_directory_claim_only_one_request(prepared_case):
    c = prepared_case
    start = Event()
    def call():
        assert start.wait(5)
        return calendar.prepare_calendar(network=True, trade_date=downstream.DAY,
            codes=",".join(cli.DEFAULT_CODES), approved_sha=c["head"], output=c["output"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call) for _ in range(2)]
        start.set()
        values = [future.result(timeout=10) for future in futures]
    assert sum(v["status"] == calendar.SUCCESS for v in values) == 1
    assert c["transport"].request_count == 1
    assert not (c["output"] / "failure-receipt.json").exists()


def test_changed_provider_request_cannot_broaden_scope(prepared_case, monkeypatch, capsys):
    c = prepared_case
    def wrong_request(self):
        self.transport.request("GET", providers.TENCENT_KLINE_URL,
            params={"param": "sz000001,day,,,260,qfq"}, data=None,
            headers={"User-Agent": providers.USER_AGENT, "Referer": "https://gu.qq.com/"}, timeout_seconds=10.0)
        pytest.fail("unexpected dispatch")
    monkeypatch.setattr(providers.StaticSourceProviders, "collect_trading_calendar_batch", wrong_request)
    code, value = invoke(c, capsys)
    assert code == 2 and value["network_requests_made"] == c["transport"].request_count == 0
    assert value["failure_stage"] == "request_contract"


def test_failure_after_contract_write_cannot_be_verified_as_success(prepared_case, monkeypatch, capsys):
    c = prepared_case
    sha = calendar._sha
    def unreadable(path):
        if Path(path).name == "completed-calendar.json":
            raise PermissionError("synthetic final file read failure")
        return sha(path)
    monkeypatch.setattr(calendar, "_sha", unreadable)
    code, value = invoke(c, capsys)
    assert code == 2 and value["failure_receipt_persisted"] and "trade_dates" not in value
    monkeypatch.setattr(calendar, "_sha", sha)
    path = c["output"] / "completed-calendar.json"
    result = calendar.verify_calendar_file(path, expected_file_sha256=sha(path))
    assert not result["provider_validation_passed"] and "calendar_failure_receipt_present" in result["errors"][0]


@pytest.mark.parametrize("mutation", ["dates", "raw", "anchor", "codes", "status", "safety", "intent"])
def test_independent_replay_rejects_tampering(prepared_case, capsys, mutation):
    c = prepared_case
    code, value = invoke(c, capsys)
    assert code == 0
    path = Path(value["result_path"])
    saved = json.loads(path.read_bytes())
    if mutation == "dates": saved["trade_dates"].pop()
    elif mutation == "codes": saved["requested_codes"] = []
    elif mutation == "status": saved["provider_validation_passed"] = False
    elif mutation == "safety": saved["data_ready"] = True
    elif mutation == "raw": (c["output"] / "raw-response.json").write_bytes(b"tampered test-only")
    elif mutation == "intent": (c["output"] / "intent.json").write_bytes(b"tampered test-only")
    path.write_text(json.dumps(saved), encoding="utf-8")
    result = calendar.verify_calendar_file(path, expected_file_sha256="f" * 64 if mutation == "anchor" else calendar._sha(path))
    assert not result["evidence_integrity_verified"] and not result["provider_validation_passed"]
    safe(result)
