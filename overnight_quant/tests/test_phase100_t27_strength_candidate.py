"""Synthetic fltt=1 behavior only; not native source validation evidence."""
from copy import deepcopy
from datetime import datetime
import base64
import hashlib
import json
from urllib.parse import urlencode

import pytest

from overnight_quant.data import eastmoney_industry_strength_candidate as candidate
from overnight_quant.data.market_source_providers import MARKET_DOTNET_TRANSPORT_VERSION
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_adapters import (
    SourceProviderEnvelope, audit_source_adapters, execute_source_adapter,
)


DATE = "2026-10-09"
CUTOFF = f"{DATE}T14:40:00+08:00"
DEADLINE = f"{DATE}T14:41:05+08:00"


def _fixture(board="BK1239", value=123, precision=2, event="14:37:00"):
    request = candidate.board_strength_request(board)
    data = {"f57": board, "f58": candidate.BOARD_NAMES[board], "f170": value, "f152": precision,
            "f86": int(datetime.fromisoformat(f"{DATE}T{event}+08:00").timestamp()), "f169": -10}
    raw = json.dumps({"rc": 0, "data": data}, ensure_ascii=False).encode("utf-8")
    capture = {
        "origin_source": "eastmoney", "adapter": "direct_http", "source_version": candidate.SOURCE_VERSION,
        "contract_version": candidate.CONTRACT_VERSION, "transport_version": MARKET_DOTNET_TRANSPORT_VERSION,
        "request_hash": stable_hash({"method": "GET", "url": request["url"], "params": sorted(request["params"].items())}),
        "response_url": request["url"] + "?" + urlencode(sorted(request["params"].items())),
        "http_status_code": 200, "redirect_chain": [],
        "content_base64": base64.b64encode(raw).decode("ascii"), "raw_hash": hashlib.sha256(raw).hexdigest(),
        "request_started_at": f"{DATE}T14:37:01.100+08:00",
        "request_completed_at": f"{DATE}T14:37:01.400+08:00",
        "process_started_at": f"{DATE}T14:37:01.000+08:00",
        "process_completed_at": f"{DATE}T14:37:01.500+08:00",
        "observed_at": f"{DATE}T14:37:01.500+08:00",
        "available_at": f"{DATE}T14:37:01.501+08:00",
    }
    return request, capture


def _parse(request, capture, **kwargs):
    return candidate.parse_captured_board_strength(
        capture, request=request, trade_date=DATE, feature_cutoff=CUTOFF, collection_deadline=DEADLINE,
        expected_raw_sha256=kwargs.pop("expected_raw_sha256", capture["raw_hash"]),
        test_only=kwargs.pop("test_only", True), **kwargs,
    )


def _rewrite(capture, **changes):
    content = json.loads(base64.b64decode(capture["content_base64"]))
    content["data"].update(changes)
    raw = json.dumps(content, ensure_ascii=False).encode("utf-8")
    capture.update(content_base64=base64.b64encode(raw).decode("ascii"), raw_hash=hashlib.sha256(raw).hexdigest())


def _safe(result):
    assert result["records"] == []
    assert result["qualification"] == "0/3"
    for key in ("data_ready", "hard_gate_authorized", "provider_activated", "source_qualification_verified",
                "provider_contract_complete", "automatic_configuration_change"):
        assert result[key] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []
    assert result["network_requests_made"] == 0


@pytest.mark.parametrize("value,precision,expected", [(123, 2, 1.23), (-123, 2, -1.23), (0, 2, 0.0), (3, 0, 3.0)])
def test_signed_zero_native_precision_conversion(value, precision, expected):
    request, capture = _fixture(value=value, precision=precision)
    result = _parse(request, capture)
    assert result["status"] == "INDUSTRY_STRENGTH_CANDIDATE_PARSED"
    row = result["audit_records"][0]
    assert row["test_only"] is True
    assert row["payload"]["change_pct"] == expected
    assert row["payload"]["field_units"]["change_pct"] == "percent"
    assert row["payload"]["raw_fields"]["f170"] == value
    assert "relative_strength_pct" not in row["payload"]
    assert row["event_time"] == f"{DATE}T14:37:00+08:00"
    _safe(result)


@pytest.mark.parametrize("board", sorted(candidate.BOARD_NAMES))
def test_four_exact_board_identities(board):
    request, capture = _fixture(board=board)
    assert _parse(request, capture)["audit_records"][0]["payload"]["board_code"] == board


@pytest.mark.parametrize("precision", [None, "2", True, -1, 9, 2.0])
def test_unknown_or_non_integer_precision_never_zero_filled(precision):
    request, capture = _fixture(precision=precision)
    result = _parse(request, capture)
    assert result["error_code"] == "INDUSTRY_STRENGTH_PRECISION_INVALID"
    assert result["audit_records"] == []
    _safe(result)


@pytest.mark.parametrize("value", [None, "-", "123", True, float("nan"), float("inf")])
def test_invalid_native_value_rejected(value):
    request, capture = _fixture(value=value)
    result = _parse(request, capture)
    assert result["error_code"] == "INDUSTRY_STRENGTH_VALUE_INVALID"
    _safe(result)


@pytest.mark.parametrize("field,value", [("f57", "BK1277"), ("f58", "wrong-name"), ("f86", "0"), ("f86", True)])
def test_wrong_identity_or_native_timestamp_rejected(field, value):
    request, capture = _fixture()
    _rewrite(capture, **{field: value})
    result = _parse(request, capture)
    assert not result["execution_ok"]
    assert result["audit_records"] == []
    _safe(result)


def test_late_native_second_not_minute_truncated():
    request, capture = _fixture(event="14:40:01")
    for field in ("request_started_at", "request_completed_at", "process_started_at", "process_completed_at", "observed_at", "available_at"):
        capture[field] = f"{DATE}T14:40:02+08:00"
    result = _parse(request, capture)
    assert result["error_code"] == "INDUSTRY_STRENGTH_EVENT_AFTER_CUTOFF"
    _safe(result)


@pytest.mark.parametrize("field,value", [
    ("available_at", f"{DATE}T14:41:05.001+08:00"),
    ("request_completed_at", f"{DATE}T14:37:00+08:00"),
    ("observed_at", f"{DATE}T06:37:01.500Z"),
    ("available_at", f"{DATE}T14:37:01.501"),
    ("process_started_at", f"{DATE}T14:36:58+08:00"),
])
def test_time_order_timezone_and_process_budget(field, value):
    request, capture = _fixture()
    capture[field] = value
    result = _parse(request, capture)
    assert not result["execution_ok"]
    _safe(result)


def test_old_fltt2_contract_is_not_reinterpreted_or_mutated():
    request, capture = _fixture()
    request["params"]["fltt"] = "2"
    original = deepcopy(capture)
    result = _parse(request, capture)
    assert result["error_code"] == "INDUSTRY_STRENGTH_REQUEST_CONTRACT_MISMATCH"
    assert capture == original
    request, capture = _fixture()
    capture["source_version"] = "eastmoney_coreconception+board_constituent_breadth_v1"
    assert _parse(request, capture)["error_code"] == "INDUSTRY_STRENGTH_IDENTITY_OR_VERSION_MISMATCH"


def test_external_raw_anchor_rejects_resigned_capture():
    request, capture = _fixture()
    original_hash = capture["raw_hash"]
    _rewrite(capture, f170=999)
    result = _parse(request, capture, expected_raw_sha256=original_hash)
    assert result["error_code"] == "INDUSTRY_STRENGTH_RAW_HASH_MISMATCH"
    _safe(result)


def test_request_parameters_and_external_anchor_strict():
    request, capture = _fixture()
    assert _parse(request, capture, expected_raw_sha256="a")["error_code"] == "INDUSTRY_STRENGTH_EXTERNAL_RAW_ANCHOR_INVALID"
    request["params"]["fields"] += ",f3"
    assert _parse(request, capture)["error_code"] == "INDUSTRY_STRENGTH_REQUEST_CONTRACT_MISMATCH"


@pytest.mark.parametrize("field", ["f170", "f152", "f86"])
def test_missing_required_native_field_is_not_repaired(field):
    request, capture = _fixture()
    payload = json.loads(base64.b64decode(capture["content_base64"]))
    payload["data"].pop(field)
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    capture.update(content_base64=base64.b64encode(raw).decode("ascii"), raw_hash=hashlib.sha256(raw).hexdigest())
    result = _parse(request, capture)
    assert not result["execution_ok"]
    assert result["audit_records"] == []
    _safe(result)


@pytest.mark.parametrize("field,value", [
    ("origin_source", "tencent"), ("transport_version", "eastmoney_requests_v1"),
    ("http_status_code", True), ("response_url", "https://example.com/"),
    ("redirect_chain", ["https://push2.eastmoney.com/"]), ("content_base64", "!"),
])
def test_untrusted_capture_cannot_obtain_candidate_parse(field, value):
    request, capture = _fixture()
    capture[field] = value
    result = _parse(request, capture)
    assert not result["execution_ok"]
    _safe(result)


def test_native_event_exact_cutoff_and_precision_preserved():
    request, capture = _fixture(event="14:40:00")
    for field in ("request_started_at", "request_completed_at", "process_started_at", "process_completed_at", "observed_at", "available_at"):
        capture[field] = f"{DATE}T14:40:00+08:00"
    assert _parse(request, capture)["audit_records"][0]["event_time"] == CUTOFF


def test_duplicate_json_field_and_non_boolean_test_flag_rejected():
    request, capture = _fixture()
    assert _parse(request, capture, test_only="true")["error_code"] == "INDUSTRY_STRENGTH_REQUEST_INVALID"
    raw = b'{"rc":0,"rc":0,"data":{}}'
    capture.update(content_base64=base64.b64encode(raw).decode("ascii"), raw_hash=hashlib.sha256(raw).hexdigest())
    assert _parse(request, capture)["error_code"] == "INDUSTRY_STRENGTH_DUPLICATE_JSON_FIELD"


def test_deterministic_replay_and_input_dictionary_order():
    request, capture = _fixture()
    result = _parse(request, capture)
    request = dict(reversed(list(request.items())))
    request["params"] = dict(reversed(list(request["params"].items())))
    assert stable_hash(result) == stable_hash(_parse(request, dict(reversed(list(capture.items())))))


def test_no_network_no_factory_no_production_activation(monkeypatch):
    import requests
    import socket
    def forbidden(*args, **kwargs):
        pytest.fail("network or default constructor forbidden")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    plan = candidate.board_strength_request_plan()
    assert plan["planned_request_count"] == 4
    assert plan["retry_count"] == plan["fallback_count"] == 0
    assert all(row["params"]["fltt"] == "1" for row in plan["requests"])
    request, capture = _fixture()
    result = _parse(request, capture, test_only=False)
    assert result["audit_records"][0]["test_only"] is False
    _safe(result)
    audit = audit_source_adapters(environ={})
    assert audit["bound_count"] == 8 and audit["candidate_count"] == 7
    called = []
    # An unregistered version cannot invoke an envelope or a bare callable.
    result = execute_source_adapter(
        "industry_snapshot", origin_source="eastmoney", adapter="direct_http",
        source_version=candidate.SOURCE_VERSION,
        provider_envelope=SourceProviderEnvelope("candidate.fltt1", lambda: called.append(True)), environ={},
    )
    assert result["status"] != "SOURCE_ADAPTER_BOUND"
    assert called == []
    assert result["hard_gate_authorized"] is False
