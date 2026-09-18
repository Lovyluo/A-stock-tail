from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from overnight_quant.data.exchange_announcement_providers import (
    BSE_ANNOUNCEMENT_URL,
    BSE_LANDING_URL,
    EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION,
    EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_V1,
    PROVIDER_KEYS,
    SOURCE_IDENTITIES,
    SSE_ANNOUNCEMENT_URL,
    SZSE_ANNOUNCEMENT_URL,
    ExchangeAnnouncementContractError,
    ExchangeAnnouncementProviders,
    ExchangeHttpResponse,
    compute_exchange_announcement_verifier_contract_hash,
    replay_exchange_announcement_responses,
    validate_exchange_announcement_records,
)
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED,
    SourceProviderEnvelope,
    audit_source_adapters,
    execute_source_adapter,
)
from overnight_quant.data.source_capability_registry import (
    compute_source_capability_registry_hash,
    get_source_capability_registry,
)
from overnight_quant.scripts import run_exchange_announcement_evidence_verify as verifier
from overnight_quant.scripts import run_exchange_announcement_validation as validation
from overnight_quant.scripts import run_exchange_announcement_reanalysis as reanalysis


CUTOFF = "2026-09-18T14:50:00+08:00"


class Clock:
    def __init__(self, count: int = 40, *, hour: int = 14, minute: int = 40) -> None:
        base = datetime(2026, 9, 18, hour, minute, tzinfo=CN_TZ)
        self.values = [
            base + timedelta(milliseconds=index) for index in range(count)
        ]

    def __call__(self) -> datetime:
        return self.values.pop(0)


class FakeTransport:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.request_count = 0
        self.requests = []

    def request(
        self,
        method,
        url,
        *,
        params,
        data,
        json_body,
        headers,
        timeout_seconds,
    ):
        self.request_count += 1
        self.requests.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "data": data,
                "json_body": json_body,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def response(payload, url, *, content_type="application/json"):
    raw = payload if isinstance(payload, bytes) else json.dumps(
        payload, ensure_ascii=False
    ).encode("utf-8")
    return ExchangeHttpResponse(raw, 200, url, {"Content-Type": content_type})


def sse_payload(code="600000", *, published="2026-09-18"):
    return {
        "result": [
            {
                "SECURITY_CODE": code,
                "ORG_BULLETIN_ID": f"SSE-{code}-1",
                "TITLE": "Official SSE announcement",
                "SSEDATE": published,
                "URL": f"/disclosure/listedinfo/announcement/c/new/{code}.pdf",
            }
        ]
    }


def szse_payload(code="000001", *, published="2026-09-18 10:00:00"):
    return {
        "data": [
            {
                "secCode": [code],
                "id": f"SZSE-{code}-1",
                "title": "Official SZSE announcement",
                "publishTime": published,
                "attachPath": f"/disc/disk03/finalpage/{code}.PDF",
            }
        ]
    }


def bse_payload(code="920925", *, published="2026-09-18"):
    value = [
        {
            "listInfo": {
                "content": [
                    {
                        "companyCd": code,
                        "disclosureCode": f"BSE-{code}-1",
                        "disclosureTitle": "Official BSE announcement",
                        "publishDate": published,
                        "destFilePath": f"/disclosure/{code}.PDF",
                    }
                ]
            }
        }
    ]
    return f"null({json.dumps(value, ensure_ascii=False)})".encode("utf-8")


def provider(source, codes, payload, *, clock=None):
    responses = []
    if source == "bse":
        responses.append(
            response(
                b"<!doctype html><html><body>BSE</body></html>",
                BSE_LANDING_URL,
                content_type="text/html; charset=utf-8",
            )
        )
        responses.append(response(payload, BSE_ANNOUNCEMENT_URL))
    else:
        url = SSE_ANNOUNCEMENT_URL if source == "sse" else SZSE_ANNOUNCEMENT_URL
        responses.extend(response(item, url) for item in payload)
    transport = FakeTransport(responses)
    instance = ExchangeAnnouncementProviders(
        codes,
        feature_cutoff=CUTOFF,
        transport=transport,
        clock=clock or Clock(),
    )
    return instance, transport


def test_registry_keeps_three_exchange_sources_candidate_and_cninfo_unqualified():
    rows = get_source_capability_registry()
    announcements = [
        row for row in rows
        if row["capability"] == "announcement"
        and row["adapter"] == "direct_http"
        and row["origin_source"] in {"cninfo", "sse", "szse", "bse"}
    ]
    assert {row["origin_source"] for row in announcements} == {
        "cninfo", "sse", "szse", "bse"
    }
    assert all(row["qualification_status"] == "unqualified" for row in announcements)
    audit = audit_source_adapters(environ={})
    assert audit["bound_count"] == 8
    assert audit["candidate_count"] == 7
    assert audit["network_requests_made"] == 0
    assert audit["data_ready"] is False
    assert audit["candidates"] == audit["tickets"] == audit["orders"] == []


@pytest.mark.parametrize(
    ("source", "codes", "payload", "expected_count"),
    [
        ("sse", ("600000", "600519"), [sse_payload("600000"), sse_payload("600519")], 2),
        ("szse", ("000001", "000333"), [szse_payload("000001"), szse_payload("000333")], 2),
        ("bse", ("920925",), bse_payload(), 1),
    ],
)
def test_official_sources_emit_complete_single_origin_records(
    source, codes, payload, expected_count
):
    if source == "sse":
        payload = [sse_payload(code, published="2026-09-17") for code in codes]
    elif source == "bse":
        payload = bse_payload(published="2026-09-17")
    instance, transport = provider(source, codes, payload)
    batch = getattr(instance, f"collect_{source}_batch")()
    assert len(batch.records) == expected_count
    assert {row["origin_source"] for row in batch.records} == {source}
    assert all(row["capability"] == "announcement" for row in batch.records)
    contract = validate_exchange_announcement_records(
        source, batch.records, feature_cutoff=CUTOFF, codes=codes
    )
    assert contract["valid"] is True
    assert transport.request_count == expected_count + (1 if source == "bse" else 0)
    if source == "bse":
        assert ("needFields[]", "disclosureCode") in transport.requests[-1]["data"]
        assert "Cookie" not in transport.requests[-1]["headers"]


@pytest.mark.parametrize(
    ("source", "code"),
    [("sse", "000001"), ("szse", "600000"), ("bse", "600000")],
)
def test_exchange_route_mismatch_fails_before_network(source, code):
    instance, transport = provider(source, (code,), [] if source != "bse" else b"")
    with pytest.raises(ExchangeAnnouncementContractError, match="ROUTE_MISMATCH"):
        getattr(instance, f"collect_{source}_batch")()
    assert transport.request_count == 0


def test_cross_exchange_response_and_missing_time_fail_closed():
    mismatch, _ = provider("sse", ("600000",), [sse_payload("600519")])
    with pytest.raises(ExchangeAnnouncementContractError, match="CODE_MISMATCH"):
        mismatch.collect_sse_batch()

    missing = sse_payload()
    del missing["result"][0]["SSEDATE"]
    invalid, _ = provider("sse", ("600000",), [missing])
    with pytest.raises(ExchangeAnnouncementContractError, match="TIME_INVALID"):
        invalid.collect_sse_batch()


def test_after_cutoff_is_filtered_and_zero_result_is_legal():
    instance, _ = provider(
        "szse",
        ("000001",),
        [szse_payload(published="2026-09-18 15:00:00")],
    )
    batch = instance.collect_szse_batch()
    assert batch.status == "AVAILABLE_EMPTY"
    assert batch.records == ()
    assert len(batch.audit_records) == 1
    assert batch.audit_records[0]["audit_reason"] == (
        "publication_at_or_after_cutoff"
    )
    contract = validate_exchange_announcement_records(
        "szse", [], feature_cutoff=CUTOFF, codes=("000001",)
    )
    assert contract["valid"] is True


def test_nonofficial_document_url_and_html_api_fail_closed():
    payload = sse_payload()
    payload["result"][0]["URL"] = "https://example.com/fake.pdf"
    invalid, _ = provider("sse", ("600000",), [payload])
    with pytest.raises(ExchangeAnnouncementContractError, match="DOCUMENT_URL_INVALID"):
        invalid.collect_sse_batch()

    html = ExchangeAnnouncementProviders(
        ("600000",),
        feature_cutoff=CUTOFF,
        transport=FakeTransport(
            [response(b"<html>blocked</html>", SSE_ANNOUNCEMENT_URL, content_type="text/html")]
        ),
        clock=Clock(),
    )
    with pytest.raises(ExchangeAnnouncementContractError, match="HTML_RESPONSE_REJECTED"):
        html.collect_sse_batch()


def test_input_order_does_not_change_records_or_hashes():
    forward, _ = provider(
        "sse", ("600000", "600519"), [
            sse_payload("600000", published="2026-09-17"),
            sse_payload("600519", published="2026-09-17"),
        ]
    )
    reverse, _ = provider(
        "sse", ("600519", "600000"), [
            sse_payload("600000", published="2026-09-17"),
            sse_payload("600519", published="2026-09-17"),
        ]
    )
    assert forward.collect_sse_records() == reverse.collect_sse_records()


def test_candidate_provider_is_never_called_from_production_adapter():
    called = 0

    def candidate_provider():
        nonlocal called
        called += 1
        return []

    source_version = SOURCE_IDENTITIES["sse"][2]
    result = execute_source_adapter(
        "announcement",
        origin_source="sse",
        adapter="direct_http",
        source_version=source_version,
        provider_envelope=SourceProviderEnvelope(
            PROVIDER_KEYS["sse"], candidate_provider
        ),
        environ={},
    )
    assert result["status"] == SOURCE_ADAPTER_CANDIDATE_NOT_ACTIVATED
    assert result["provider_called"] is False
    assert called == 0
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_offline_validation_makes_no_network_request():
    result = validation.run_exchange_announcement_validation(
        network=False,
        source="sse",
        trade_date="2026-09-18",
    )
    assert result["network_requests_made"] == 0
    assert result["data_ready"] is False
    assert result["candidates"] == result["tickets"] == result["orders"] == []


def test_evidence_replay_detects_raw_tampering_and_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(verifier, "CACHE_ROOT", tmp_path)
    transport = FakeTransport([response(sse_payload(), SSE_ANNOUNCEMENT_URL)])
    evidence = validation.run_exchange_announcement_validation(
        network=True,
        source="sse",
        trade_date="2026-09-18",
        codes=("600000",),
        output="sse.json",
        transport=transport,
        clock=Clock(),
    )
    raw = (tmp_path / "sse.json").read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    first = verifier.verify_file(
        tmp_path / "sse.json", expected_file_sha256=file_hash
    )
    second = verifier.verify_file(
        tmp_path / "sse.json", expected_file_sha256=file_hash
    )
    assert first == second
    assert first["status"] == verifier.EVIDENCE_VERIFIED
    assert evidence["evidence_schema_version"] == (
        EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION
    )

    tampered = deepcopy(evidence)
    tampered["raw_responses"][0]["raw_hash"] = "f" * 64
    tampered["evidence_hash"] = validation.compute_evidence_hash(tampered)
    rejected = verifier.verify_exchange_announcement_evidence(
        tampered, expected_file_sha256="a" * 64
    )
    assert rejected["status"] == verifier.EVIDENCE_INVALID


def test_atomic_evidence_write_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path)
    target = validation.write_json_atomic("evidence.json", {"value": 1})
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        validation.write_json_atomic("evidence.json", {"value": 2})
    assert target.read_bytes() == before


def test_replay_rejects_wrong_origin_and_preserves_safe_output():
    instance, _ = provider("sse", ("600000",), [sse_payload()])
    batch = instance.collect_sse_batch()
    response_row = dict(batch.responses[0])
    response_row["response_url"] = "https://www.szse.cn/api/fake"
    records, errors = replay_exchange_announcement_responses(
        "sse", [response_row], feature_cutoff=CUTOFF, codes=("600000",)
    )
    assert records == []
    assert "0:response_contract_invalid" in errors


def _source_payload(source, published):
    if source == "sse":
        return [sse_payload(published=published)]
    if source == "szse":
        return [szse_payload(published=published)]
    return bse_payload(published=published)


def _date_only_value(source, day):
    return f"{day} 00:00:00" if source == "szse" else day


@pytest.mark.parametrize("source,code", [("sse", "600000"), ("szse", "000001"), ("bse", "920925")])
def test_same_day_date_only_is_audit_only_for_every_exchange(source, code):
    instance, _ = provider(
        source,
        (code,),
        _source_payload(source, _date_only_value(source, "2026-09-18")),
    )
    batch = getattr(instance, f"collect_{source}_batch")()
    assert batch.records == ()
    assert len(batch.audit_records) == 1
    row = batch.audit_records[0]
    assert row["published_at_precision"] == "date"
    assert row["audit_reason"] == "publication_time_precision_insufficient"
    assert row["formal_eligible"] is False


@pytest.mark.parametrize("source,code", [("sse", "600000"), ("szse", "000001"), ("bse", "920925")])
def test_previous_day_date_only_remains_formally_eligible(source, code):
    instance, _ = provider(
        source,
        (code,),
        _source_payload(source, _date_only_value(source, "2026-09-17")),
    )
    batch = getattr(instance, f"collect_{source}_batch")()
    assert len(batch.records) == 1
    assert batch.audit_records == ()
    assert batch.records[0]["published_at_precision"] == "date"


@pytest.mark.parametrize("source,code", [("sse", "600000"), ("szse", "000001"), ("bse", "920925")])
def test_same_day_precise_time_before_cutoff_is_formal(source, code):
    instance, _ = provider(
        source,
        (code,),
        _source_payload(source, "2026-09-18 14:49:59"),
        clock=Clock(hour=15, minute=0),
    )
    batch = getattr(instance, f"collect_{source}_batch")()
    assert len(batch.records) == 1
    assert batch.audit_records == ()
    assert batch.records[0]["published_at_precision"] == "datetime"


@pytest.mark.parametrize("source,code", [("sse", "600000"), ("szse", "000001"), ("bse", "920925")])
@pytest.mark.parametrize("clock_text", ["14:50:00", "14:50:01"])
def test_same_day_precise_time_at_or_after_cutoff_is_rejected(
    source, code, clock_text
):
    instance, _ = provider(
        source,
        (code,),
        _source_payload(source, f"2026-09-18 {clock_text}"),
        clock=Clock(hour=15, minute=0),
    )
    batch = getattr(instance, f"collect_{source}_batch")()
    assert batch.records == ()
    assert len(batch.audit_records) == 1
    assert batch.audit_records[0]["audit_reason"] == (
        "publication_at_or_after_cutoff"
    )


@pytest.mark.parametrize("source,code", [("sse", "600000"), ("szse", "000001"), ("bse", "920925")])
def test_date_only_value_is_never_upgraded_to_datetime(source, code):
    instance, _ = provider(
        source,
        (code,),
        _source_payload(source, _date_only_value(source, "2026-09-17")),
    )
    row = getattr(instance, f"collect_{source}_batch")().records[0]
    assert row["published_at_precision"] == "date"
    assert row["payload"]["published_at_precision"] == "date"


@pytest.mark.parametrize("source,code", [("sse", "600000"), ("szse", "000001"), ("bse", "920925")])
def test_zero_announcement_response_is_legal_for_every_exchange(source, code):
    if source == "sse":
        payload = [{"result": []}]
    elif source == "szse":
        payload = [{"data": []}]
    else:
        payload = b"null([{\"listInfo\":{\"content\":[]}}])"
    instance, _ = provider(source, (code,), payload)
    batch = getattr(instance, f"collect_{source}_batch")()
    assert batch.status == "AVAILABLE_EMPTY"
    assert batch.records == batch.audit_records == ()


def test_v1_evidence_reanalysis_moves_same_day_date_to_audit(
    tmp_path, monkeypatch
):
    instance, _ = provider("sse", ("600000",), [sse_payload()])
    batch = instance.collect_sse_batch()
    v1_records, replay_errors = replay_exchange_announcement_responses(
        "sse",
        batch.responses,
        feature_cutoff=CUTOFF,
        codes=("600000",),
        contract_version="v1",
    )
    assert replay_errors == []
    assert len(v1_records) == 1
    raw_responses = [validation._serialize_response(item) for item in batch.responses]
    v1 = validation._safe(
        {
            "evidence_schema_version": EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_V1,
            "status": "EXCHANGE_ANNOUNCEMENT_NETWORK_VALIDATED",
            "execution_ok": True,
            "network_mode": True,
            "source": "sse",
            "origin_source": "sse",
            "source_version": SOURCE_IDENTITIES["sse"][2],
            "provider_key": PROVIDER_KEYS["sse"],
            "trade_date": "2026-09-18",
            "feature_cutoff": CUTOFF,
            "requested_codes": ["600000"],
            "producer_commit_sha": "0" * 40,
            "provider_verifier_contract_hash": (
                compute_exchange_announcement_verifier_contract_hash(
                    EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_V1
                )
            ),
            "capability_registry_hash": compute_source_capability_registry_hash(),
            "capability_result": {
                "status": "EXCHANGE_ANNOUNCEMENT_SOURCE_VALIDATED",
                "record_count": 1,
            },
            "records": v1_records,
            "raw_responses": raw_responses,
            "network_requests_made": 1,
            "upstream_network_activity": "measured",
            "provider_validation_passed": True,
            "evidence_integrity_verified": False,
            "error_code": "",
            "evidence_hash": "",
        }
    )
    v1["evidence_hash"] = validation.compute_evidence_hash(v1)
    monkeypatch.setattr(reanalysis, "CACHE_ROOT", tmp_path)
    source = tmp_path / "v1.json"
    source.write_text(
        json.dumps(v1, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    derived = reanalysis.build_exchange_announcement_reanalysis(
        "v1.json", expected_file_sha256=source_sha
    )
    assert derived["evidence_schema_version"] == (
        EXCHANGE_ANNOUNCEMENT_EVIDENCE_SCHEMA_VERSION
    )
    assert derived["records"] == []
    assert len(derived["audit_records"]) == 1
    assert derived["audit_records"][0]["audit_reason"] == (
        "publication_time_precision_insufficient"
    )
    assert derived["derived_from"]["file_sha256"] == source_sha
    checked = verifier.verify_exchange_announcement_evidence(
        derived, expected_file_sha256="a" * 64
    )
    assert checked["status"] == verifier.EVIDENCE_VERIFIED
    assert checked["data_ready"] is False
    assert checked["candidates"] == checked["tickets"] == checked["orders"] == []
