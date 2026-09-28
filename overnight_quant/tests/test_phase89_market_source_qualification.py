from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json

import pytest
import requests

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_CLIST_URL,
    EASTMONEY_FUND_FLOW_URL,
    EASTMONEY_STOCK_URL,
    EASTMONEY_ULIST_URL,
    FIXED_CODES,
    INDUSTRY_CLASSIFICATION_VERSION,
    LEGACY_EVIDENCE_SCHEMA_VERSION,
    MARKET_REQUESTS_TRANSPORT_VERSION,
    MARKET_STOCK_POOL_VERSION,
    MAX_RESPONSE_BYTES,
    PREVIOUS_EVIDENCE_SCHEMA_VERSION,
    PROVIDER_KEYS,
    SOURCE_IDENTITIES,
    EastmoneyMarketSourceProviders,
    MarketHttpResponse,
    MarketRequestsTransport,
    MarketSourceContractError,
    compute_legacy_market_source_verifier_contract_hash,
    compute_previous_market_source_verifier_contract_hash,
    validate_market_source_records,
)
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED,
    SourceProviderEnvelope,
    audit_source_adapters,
    execute_source_adapter,
)
from overnight_quant.data.source_capability_registry import (
    get_source_capability_registry,
)
from overnight_quant.scripts.run_market_source_evidence_verify import (
    EVIDENCE_INVALID,
    EVIDENCE_VERIFIED,
    verify_file,
    verify_market_source_evidence,
)
from overnight_quant.scripts import run_market_source_validation as validation
from overnight_quant.scripts.run_market_source_validation import (
    file_sha256,
    run_market_source_validation,
)


TRADE_DATE = "2026-09-18"
CUTOFF = "2026-09-18T14:50:00+08:00"
DEADLINE = "2026-09-18T14:51:05+08:00"
EVENT_TS = int(datetime(2026, 9, 18, 14, 50, tzinfo=CN_TZ).timestamp())
EVENT_TS_PLUS_SECONDS = int(datetime(2026, 9, 18, 14, 50, 5, tzinfo=CN_TZ).timestamp())
EVENT_TS_AFTER_CUTOFF_MINUTE = int(datetime(2026, 9, 18, 14, 51, tzinfo=CN_TZ).timestamp())


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.request_count = 0
        self.transport_version = MARKET_REQUESTS_TRANSPORT_VERSION

    def request(self, method, url, *, params, headers, timeout_seconds):
        self.request_count += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        base = datetime(2026, 9, 18, 14, 50, 1, tzinfo=CN_TZ)
        offset = timedelta(milliseconds=(self.request_count - 1) * 2)
        return replace(
            value,
            request_started_at=(base + offset).isoformat(timespec="microseconds"),
            request_completed_at=(
                base + offset + timedelta(microseconds=500)
            ).isoformat(timespec="microseconds"),
        )


class Clock:
    def __init__(self, *, late=False):
        base = datetime(2026, 9, 18, 14, 50, 1, tzinfo=CN_TZ)
        self.values = [base + timedelta(milliseconds=index) for index in range(100)]
        if late:
            self.values[1] = datetime(2026, 9, 18, 14, 51, 5, 1_000, tzinfo=CN_TZ)

    def __call__(self):
        return self.values.pop(0)


def response(payload, url=EASTMONEY_CLIST_URL):
    return MarketHttpResponse(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
        200,
        url,
        {},
        "2026-09-18T14:50:01.000000+08:00",
        "2026-09-18T14:50:01.000500+08:00",
        MARKET_REQUESTS_TRANSPORT_VERSION,
    )


def market_responses(*, incomplete=False, invalid_total=False):
    rows = [
        {"f12": "000001", "f14": "上证指数", "f104": 2, "f105": 1, "f106": 0, "f124": EVENT_TS_PLUS_SECONDS},
        {"f12": "399001", "f14": "深证成指", "f104": 3, "f105": 1, "f106": 1, "f124": EVENT_TS},
        {"f12": "899050", "f14": "北证50", "f104": 1, "f105": 1, "f106": 0, "f124": EVENT_TS},
    ]
    if incomplete:
        rows = rows[:-1]
    if invalid_total:
        rows[0]["f104"] = "-"
    return [
        response({"data": {"diff": rows}}, EASTMONEY_ULIST_URL),
        response(
            {"data": {"f170": 0.42, "f57": "000001", "f58": "上证指数", "f86": EVENT_TS}},
            EASTMONEY_STOCK_URL,
        ),
    ]


def industry_responses(
    *,
    missing_mapping=False,
    mapping_after_cutoff=False,
    mapping_after_deadline=False,
    board_after_cutoff=False,
):
    industries = ["银行", "家电", "银行", "白酒", "保险"]
    boards = []
    for index, name in enumerate(sorted(set(industries)), 1):
        boards.append({
            "f12": f"BK{index:04d}", "f14": name, "f3": 1.2,
            "f104": 6, "f105": 3, "f106": 1,
            "f124": EVENT_TS_AFTER_CUTOFF_MINUTE if board_after_cutoff else EVENT_TS_PLUS_SECONDS,
        })
    values = [response({"data": {"total": len(boards), "diff": boards}})]
    for code, name in zip(FIXED_CODES, industries):
        values.append(response({"data": {
            "f57": code,
            "f58": code,
            "f127": "不存在行业" if missing_mapping and code == FIXED_CODES[0] else name,
            "f86": (
                "2026-09-18T14:51:05.001+08:00"
                if mapping_after_deadline
                else EVENT_TS_PLUS_SECONDS if mapping_after_cutoff else EVENT_TS
            ),
        }}, EASTMONEY_STOCK_URL))
    return values


def fund_responses(*, missing_code=False):
    values = []
    for code in FIXED_CODES:
        lines = [] if missing_code and code == FIXED_CODES[-1] else [
            f"{TRADE_DATE} 14:50,100,10,20,30,40,0"
        ]
        values.append(response({"data": {"klines": lines}}, EASTMONEY_FUND_FLOW_URL))
    return values


def provider(responses, *, late=False):
    return EastmoneyMarketSourceProviders(
        FIXED_CODES,
        trade_date=TRADE_DATE,
        feature_cutoff=CUTOFF,
        collection_deadline=DEADLINE,
        transport=FakeTransport(responses),
        clock=Clock(late=late),
    )


class RequestsResponse:
    def __init__(
        self,
        body=b'{"data":{}}',
        *,
        status_code=200,
        url=EASTMONEY_ULIST_URL,
        headers=None,
    ):
        self.body = body
        self.status_code = status_code
        self.url = url
        self.headers = dict(headers or {})
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        yield self.body

    def close(self):
        self.closed = True


def test_requests_transport_single_success_uses_tls_and_environment_proxy():
    observed = []
    upstream = RequestsResponse()

    def request_callable(*args, **kwargs):
        observed.append((args, kwargs))
        return upstream

    transport = MarketRequestsTransport(request_callable=request_callable)
    result = transport.request(
        "GET",
        EASTMONEY_ULIST_URL,
        params={"b": "2", "a": "1"},
        headers={"User-Agent": "test"},
        timeout_seconds=2,
    )
    assert transport.request_count == 1
    assert result.transport_version == MARKET_REQUESTS_TRANSPORT_VERSION
    assert result.content == b'{"data":{}}'
    assert upstream.closed is True
    assert observed[0][1]["verify"] is True
    assert observed[0][1]["allow_redirects"] is True
    assert "proxies" not in observed[0][1]


@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (requests.Timeout("timeout"), "MARKET_SOURCE_TIMEOUT"),
        (requests.ConnectionError("disconnect"), "MARKET_SOURCE_REQUEST_FAILED"),
    ],
)
def test_requests_transport_failure_is_single_attempt_without_fallback(
    failure, error_code
):
    calls = 0

    def request_callable(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise failure

    transport = MarketRequestsTransport(request_callable=request_callable)
    with pytest.raises(MarketSourceContractError, match=error_code):
        transport.request(
            "GET",
            EASTMONEY_ULIST_URL,
            params={},
            headers={},
            timeout_seconds=2,
        )
    assert calls == transport.request_count == 1


@pytest.mark.parametrize(
    ("upstream", "error_code"),
    [
        (RequestsResponse(status_code=503), "MARKET_SOURCE_HTTP_503"),
        (
            RequestsResponse(url="https://push2his.eastmoney.com/api/qt/test"),
            "MARKET_SOURCE_RESPONSE_URL_INVALID",
        ),
        (
            RequestsResponse(url="http://push2.eastmoney.com/api/qt/test"),
            "MARKET_SOURCE_RESPONSE_URL_INVALID",
        ),
        (RequestsResponse(body=b""), "MARKET_SOURCE_EMPTY_RESPONSE"),
        (
            RequestsResponse(body=b"x" * (MAX_RESPONSE_BYTES + 1)),
            "MARKET_SOURCE_RESPONSE_TOO_LARGE",
        ),
    ],
)
def test_requests_transport_rejects_invalid_response(upstream, error_code):
    transport = MarketRequestsTransport(request_callable=lambda *args, **kwargs: upstream)
    with pytest.raises(MarketSourceContractError, match=error_code):
        transport.request(
            "GET",
            EASTMONEY_ULIST_URL,
            params={},
            headers={},
            timeout_seconds=2,
        )
    assert transport.request_count == 1
    assert upstream.closed is True


def successful_runner(task, deadline_ms, worker_command):
    capability = task["capability"]
    responses = {
        "market_breadth": market_responses(),
        "industry_snapshot": industry_responses(),
        "fund_flow": fund_responses(),
    }[capability]
    instance = provider(responses)
    batch = {
        "market_breadth": instance.collect_market_breadth_batch,
        "industry_snapshot": instance.collect_industry_batch,
        "fund_flow": instance.collect_fund_flow_batch,
    }[capability]()
    serialized = []
    for item in batch.responses:
        raw = item["raw_bytes"]
        serialized.append({
            **{key: value for key, value in item.items() if key != "raw_bytes"},
            "encoding": "base64",
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "byte_count": len(raw),
        })
    return {
        "ok": True,
        "payload": {
            "records": list(batch.records),
            "responses": serialized,
            "batch_status": batch.status,
        },
        "elapsed_ms": 25,
    }


def complete_evidence(tmp_path, monkeypatch, name="complete.json"):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    return run_market_source_validation(
        network=True,
        trade_date=TRADE_DATE,
        output=tmp_path / name,
        worker_runner=successful_runner,
    )


def resign_evidence(evidence):
    evidence["evidence_hash"] = validation.compute_evidence_hash(evidence)
    return evidence


def test_registry_adds_three_unqualified_candidates_without_binding():
    capabilities = get_source_capability_registry()
    identities = {
        (row["capability"], row["origin_source"], row["adapter"], row["source_version"]): row
        for row in capabilities
    }
    for capability, source_identity in SOURCE_IDENTITIES.items():
        row = identities[(capability, *source_identity)]
        assert row["qualification_status"] == "unqualified"
        assert row["hard_gate_eligible"] is False
        assert row["qualification_progress"] == "0/3"
    audit = audit_source_adapters(environ={})
    assert audit["bound_count"] == 8
    assert audit["candidate_count"] == 7
    assert audit["network_requests_made"] == 0
    assert audit["data_ready"] is False
    assert audit["candidates"] == audit["tickets"] == audit["orders"] == []


@pytest.mark.parametrize("capability", sorted(SOURCE_IDENTITIES))
def test_production_candidate_never_calls_provider(capability):
    origin_source, adapter, source_version = SOURCE_IDENTITIES[capability]
    called = 0

    def forged():
        nonlocal called
        called += 1
        return []

    result = execute_source_adapter(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        source_version=source_version,
        provider_envelope=SourceProviderEnvelope(PROVIDER_KEYS[capability], forged),
        environ={},
    )
    assert result["status"] == SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
    assert result["provider_called"] is False
    assert called == 0
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_market_breadth_uses_full_pool_counts_and_eastmoney_index():
    batch = provider(market_responses()).collect_market_breadth_batch()
    assert len(batch.records) == 1
    payload = batch.records[0]["payload"]
    assert payload["stock_pool_version"] == MARKET_STOCK_POOL_VERSION
    assert payload["stock_pool_scope"] == "sse+szse+bse_index_breadth_counts"
    assert payload["total_count"] == 10
    assert payload["valid_count"] == 10
    assert payload["breadth_ratio"] == pytest.approx(6 / 10)
    assert payload["index_change_pct"] == 0.42
    assert payload["benchmark_change_field"] == "f170"
    assert set(payload["index_breadth"]) == {"000001", "399001", "899050"}
    assert payload["raw_benchmark_event_time"] == datetime.fromtimestamp(
        EVENT_TS, tz=CN_TZ
    ).isoformat()
    assert batch.records[0]["event_time"] == CUTOFF
    assert batch.records[0]["origin_source"] == "eastmoney"
    assert validate_market_source_records("market_breadth", batch.records)["valid"]


def test_market_breadth_rejects_f3_when_stock_f170_is_missing():
    responses = market_responses()
    responses[-1] = response(
        {"data": {"f3": 0.42, "f57": "000001", "f58": "上证指数", "f86": EVENT_TS}},
        EASTMONEY_STOCK_URL,
    )
    with pytest.raises(MarketSourceContractError, match="MARKET_INDEX_CHANGE_INVALID"):
        provider(responses).collect_market_breadth_batch()


def test_ulist_f170_never_changes_stock_get_benchmark_change():
    responses = market_responses()
    payload = json.loads(responses[0].content.decode("utf-8"))
    for row in payload["data"]["diff"]:
        row["f170"] = 9999
    responses[0] = response(payload, EASTMONEY_ULIST_URL)
    batch = provider(responses).collect_market_breadth_batch()
    assert batch.records[0]["payload"]["index_change_pct"] == 0.42


def test_market_stock_pool_incomplete_fails_closed():
    with pytest.raises(MarketSourceContractError, match="MARKET_STOCK_POOL_INCOMPLETE"):
        provider(market_responses(incomplete=True)).collect_market_breadth_batch()


def test_industry_mapping_and_breadth_share_one_classification():
    batch = provider(industry_responses(mapping_after_cutoff=True)).collect_industry_batch()
    assert {row["payload"]["code"] for row in batch.records} == set(FIXED_CODES)
    assert {row["payload"]["classification_version"] for row in batch.records} == {
        INDUSTRY_CLASSIFICATION_VERSION
    }
    assert {row["event_time"] for row in batch.records} == {CUTOFF}
    assert {
        row["payload"]["mapping_time_semantics"] for row in batch.records
    } == {"static_classification_observed_before_deadline"}
    assert validate_market_source_records("industry_snapshot", reversed(batch.records))["valid"]


def test_industry_board_event_after_cutoff_minute_fails_closed():
    with pytest.raises(MarketSourceContractError, match="INDUSTRY_EVENT_AFTER_CUTOFF"):
        provider(industry_responses(board_after_cutoff=True)).collect_industry_batch()


def test_industry_mapping_after_collection_deadline_fails_closed():
    with pytest.raises(MarketSourceContractError, match="INDUSTRY_MAPPING_AFTER_DEADLINE"):
        provider(industry_responses(mapping_after_deadline=True)).collect_industry_batch()


def test_industry_mapping_mismatch_rejects_whole_batch():
    with pytest.raises(MarketSourceContractError, match="INDUSTRY_MAPPING_MISSING"):
        provider(industry_responses(missing_mapping=True)).collect_industry_batch()


def test_fund_flow_requires_five_stocks_and_explicit_unit_semantics():
    batch = provider(fund_responses()).collect_fund_flow_batch()
    assert len(batch.records) == 5
    assert validate_market_source_records("fund_flow", reversed(batch.records))["valid"]
    tampered = deepcopy(list(batch.records))
    tampered[0]["payload"]["amount_unit"] = "CNY_10K"
    assert not validate_market_source_records("fund_flow", tampered)["valid"]


def test_fund_flow_missing_stock_rejects_whole_batch():
    with pytest.raises(MarketSourceContractError, match="FUND_FLOW_MISSING"):
        provider(fund_responses(missing_code=True)).collect_fund_flow_batch()


def test_late_provider_completion_returns_no_partial_records():
    with pytest.raises(MarketSourceContractError, match="COLLECTION_DEADLINE_EXCEEDED"):
        provider(market_responses(), late=True).collect_market_breadth_batch()


def test_source_mixing_and_sina_proxy_are_rejected():
    records = list(provider(fund_responses()).collect_fund_flow_batch().records)
    records[0]["origin_source"] = "sina"
    records[0]["source"] = "sina_money_flow"
    records[0]["payload"]["is_proxy"] = True
    contract = validate_market_source_records("fund_flow", records)
    assert contract["valid"] is False
    assert "source_identity_mismatch" in contract["errors"]
    assert "fund_flow_payload_invalid" in contract["errors"]


def test_timeout_failure_evidence_is_safe_and_non_qualifying(tmp_path, monkeypatch):
    def timeout_runner(task, deadline_ms, worker_command):
        return {
            "ok": False,
            "error_code": "REQUEST_DEADLINE_EXCEEDED",
            "request_timed_out": True,
            "worker_terminated": True,
            "elapsed_ms": deadline_ms,
        }

    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / "timeout.json"
    result = run_market_source_validation(
        network=True,
        trade_date=TRADE_DATE,
        output=output,
        worker_runner=timeout_runner,
    )
    assert result["status"] == "MARKET_SOURCE_NETWORK_VALIDATION_FAILED"
    assert result["provider_validation_passed"] is False
    assert all(
        row["error_code"] == "REQUEST_DEADLINE_EXCEEDED"
        for row in result["capability_results"].values()
    )
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_immutable_evidence_external_anchor_and_double_replay(tmp_path, monkeypatch):
    output = tmp_path / "complete.json"
    result = complete_evidence(tmp_path, monkeypatch)
    anchor = file_sha256(output)
    first = verify_file(output, expected_file_sha256=anchor)
    second = verify_file(output, expected_file_sha256=anchor)
    assert result["provider_validation_passed"] is True
    assert first["status"] == second["status"] == EVIDENCE_VERIFIED
    assert first["replay_hash"] == second["replay_hash"]
    assert first["actual_file_sha256"] == second["actual_file_sha256"] == anchor
    assert first["data_ready"] is False
    assert first["candidates"] == first["tickets"] == first["orders"] == []


def test_resigned_hash_tamper_remains_invalid():
    evidence = {
        "evidence_schema_version": "market_source_evidence_v2",
        "evidence_hash": "",
        "provider_keys": PROVIDER_KEYS,
        "requested_codes": list(FIXED_CODES),
        "provider_verifier_contract_hash": "f" * 64,
        "capability_registry_hash": "e" * 64,
        "capability_results": {},
        "records_by_capability": {},
        "raw_responses": {},
        "provider_validation_passed": False,
        "automatic_configuration_change": False,
        "automatic_qualification_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [], "tickets": [], "orders": [],
    }
    material = dict(evidence)
    material.pop("evidence_hash")
    evidence["evidence_hash"] = stable_hash(material)
    result = verify_market_source_evidence(
        evidence, expected_file_sha256="a" * 64
    )
    assert result["status"] == EVIDENCE_INVALID
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_resigned_market_internal_detail_tamper_is_rejected(tmp_path, monkeypatch):
    evidence = deepcopy(complete_evidence(tmp_path, monkeypatch))
    record = evidence["records_by_capability"]["market_breadth"][0]
    record["payload"]["index_breadth"]["000001"]["up_count"] += 1
    contract = validate_market_source_records("market_breadth", [record])
    evidence["capability_results"]["market_breadth"]["records_hash"] = contract[
        "records_hash"
    ]
    result = verify_market_source_evidence(
        resign_evidence(evidence), expected_file_sha256="a" * 64
    )
    assert result["status"] == EVIDENCE_INVALID
    assert any(
        "market_breadth:records_replay_mismatch" in error
        or "market_breadth:market_payload_invalid" in error
        for error in result["errors"]
    )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("mapping_as_formal_event", "industry_snapshot:industry_payload_invalid"),
        ("missing_time_semantics", "industry_snapshot:industry_payload_invalid"),
        ("board_raw_time_changed", "industry_snapshot:industry_payload_invalid"),
        ("source_version_changed", "industry_snapshot:source_identity_mismatch"),
        ("mapping_changed_surface_consistent", "industry_snapshot:records_replay_mismatch"),
    ],
)
def test_resigned_industry_timing_tamper_is_rejected(
    tmp_path, monkeypatch, mutation, expected_error
):
    evidence = deepcopy(complete_evidence(tmp_path, monkeypatch, f"{mutation}.json"))
    record = evidence["records_by_capability"]["industry_snapshot"][0]
    payload = record["payload"]
    if mutation == "mapping_as_formal_event":
        payload["raw_mapping_event_time"] = "2026-09-18T14:49:30+08:00"
        record["event_time"] = "2026-09-18T14:49:00+08:00"
    elif mutation == "missing_time_semantics":
        payload.pop("mapping_time_semantics")
    elif mutation == "board_raw_time_changed":
        payload["raw_board_event_time"] = "2026-09-18T14:49:30+08:00"
    elif mutation == "source_version_changed":
        record["source_version"] = "push2_stock_industry+board_breadth_v2099-01-01"
    else:
        response_item = evidence["raw_responses"]["industry_snapshot"][1]
        raw = base64.b64decode(response_item["content_base64"])
        raw_payload = json.loads(raw.decode("utf-8"))
        raw_payload["data"]["f127"] = "家电"
        changed = json.dumps(
            raw_payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        response_item["content_base64"] = base64.b64encode(changed).decode("ascii")
        response_item["byte_count"] = len(changed)
        response_item["raw_hash"] = hashlib.sha256(changed).hexdigest()
    contract = validate_market_source_records(
        "industry_snapshot", evidence["records_by_capability"]["industry_snapshot"]
    )
    evidence["capability_results"]["industry_snapshot"]["records_hash"] = contract[
        "records_hash"
    ]
    result = verify_market_source_evidence(
        resign_evidence(evidence), expected_file_sha256="b" * 64
    )
    assert result["status"] == EVIDENCE_INVALID
    assert any(expected_error in error for error in result["errors"])
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_legacy_v1_evidence_is_verified_for_audit_only():
    evidence = {
        "evidence_schema_version": LEGACY_EVIDENCE_SCHEMA_VERSION,
        "evidence_hash": "",
        "provider_keys": PROVIDER_KEYS,
        "requested_codes": list(FIXED_CODES),
        "provider_verifier_contract_hash": (
            compute_legacy_market_source_verifier_contract_hash()
        ),
        "capability_registry_hash": (
            "f4e91460aa67674ce536b70184d85b05cfc6fedc8526fa27f16e3f3f616fd833"
        ),
        "provider_validation_passed": True,
        "automatic_configuration_change": False,
        "automatic_qualification_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    result = verify_market_source_evidence(
        resign_evidence(evidence), expected_file_sha256="c" * 64
    )
    assert result["status"] == EVIDENCE_VERIFIED
    assert result["evidence_integrity_verified"] is True
    assert result["provider_validation_passed"] is False
    assert result["audit_only"] is True
    assert result["qualification_eligible"] is False


def test_previous_v2_evidence_is_verified_for_audit_only():
    evidence = {
        "evidence_schema_version": PREVIOUS_EVIDENCE_SCHEMA_VERSION,
        "evidence_hash": "",
        "provider_keys": PROVIDER_KEYS,
        "requested_codes": list(FIXED_CODES),
        "provider_verifier_contract_hash": (
            compute_previous_market_source_verifier_contract_hash()
        ),
        "capability_registry_hash": (
            "eba7d83802c164ff747d0271d2b799724bbc15ee9886c031b3db59511f8d15ed"
        ),
        "provider_validation_passed": True,
        "automatic_configuration_change": False,
        "automatic_qualification_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    result = verify_market_source_evidence(
        resign_evidence(evidence), expected_file_sha256="d" * 64
    )
    assert result["status"] == EVIDENCE_VERIFIED
    assert result["evidence_integrity_verified"] is True
    assert result["provider_validation_passed"] is False
    assert result["audit_only"] is True
    assert result["qualification_eligible"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transport_version", "eastmoney_requests_v999"),
        ("source_version", "push2_fflow_kline_requests_v2099-01-01"),
    ],
)
def test_resigned_v3_transport_or_source_version_tamper_is_rejected(
    tmp_path, monkeypatch, field, value
):
    evidence = deepcopy(complete_evidence(tmp_path, monkeypatch, f"{field}.json"))
    if field == "transport_version":
        evidence[field] = value
    else:
        evidence["records_by_capability"]["fund_flow"][0][field] = value
        contract = validate_market_source_records(
            "fund_flow", evidence["records_by_capability"]["fund_flow"]
        )
        evidence["capability_results"]["fund_flow"]["records_hash"] = contract[
            "records_hash"
        ]
    result = verify_market_source_evidence(
        resign_evidence(evidence), expected_file_sha256="e" * 64
    )
    assert result["status"] == EVIDENCE_INVALID
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_record_order_does_not_change_contract_hash():
    records = list(provider(fund_responses()).collect_fund_flow_batch().records)
    first = validate_market_source_records("fund_flow", records)
    second = validate_market_source_records("fund_flow", list(reversed(records)))
    assert first["records_hash"] == second["records_hash"]
