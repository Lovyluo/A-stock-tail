from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest
import requests

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_transport_diagnostics import (
    TRANSPORT_DOTNET_HTTPCLIENT,
    TRANSPORT_REQUESTS_CURRENT,
    TRANSPORT_REQUESTS_FIXED_HEADERS,
    compute_diagnostic_evidence_hash,
    diagnostic_requests,
    evaluate_transport_candidates,
    run_transport_diagnostic,
    verify_transport_diagnostic,
    write_json_atomic,
)


TRADE_DATE = "2026-09-28"


class IncrementingClock:
    def __init__(self, start: datetime, step_ms: int = 25):
        self.value = start
        self.step = timedelta(milliseconds=step_ms)

    def __call__(self):
        value = self.value
        self.value += self.step
        return value


class FakeResponse:
    def __init__(self, body: bytes, url: str, status_code: int = 200):
        self.content = body
        self.url = url
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.closed = False

    def close(self):
        self.closed = True


def _environment():
    return {
        "proxy_mode": {"mode": "direct", "configured": False, "endpoints": {}},
        "dns_status": {"ok": True, "addresses": ["127.0.0.1"], "error_type": ""},
        "tls_status": {"ok": True, "protocol": "TLSv1.3", "error_type": ""},
        "proxy_listener_status": {},
    }


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _transport_recorder(*, fail_endpoint="", f170=True):
    calls = []
    board_time = int(datetime(2026, 9, 28, 14, 50, tzinfo=CN_TZ).timestamp())
    industries = {
        "000001": "银行",
        "000333": "家电",
        "600000": "银行",
        "600519": "酿酒",
        "601318": "保险",
    }
    boards = [
        {
            "f3": 1.2,
            "f12": f"BK{index:04d}",
            "f14": name,
            "f104": 10,
            "f105": 5,
            "f106": 1,
            "f124": board_time,
        }
        for index, name in enumerate(sorted(set(industries.values())), 1)
    ]

    def request(method, url, **kwargs):
        params = dict(kwargs["params"])
        calls.append((method, url, params, kwargs))
        endpoint = _endpoint_id(url, params)
        if endpoint == fail_endpoint:
            raise requests.Timeout("single attempt timeout")
        if endpoint == "market_breadth_counts":
            payload = {"data": {"diff": [
                {"f12": "000001", "f14": "上证指数", "f104": 1000, "f105": 900, "f106": 100, "f124": board_time},
                {"f12": "399001", "f14": "深证成指", "f104": 1100, "f105": 1000, "f106": 100, "f124": board_time},
                {"f12": "899050", "f14": "北证50", "f104": 120, "f105": 100, "f106": 10, "f124": board_time},
            ]}}
        elif endpoint == "market_benchmark_f170":
            payload = {"data": {"f57": "000001", "f58": "上证指数", "f86": board_time}}
            payload["data"]["f170" if f170 else "f3"] = 0.25
        elif endpoint == "industry_boards":
            payload = {"data": {"diff": boards}}
        elif endpoint.startswith("industry_mapping:"):
            code = endpoint.split(":", 1)[1]
            payload = {"data": {"f57": code, "f58": code, "f127": industries[code], "f86": board_time}}
        else:
            code = endpoint.split(":", 1)[1]
            payload = {"data": {"code": code, "klines": [
                f"{TRADE_DATE} 14:49,1,2,3,4,5",
                f"{TRADE_DATE} 14:50,2,3,4,5,6",
            ]}}
        full_url = url + "?" + "&".join(f"{key}={value}" for key, value in kwargs["params"])
        return FakeResponse(_json_bytes(payload), full_url)

    return calls, request


def _endpoint_id(url, params):
    fields = params.get("fields")
    if url.endswith("ulist.np/get"):
        return "market_breadth_counts"
    if url.endswith("clist/get"):
        return "industry_boards"
    if url.endswith("fflow/kline/get"):
        code = str(params["secid"]).split(".")[-1]
        return f"fund_flow:{code}"
    if fields == "f57,f58,f86,f170":
        return "market_benchmark_f170"
    code = str(params["secid"]).split(".")[-1]
    return f"industry_mapping:{code}"


def _evidence(transport_id=TRANSPORT_REQUESTS_CURRENT, *, start=None, fail_endpoint="", f170=True):
    calls, request = _transport_recorder(fail_endpoint=fail_endpoint, f170=f170)
    start = start or datetime(2026, 9, 28, 20, 0, tzinfo=CN_TZ)
    evidence = run_transport_diagnostic(
        transport_id=transport_id,
        trade_date=TRADE_DATE,
        round_id="round-1",
        network=True,
        request_callable=request,
        environment_inspector=_environment,
        clock=IncrementingClock(start),
    )
    return evidence, calls


def _verify(evidence):
    return verify_transport_diagnostic(
        evidence,
        expected_file_sha256="a" * 64,
        actual_file_sha256="a" * 64,
    )


def _resign(evidence):
    evidence["evidence_hash"] = compute_diagnostic_evidence_hash(evidence)
    return evidence


def test_diagnostic_request_matrix_is_fixed_and_complete():
    specs = diagnostic_requests()
    assert len(specs) == 13
    assert [item.endpoint_id for item in specs[:3]] == [
        "market_breadth_counts",
        "market_benchmark_f170",
        "industry_boards",
    ]
    assert sum(item.endpoint_id.startswith("industry_mapping:") for item in specs) == 5
    assert sum(item.endpoint_id.startswith("fund_flow:") for item in specs) == 5


def test_requests_paths_each_call_every_request_once_without_fallback():
    current, current_calls = _evidence(TRANSPORT_REQUESTS_CURRENT)
    fixed, fixed_calls = _evidence(TRANSPORT_REQUESTS_FIXED_HEADERS)
    assert len(current_calls) == len(fixed_calls) == 13
    assert current["network_requests_made"] == fixed["network_requests_made"] == 13
    assert current["retry_count"] == current["fallback_count"] == 0
    assert fixed["retry_count"] == fixed["fallback_count"] == 0
    assert current["request_headers"] == {"User-Agent": current["request_headers"]["User-Agent"]}
    assert fixed["request_headers"]["Accept"] == "application/json,text/plain,*/*"
    assert fixed["request_headers"]["Referer"] == "https://quote.eastmoney.com/"
    assert current["candidate_round_passed"] is True
    assert fixed["candidate_round_passed"] is True


def test_timeout_is_one_failed_request_and_does_not_trigger_retry_or_fallback():
    evidence, calls = _evidence(fail_endpoint="industry_boards")
    assert len(calls) == 13
    assert sum(_endpoint_id(url, params) == "industry_boards" for _, url, params, _ in calls) == 1
    assert evidence["candidate_round_passed"] is False
    assert evidence["retry_count"] == evidence["fallback_count"] == 0
    verified = _verify(evidence)
    assert verified["status"] == "MARKET_TRANSPORT_DIAGNOSTIC_VERIFIED"
    assert verified["candidate_round_passed"] is False
    assert verified["data_ready"] is False
    assert verified["candidates"] == verified["tickets"] == verified["orders"] == []


def test_field_contract_requires_stock_get_f170_not_f3():
    evidence, _ = _evidence(f170=False)
    verified = _verify(evidence)
    assert verified["status"] == "MARKET_TRANSPORT_DIAGNOSTIC_VERIFIED"
    assert verified["candidate_round_passed"] is False
    assert any("market_benchmark_f170_invalid" in value for value in evidence["validation_errors"])


def test_resigned_raw_response_tamper_is_rejected():
    evidence, _ = _evidence()
    tampered = deepcopy(evidence)
    tampered["requests"][0]["content_base64"] = "e30="
    _resign(tampered)
    result = _verify(tampered)
    assert result["status"] == "MARKET_TRANSPORT_DIAGNOSTIC_INVALID"
    assert any("response_bytes_invalid" in value or "raw_hash_invalid" in value for value in result["errors"])


def test_resigned_session_window_summary_tamper_is_rejected():
    evidence, _ = _evidence()
    evidence["session_window_round"] = True
    _resign(evidence)
    result = _verify(evidence)
    assert result["status"] == "MARKET_TRANSPORT_DIAGNOSTIC_INVALID"
    assert "session_window_summary_mismatch" in result["errors"]


def test_verifier_replay_is_deterministic():
    evidence, _ = _evidence()
    first = _verify(evidence)
    second = _verify(evidence)
    assert first == second
    assert first["replay_hash"] == second["replay_hash"]


def test_external_sha_anchor_is_required():
    evidence, _ = _evidence()
    result = verify_transport_diagnostic(
        evidence,
        expected_file_sha256="a" * 64,
        actual_file_sha256="b" * 64,
    )
    assert result["status"] == "MARKET_TRANSPORT_DIAGNOSTIC_INVALID"
    assert result["hard_gate_authorized"] is False


def test_three_round_selection_requires_spacing_session_window_and_unique_winner():
    verified = []
    for transport_id in (
        TRANSPORT_REQUESTS_CURRENT,
        TRANSPORT_REQUESTS_FIXED_HEADERS,
        TRANSPORT_DOTNET_HTTPCLIENT,
    ):
        for index, started in enumerate((
            "2026-09-28T20:00:00+08:00",
            "2026-09-28T20:02:00+08:00",
            "2026-09-29T14:40:00+08:00",
        ), 1):
            passed = transport_id == TRANSPORT_REQUESTS_FIXED_HEADERS
            verified.append({
                "status": "MARKET_TRANSPORT_DIAGNOSTIC_VERIFIED",
                "evidence_integrity_verified": True,
                "transport_id": transport_id,
                "round_id": f"round-{index}",
                "candidate_round_passed": passed,
                "source_evidence": {
                    "benchmark_started_at": started,
                    "session_window_round": index == 3,
                    "trade_date": started[:10],
                },
                "replay": {"round_id": f"round-{index}"},
            })
    result = evaluate_transport_candidates(verified)
    assert result["status"] == "EASTMONEY_TRANSPORT_CANDIDATE_SELECTED"
    assert result["selected_transport"] == TRANSPORT_REQUESTS_FIXED_HEADERS
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_three_round_selection_fails_closed_without_session_window():
    rows = []
    for index in range(3):
        rows.append({
            "status": "MARKET_TRANSPORT_DIAGNOSTIC_VERIFIED",
            "evidence_integrity_verified": True,
            "transport_id": TRANSPORT_REQUESTS_CURRENT,
            "candidate_round_passed": True,
            "source_evidence": {
                "benchmark_started_at": f"2026-09-28T20:0{index * 2}:00+08:00",
                "session_window_round": False,
            },
            "replay": {"round_id": f"round-{index}"},
        })
    result = evaluate_transport_candidates(rows)
    assert result["status"] == "EASTMONEY_TRANSPORT_UNQUALIFIED"
    assert result["selected_transport"] is None


def test_atomic_writer_is_non_overwriting(tmp_path):
    target = tmp_path / "evidence.json"
    write_json_atomic(target, {"winner": 1})
    original = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        write_json_atomic(target, {"winner": 2})
    assert hashlib.sha256(target.read_bytes()).hexdigest() == original
