from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED,
    SourceProviderEnvelope,
    audit_source_adapters,
    execute_source_adapter,
    get_source_adapter_registry,
)
from overnight_quant.data import source_capability_adapters as adapters
from overnight_quant.data.source_capability_registry import (
    validate_source_provenance_batch,
)
from overnight_quant.data.static_source_providers import (
    PROVIDER_KEYS,
    StaticHttpResponse,
    StaticSourceContractError,
    StaticSourceProviders,
    validate_static_provider_records,
)
from overnight_quant.scripts.run_static_source_evidence_verify import (
    EVIDENCE_INVALID,
    EVIDENCE_VERIFIED,
    verify_static_source_evidence,
)
from overnight_quant.scripts.run_static_source_validation import (
    compute_evidence_hash,
    run_static_source_validation,
)
from overnight_quant.scripts import run_static_source_validation as validation


CODES = ("000001", "000333", "600000", "600519", "601318")
TRADE_DATE = "2026-09-17"
CUTOFF = "2026-09-17T14:50:00+08:00"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.request_count = 0

    def request(self, method, url, *, params, data, headers, timeout_seconds):
        self.request_count += 1
        if not self.responses:
            raise RuntimeError("unexpected_request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Clock:
    def __init__(self, count=30):
        base = datetime(2026, 9, 17, 14, 49, tzinfo=CN_TZ)
        self.values = [base + timedelta(milliseconds=index) for index in range(count)]

    def __call__(self):
        return self.values.pop(0)


def response(payload, url="https://web.ifzq.gtimg.cn/test"):
    return StaticHttpResponse(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"), 200, url
    )


def weekdays(count=70):
    current = date(2026, 9, 16)
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current -= timedelta(days=1)
    return sorted(result)


def kline_rows(days, *, duplicate=False, invalid=False, include_target=False):
    rows = [[day, "10", "10.2", "10.5", "9.8", "1000"] for day in days]
    if duplicate:
        rows.append(list(rows[-1]))
    if invalid:
        rows[-1][3] = "9"
    if include_target:
        rows.append([TRADE_DATE, "10", "10", "10", "10", "100"])
    return rows


def provider(responses):
    return StaticSourceProviders(
        CODES,
        target_trade_date=TRADE_DATE,
        feature_cutoff=CUTOFF,
        transport=FakeTransport(responses),
        clock=Clock(),
    )


def test_static_candidates_are_registered_but_never_called_in_production():
    audit = audit_source_adapters(environ={})
    assert audit["bound_count"] == 4
    assert audit["candidate_count"] == 5
    called = 0

    def candidate():
        nonlocal called
        called += 1
        return []

    result = execute_source_adapter(
        "trading_calendar",
        origin_source="tencent",
        adapter="direct_http",
        source_version="ifzq_fqkline_day_v2026-07-30",
        provider_envelope=SourceProviderEnvelope(
            PROVIDER_KEYS["trading_calendar"], candidate
        ),
        environ={},
    )
    assert result["status"] == SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
    assert result["provider_called"] is False
    assert called == 0
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_static_candidate_matrix_is_fixed_and_cannot_be_promoted_or_rekeyed():
    rows = get_source_adapter_registry()
    candidates = {
        row["capability"]: row["candidate_provider_key"]
        for row in rows
        if row["implementation_status"] == "candidate_not_activated"
    }
    assert candidates == PROVIDER_KEYS

    promoted = deepcopy(rows)
    row = next(item for item in promoted if item["capability"] == "trading_calendar")
    row["provider_key"] = row["candidate_provider_key"]
    row["candidate_provider_key"] = ""
    row["implementation_status"] = "bound"
    with pytest.raises(ValueError):
        adapters._validate_production_source_adapter_bindings(promoted)

    rekeyed = deepcopy(rows)
    row = next(item for item in rekeyed if item["capability"] == "announcement" and item["adapter"] == "direct_http")
    row["candidate_provider_key"] = "tests.rekeyed_announcement_provider"
    with pytest.raises(ValueError):
        adapters._compute_source_adapter_registry_hash_for_test(rekeyed)


def test_calendar_rejects_weekend_duplicate_and_below_60():
    days = weekdays()
    calendar = provider([response({"data": {"sh000001": {"day": kline_rows(days)}}})])
    batch = calendar.collect_trading_calendar_batch()
    assert len(batch.records[0]["payload"]["trade_dates"]) == 70

    bad = list(days)
    bad[-1] = "2026-09-13"
    with pytest.raises(StaticSourceContractError, match="NON_TRADING_DATE"):
        provider([response({"data": {"sh000001": {"day": kline_rows(bad)}}})]).collect_trading_calendar_batch()
    with pytest.raises(StaticSourceContractError, match="DUPLICATE_DATE"):
        provider([response({"data": {"sh000001": {"day": kline_rows(days, duplicate=True)}}})]).collect_trading_calendar_batch()
    with pytest.raises(StaticSourceContractError, match="BELOW_60"):
        provider([response({"data": {"sh000001": {"day": kline_rows(days[:59])}}})]).collect_trading_calendar_batch()


@pytest.mark.parametrize("case", ["below", "duplicate", "invalid", "unfinished", "unknown"])
def test_qfq_daily_contract_rejects_invalid_history(case):
    days = weekdays()
    selected = days[-60:]
    responses = []
    for code in CODES:
        symbol = ("sh" if code.startswith("6") else "sz") + code
        rows = kline_rows(selected)
        if code == CODES[0]:
            if case == "below":
                rows = rows[:59]
            elif case == "duplicate":
                rows.append(list(rows[-1]))
            elif case == "invalid":
                rows[-1][3] = "9"
            elif case == "unfinished":
                rows.append([TRADE_DATE, "10", "10", "10", "10", "100"])
            elif case == "unknown":
                rows[-1][0] = "2026-01-01"
        responses.append(response({"data": {symbol: {"qfqday": rows}}}))
    instance = provider(responses)
    if case == "unfinished":
        batch = instance.collect_qfq_daily_batch(days)
        assert all(row["payload"]["date"] < TRADE_DATE for row in batch.records)
    else:
        with pytest.raises(StaticSourceContractError):
            instance.collect_qfq_daily_batch(days)


def test_qfq_requires_response_proof_and_valid_ohlcv():
    days = weekdays()
    symbol = "sz000001"
    with pytest.raises(StaticSourceContractError, match="QFQ_NOT_PROVEN"):
        provider([response({"data": {symbol: {"day": kline_rows(days[-60:])}}})]).collect_qfq_daily_batch(days)


def test_news_requires_published_at_and_allows_successful_zero_rows():
    missing = {"data": {"fastNewsList": [{"title": "missing time"}]}}
    with pytest.raises(StaticSourceContractError, match="PUBLISHED_AT_MISSING"):
        provider([response(missing, "https://np-weblist.eastmoney.com/test")]).collect_global_news_batch()
    empty = provider([response({"data": {"fastNewsList": []}}, "https://np-weblist.eastmoney.com/test")]).collect_global_news_batch()
    assert empty.status == "AVAILABLE_EMPTY"
    assert empty.records == ()


def test_network_failure_is_not_available_empty():
    instance = provider([StaticSourceContractError("STATIC_SOURCE_REQUEST_FAILED")])
    with pytest.raises(StaticSourceContractError, match="REQUEST_FAILED"):
        instance.collect_global_news_batch()


def test_late_announcement_is_excluded_and_identity_is_cninfo():
    before = int(datetime(2026, 9, 17, 14, 40, tzinfo=CN_TZ).timestamp() * 1000)
    after = int(datetime(2026, 9, 17, 14, 51, tzinfo=CN_TZ).timestamp() * 1000)
    rows = [
        {"announcementTitle": "before", "announcementTime": before, "announcementId": "1", "adjunctUrl": "/a.pdf"},
        {"announcementTitle": "after", "announcementTime": after, "announcementId": "2", "adjunctUrl": "/b.pdf"},
    ]
    replies = [response({"announcements": rows}, "https://www.cninfo.com.cn/test")]
    replies.extend(
        response({"announcements": []}, "https://www.cninfo.com.cn/test")
        for _ in range(4)
    )
    batch = provider(replies).collect_announcement_batch()
    assert all(row["published_at"] <= CUTOFF for row in batch.records)
    assert all(row["origin_source"] == "cninfo" for row in batch.records)


def test_provenance_rejects_source_version_and_hash_tampering():
    days = weekdays()
    batch = provider([response({"data": {"sh000001": {"day": kline_rows(days)}}})]).collect_trading_calendar_batch()
    row = deepcopy(batch.records[0])
    assert validate_source_provenance_batch("trading_calendar", [row], environ={})["status"] == "SOURCE_PROVENANCE_ACCEPTED"
    row["source_version"] = "tampered"
    assert validate_source_provenance_batch("trading_calendar", [row], environ={})["status"] == "SOURCE_VERSION_REJECTED"
    row = deepcopy(batch.records[0])
    row["raw_hash"] = "x"
    assert validate_source_provenance_batch("trading_calendar", [row], environ={})["status"] == "PROVENANCE_HASH_INVALID"


def test_record_order_does_not_change_deterministic_hash():
    days = weekdays()
    responses = []
    for code in CODES:
        symbol = ("sh" if code.startswith("6") else "sz") + code
        responses.append(response({"data": {symbol: {"qfqday": kline_rows(days[-60:])}}}))
    records = list(provider(responses).collect_qfq_daily_batch(days).records)
    forward = validate_static_provider_records(
        "daily_bar_qfq", records, target_trade_date=TRADE_DATE,
        codes=CODES, calendar_trade_dates=days,
    )
    reverse = validate_static_provider_records(
        "daily_bar_qfq", list(reversed(records)), target_trade_date=TRADE_DATE,
        codes=CODES, calendar_trade_dates=days,
    )
    assert forward["records_hash"] == reverse["records_hash"]


def test_offline_audit_makes_zero_network_requests():
    result = run_static_source_validation(
        network=False, trade_date=TRADE_DATE
    )
    assert result["network_requests_made"] == 0
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_resigned_record_tampering_is_rejected_by_raw_replay():
    evidence = {
        "evidence_schema_version": "static_source_evidence_v1",
        "status": "STATIC_SOURCE_NETWORK_VALIDATION_FAILED",
        "execution_ok": True,
        "network_mode": True,
        "trade_date": TRADE_DATE,
        "feature_cutoff": CUTOFF,
        "requested_codes": list(CODES),
        "provider_keys": dict(PROVIDER_KEYS),
        "producer_commit_sha": "a" * 40,
        "provider_verifier_contract_hash": "invalid",
        "capability_registry_hash": "invalid",
        "capability_results": {},
        "records_by_capability": {},
        "raw_responses": {},
        "network_requests_made": 0,
        "upstream_network_activity": "measured",
        "provider_validation_passed": False,
        "evidence_integrity_verified": False,
        "automatic_configuration_change": False,
        "data_ready": False,
        "hard_gate_authorized": False,
        "candidates": [], "tickets": [], "orders": [],
        "evidence_hash": "",
    }
    evidence["evidence_hash"] = compute_evidence_hash(evidence)
    assert verify_static_source_evidence(evidence, expected_file_sha256="b" * 64)["status"] == EVIDENCE_INVALID


def test_full_fake_evidence_replays_and_resigned_content_tampering_fails(
    tmp_path, monkeypatch
):
    days = weekdays()
    replies = [response({"data": {"sh000001": {"day": kline_rows(days)}}})]
    for code in CODES:
        symbol = ("sh" if code.startswith("6") else "sz") + code
        replies.append(response({"data": {symbol: {"qfqday": kline_rows(days[-60:])}}}))
    published = "2026-09-16T10:00:00+08:00"
    replies.extend(
        response({"result": {"cmsArticleWebOld": {"list": [
            {"title": f"news-{code}", "showTime": published, "url": "https://finance.eastmoney.com/a.html"}
        ]}}}, "https://search-api-web.eastmoney.com/test")
        for code in CODES
    )
    replies.append(response({"data": {"fastNewsList": []}}, "https://np-weblist.eastmoney.com/test"))
    replies.extend(
        response({"announcements": [
            {"announcementTitle": f"announcement-{code}", "announcementTime": int(datetime(2026, 9, 16, 10, 0, tzinfo=CN_TZ).timestamp() * 1000), "announcementId": code, "adjunctUrl": "/a.pdf"}
        ]}, "https://www.cninfo.com.cn/test")
        for code in CODES
    )
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    evidence = run_static_source_validation(
        network=True,
        trade_date=TRADE_DATE,
        output="evidence.json",
        transport=FakeTransport(replies),
        clock=Clock(count=50),
    )
    path = tmp_path / "evidence.json"
    anchor = hashlib.sha256(path.read_bytes()).hexdigest()
    assert evidence["provider_validation_passed"] is True
    verified = verify_static_source_evidence(
        evidence, expected_file_sha256=anchor
    )
    assert verified["status"] == EVIDENCE_VERIFIED

    tampered = deepcopy(evidence)
    tampered["records_by_capability"]["stock_news"][0]["payload"]["title"] = "tampered"
    tampered["evidence_hash"] = compute_evidence_hash(tampered)
    rejected = verify_static_source_evidence(
        tampered, expected_file_sha256="c" * 64
    )
    assert rejected["status"] == EVIDENCE_INVALID
    assert any("records_replay_mismatch" in item for item in rejected["errors"])
