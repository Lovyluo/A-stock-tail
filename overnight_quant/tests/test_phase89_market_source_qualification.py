from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.market_source_providers import (
    EASTMONEY_CLIST_URL,
    EASTMONEY_FUND_FLOW_URL,
    EASTMONEY_STOCK_URL,
    EASTMONEY_ULIST_URL,
    FIXED_CODES,
    INDUSTRY_CLASSIFICATION_VERSION,
    MARKET_STOCK_POOL_VERSION,
    PROVIDER_KEYS,
    SOURCE_IDENTITIES,
    EastmoneyMarketSourceProviders,
    MarketHttpResponse,
    MarketSourceContractError,
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

    def request(self, method, url, *, params, headers, timeout_seconds):
        self.request_count += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


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
    )


def market_responses(*, incomplete=False, invalid_total=False):
    rows = [
        {"f12": "000001", "f14": "上证指数", "f3": 1.2, "f104": 2, "f105": 1, "f106": 0, "f124": EVENT_TS_PLUS_SECONDS},
        {"f12": "399001", "f14": "深证成指", "f3": 0.8, "f104": 3, "f105": 1, "f106": 1, "f124": EVENT_TS},
        {"f12": "899050", "f14": "北证50", "f3": 0.5, "f104": 1, "f105": 1, "f106": 0, "f124": EVENT_TS},
    ]
    if incomplete:
        rows = rows[:-1]
    if invalid_total:
        rows[0]["f104"] = "-"
    return [
        response({"data": {"diff": rows}}, EASTMONEY_ULIST_URL),
        response(
            {"data": {"f3": 0.42, "f57": "000001", "f58": "上证指数", "f86": EVENT_TS}},
            EASTMONEY_STOCK_URL,
        ),
    ]


def industry_responses(*, missing_mapping=False, mapping_after_cutoff=False, board_after_cutoff=False):
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
            "f86": EVENT_TS_PLUS_SECONDS if mapping_after_cutoff else EVENT_TS,
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
    assert batch.records[0]["event_time"] == CUTOFF
    assert batch.records[0]["origin_source"] == "eastmoney"
    assert validate_market_source_records("market_breadth", batch.records)["valid"]


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
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())

    def runner(task, deadline_ms, worker_command):
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

    output = tmp_path / "complete.json"
    result = run_market_source_validation(
        network=True,
        trade_date=TRADE_DATE,
        output=output,
        worker_runner=runner,
    )
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
        "evidence_schema_version": "market_source_evidence_v1",
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


def test_record_order_does_not_change_contract_hash():
    records = list(provider(fund_responses()).collect_fund_flow_batch().records)
    first = validate_market_source_records("fund_flow", records)
    second = validate_market_source_records("fund_flow", list(reversed(records)))
    assert first["records_hash"] == second["records_hash"]
