from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data import static_source_providers as static_providers
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_BOUND,
    SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED,
    SOURCE_ADAPTER_PROVIDER_EMPTY,
    SOURCE_ADAPTER_PROVIDER_FAILED,
    SourceProviderEnvelope,
    audit_source_adapters,
    execute_source_adapter,
    get_source_adapter_registry,
)
from overnight_quant.data import source_capability_adapters as adapters
from overnight_quant.data.source_capability_registry import (
    get_source_capability_registry,
    route_source_capability,
    validate_source_provenance_batch,
)
from overnight_quant.data.static_source_providers import (
    CNINFO_ANNOUNCEMENT_URL,
    PROVIDER_KEYS,
    StaticHttpResponse,
    StaticSourceContractError,
    StaticSourceProviders,
    validate_static_provider_records,
)
from overnight_quant.data.static_source_qualification import (
    S1_APPROVED_PROVIDER_KEYS,
    S1_EVIDENCE_FILE_SHA256,
    S1_EVIDENCE_HASH,
    S1_PARTIAL_QUALIFICATION_RECORD,
    S1_PARTIAL_QUALIFICATION_RECORD_HASH,
    S1_REPLAY_HASH,
    S1_UNQUALIFIED_PROVIDER_KEYS,
    build_s1_partial_qualification_record,
    validate_s1_partial_qualification_record,
)
from overnight_quant.scripts.run_static_source_evidence_verify import (
    EVIDENCE_INVALID,
    EVIDENCE_VERIFIED,
    verify_file,
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


def response(
    payload,
    url="https://web.ifzq.gtimg.cn/test",
    *,
    status_code=200,
    headers=None,
):
    return StaticHttpResponse(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        status_code,
        url,
        headers or {},
    )


def cninfo_html_403():
    return StaticHttpResponse(
        b"<!doctype html><html><body>Forbidden</body></html>",
        403,
        CNINFO_ANNOUNCEMENT_URL,
        {"Content-Type": "text/html; charset=utf-8", "Server": "nginx"},
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


def _adapter_record(capability, origin_source, source_version):
    record = {
        "capability": capability,
        "origin_source": origin_source,
        "adapter": "direct_http",
        "source_version": source_version,
        "event_time": "2026-09-17T14:40:00+08:00",
        "observed_at": "2026-09-17T14:40:01+08:00",
        "available_at": "2026-09-17T14:40:02+08:00",
        "request_hash": "a" * 64,
        "raw_hash": "b" * 64,
        "payload": {"code": "000001"},
    }
    if capability in {"stock_news", "global_news", "announcement"}:
        record["published_at"] = "2026-09-17T14:30:00+08:00"
    return record


def test_s1_partial_qualification_record_is_deterministic_and_per_capability():
    record = build_s1_partial_qualification_record()
    assert record == S1_PARTIAL_QUALIFICATION_RECORD
    assert record["qualification_record_hash"] == (
        S1_PARTIAL_QUALIFICATION_RECORD_HASH
    )
    assert record["evidence"] == {
        "evidence_hash": S1_EVIDENCE_HASH,
        "file_sha256": S1_EVIDENCE_FILE_SHA256,
        "replay_hash": S1_REPLAY_HASH,
        "capability_registry_hash": (
            "e4efac3a9d03dce1bb8e7edd063f82699b788c9cf404e4eba61308fb0d1457bc"
        ),
    }
    results = {row["capability"]: row for row in record["capabilities"]}
    assert {
        key for key, value in results.items()
        if value["qualification_status"] == "qualified"
    } == {"trading_calendar", "daily_bar_qfq", "stock_news", "global_news"}
    assert results["announcement"]["qualification_status"] == "unqualified"
    assert results["announcement"]["implementation_status"] == (
        "candidate_not_activated"
    )
    assert results["announcement"]["selected_source"] is None
    assert validate_s1_partial_qualification_record(record)["record_valid"] is True


@pytest.mark.parametrize("field", ["evidence_hash", "file_sha256", "replay_hash"])
def test_s1_partial_qualification_record_rejects_evidence_tampering(field):
    record = build_s1_partial_qualification_record()
    record["evidence"][field] = "f" * 64
    record["qualification_record_hash"] = stable_hash(
        {key: value for key, value in record.items() if key != "qualification_record_hash"}
    )
    assert validate_s1_partial_qualification_record(record)["record_valid"] is False


def test_s1_partial_qualification_record_rejects_provider_key_tampering():
    record = build_s1_partial_qualification_record()
    approved = next(
        row for row in record["capabilities"]
        if row["capability"] == "trading_calendar"
    )
    approved["provider_key"] = "tests.Tampered.provider"
    record["qualification_record_hash"] = stable_hash(
        {key: value for key, value in record.items() if key != "qualification_record_hash"}
    )
    assert validate_s1_partial_qualification_record(record)["record_valid"] is False


def test_s1_registry_binds_four_approved_sources_and_retains_cninfo_candidate():
    audit = audit_source_adapters(environ={})
    assert audit["bound_count"] == 8
    assert audit["candidate_count"] == 1
    assert audit["s1_partial_qualification_record_hash"] == (
        S1_PARTIAL_QUALIFICATION_RECORD_HASH
    )
    rows = get_source_adapter_registry()
    by_identity = {
        (
            row["capability"], row["origin_source"], row["adapter"],
            row["source_version"],
        ): row
        for row in rows
    }
    for identity, provider_key in S1_APPROVED_PROVIDER_KEYS.items():
        assert by_identity[identity]["implementation_status"] == "bound"
        assert by_identity[identity]["provider_key"] == provider_key
        assert by_identity[identity]["candidate_provider_key"] == ""
    for identity, provider_key in S1_UNQUALIFIED_PROVIDER_KEYS.items():
        assert by_identity[identity]["implementation_status"] == (
            "candidate_not_activated"
        )
        assert by_identity[identity]["provider_key"] == ""
        assert by_identity[identity]["candidate_provider_key"] == provider_key
    assert audit["network_requests_made"] == 0
    assert audit["data_ready"] is False
    assert audit["candidates"] == audit["tickets"] == audit["orders"] == []


def test_s1_binding_matrix_rejects_delete_add_replace_and_duplicate():
    rows = get_source_adapter_registry()
    with pytest.raises(ValueError):
        adapters._validate_production_source_adapter_bindings(rows[:-1])
    with pytest.raises(ValueError):
        adapters._validate_production_source_adapter_bindings(rows + [rows[0]])

    added = deepcopy(rows[0])
    added["origin_source"] = "unknown"
    with pytest.raises(ValueError):
        adapters._validate_production_source_adapter_bindings(rows + [added])

    replaced = deepcopy(rows)
    row = next(
        item for item in replaced
        if item["capability"] == "trading_calendar"
        and item["origin_source"] == "tencent"
    )
    row["provider_key"] = "tests.Replaced.provider"
    with pytest.raises(ValueError):
        adapters._validate_production_source_adapter_bindings(replaced)


@pytest.mark.parametrize("identity", sorted(S1_APPROVED_PROVIDER_KEYS))
def test_approved_s1_route_requires_exact_explicit_envelope(identity):
    capability, origin_source, adapter, source_version = identity
    called = 0

    def approved_provider():
        nonlocal called
        called += 1
        return [_adapter_record(capability, origin_source, source_version)]

    result = execute_source_adapter(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        source_version=source_version,
        provider_envelope=SourceProviderEnvelope(
            S1_APPROVED_PROVIDER_KEYS[identity], approved_provider
        ),
        environ={},
    )
    assert result["status"] == SOURCE_ADAPTER_BOUND
    assert result["provider_called"] is True
    assert called == 1
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_global_news_successful_empty_batch_remains_legal():
    identity = next(
        item for item in S1_APPROVED_PROVIDER_KEYS
        if item[0] == "global_news"
    )
    capability, origin_source, adapter, source_version = identity
    result = execute_source_adapter(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        source_version=source_version,
        provider_envelope=SourceProviderEnvelope(
            S1_APPROVED_PROVIDER_KEYS[identity], lambda: []
        ),
        environ={},
    )
    assert result["status"] == SOURCE_ADAPTER_PROVIDER_EMPTY
    assert result["provider_called"] is True
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_formal_s1_provider_failure_has_no_demo_fallback():
    identity = next(iter(sorted(S1_APPROVED_PROVIDER_KEYS)))
    capability, origin_source, adapter, source_version = identity

    def failed_provider():
        raise RuntimeError("upstream_failed")

    result = execute_source_adapter(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        source_version=source_version,
        provider_envelope=SourceProviderEnvelope(
            S1_APPROVED_PROVIDER_KEYS[identity], failed_provider
        ),
        environ={},
    )
    assert result["status"] == SOURCE_ADAPTER_PROVIDER_FAILED
    assert result["provider_called"] is True
    assert "demo" not in json.dumps(result).lower()
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_cninfo_fake_success_cannot_obtain_production_binding_or_route():
    identity, provider_key = next(iter(S1_UNQUALIFIED_PROVIDER_KEYS.items()))
    capability, origin_source, adapter, source_version = identity
    called = 0

    def forged_provider():
        nonlocal called
        called += 1
        return [_adapter_record(capability, origin_source, source_version)]

    result = execute_source_adapter(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        source_version=source_version,
        provider_envelope=SourceProviderEnvelope(provider_key, forged_provider),
        environ={},
    )
    assert result["status"] == SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
    assert result["provider_called"] is False
    assert result["selected_source"] is None
    assert called == 0

    formal_route = route_source_capability(
        capability,
        origin_source=origin_source,
        adapter=adapter,
        require_hard_gate=True,
        environ={},
    )
    assert formal_route["status"] == "SOURCE_UNQUALIFIED"
    assert formal_route["selected_source"] is None
    capability_row = next(
        row for row in get_source_capability_registry()
        if row["capability"] == capability
        and row["origin_source"] == origin_source
        and row["adapter"] == adapter
    )
    assert capability_row["qualification_status"] == "unqualified"
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


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


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("600000", "gssh0600000"),
        ("000001", "gssz0000001"),
        ("830799", "gsbj0830799"),
    ],
)
def test_cninfo_org_id_uses_official_three_market_contract(code, expected):
    assert static_providers._cninfo_org_id(code) == expected


def test_cninfo_html_and_non_json_responses_fail_closed():
    with pytest.raises(
        StaticSourceContractError,
        match="CNINFO_DIRECT_ACCESS_UNAVAILABLE",
    ) as captured:
        provider([cninfo_html_403()]).collect_announcement_batch()
    assert captured.value.response_evidence["http_status_code"] == 403
    assert captured.value.response_evidence["response_content_type"].startswith(
        "text/html"
    )

    html_200 = StaticHttpResponse(
        b"<html><body>risk control</body></html>",
        200,
        CNINFO_ANNOUNCEMENT_URL,
        {"Content-Type": "text/html"},
    )
    with pytest.raises(StaticSourceContractError, match="STATIC_SOURCE_JSON_INVALID"):
        provider([html_200]).collect_announcement_batch()


def test_cninfo_successful_zero_announcements_is_available_empty():
    replies = [
        response(
            {"announcements": []},
            CNINFO_ANNOUNCEMENT_URL,
            headers={"Content-Type": "application/json"},
        )
        for _ in CODES
    ]
    batch = provider(replies).collect_announcement_batch()
    assert batch.status == "AVAILABLE_EMPTY"
    assert batch.records == ()


def test_cninfo_403_failure_evidence_is_replayable_but_not_validated(
    tmp_path, monkeypatch
):
    days = weekdays()
    replies = [response({"data": {"sh000001": {"day": kline_rows(days)}}})]
    for code in CODES:
        symbol = ("sh" if code.startswith("6") else "sz") + code
        replies.append(
            response({"data": {symbol: {"qfqday": kline_rows(days[-60:])}}})
        )
    published = "2026-09-16T10:00:00+08:00"
    replies.extend(
        response(
            {"result": {"cmsArticleWebOld": {"list": [
                {
                    "title": f"news-{code}",
                    "showTime": published,
                    "url": "https://finance.eastmoney.com/a.html",
                }
            ]}}},
            "https://search-api-web.eastmoney.com/test",
        )
        for code in CODES
    )
    replies.append(
        response(
            {"data": {"fastNewsList": []}},
            "https://np-weblist.eastmoney.com/test",
        )
    )
    replies.append(cninfo_html_403())
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    evidence = run_static_source_validation(
        network=True,
        trade_date=TRADE_DATE,
        output="cninfo_failure.json",
        transport=FakeTransport(replies),
        clock=Clock(count=40),
    )
    path = tmp_path / "cninfo_failure.json"
    anchor = hashlib.sha256(path.read_bytes()).hexdigest()

    announcement = evidence["capability_results"]["announcement"]
    assert announcement["error_code"] == "CNINFO_DIRECT_ACCESS_UNAVAILABLE"
    assert announcement["response_captured"] is True
    assert evidence["provider_validation_passed"] is False
    assert evidence["raw_responses"]["announcement"][0]["http_status_code"] == 403
    assert "cookie" not in json.dumps(evidence, ensure_ascii=False).lower()
    assert evidence["data_ready"] is False
    assert evidence["candidates"] == evidence["tickets"] == evidence["orders"] == []

    verified = verify_file(path, expected_file_sha256=anchor)
    assert verified["status"] == EVIDENCE_VERIFIED
    assert verified["evidence_integrity_verified"] is True
    assert verified["provider_validation_passed"] is False

    tampered = deepcopy(evidence)
    item = tampered["raw_responses"]["announcement"][0]
    item["response_url"] = "https://data.eastmoney.com/announcement"
    tampered["evidence_hash"] = compute_evidence_hash(tampered)
    rejected = verify_static_source_evidence(
        tampered, expected_file_sha256="d" * 64
    )
    assert rejected["status"] == EVIDENCE_INVALID
    assert "announcement:failure_url_mismatch" in rejected["errors"]

    assert (
        verify_file(path, expected_file_sha256="0" * 64)["status"]
        == EVIDENCE_INVALID
    )


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
