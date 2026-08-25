from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import multiprocessing
from pathlib import Path
import socket

import pytest

from overnight_quant.data import source_capability_adapters as adapters
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.point_in_time import stable_hash
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_BOUND,
    SourceAdapterBinding,
    SourceProviderEnvelope,
    audit_source_adapters,
)
from overnight_quant.data.source_capability_registry import (
    validate_source_provenance_batch,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_ADAPTER,
    TENCENT_ORIGIN_SOURCE,
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2,
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3,
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_V4,
    TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION,
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_SOURCE_VERSION,
    TENCENT_VALUATION_PROVIDER_KEY,
    TencentDirectHttpProviders,
    TencentHttpResponse,
    TencentProviderContractError,
    compute_tencent_provider_verifier_contract_hash,
)
from overnight_quant.scripts import run_tencent_provider_validation as validation
from overnight_quant.scripts import (
    run_tencent_provider_evidence_verify as evidence_verify,
)


CODES = ("000001", "000333", "600000", "600519", "601318")
SOURCE_TIME = "20260825143000"
OBSERVED_AT = datetime(2026, 8, 25, 14, 30, 1, tzinfo=CN_TZ)
AVAILABLE_AT = datetime(2026, 8, 25, 14, 30, 2, tzinfo=CN_TZ)


def _concurrent_validation_writer(
    target,
    cache_root,
    payload,
    start_event,
    result_queue,
):
    from overnight_quant.scripts import (
        run_tencent_provider_validation as child_validation,
    )

    child_validation.CACHE_ROOT = Path(cache_root).resolve()
    start_event.wait(10)
    try:
        child_validation.write_validation_json_atomic(target, payload)
        result_queue.put(("written", ""))
    except FileExistsError:
        result_queue.put(("exists", ""))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__))


class FakeTransport:
    def __init__(
        self,
        content: bytes,
        *,
        error: Exception | None = None,
        response_url: str | None = None,
        status_code: int = 200,
    ) -> None:
        self.content = content
        self.error = error
        self.response_url = response_url
        self.status_code = status_code
        self.request_count = 0
        self.calls = []

    def request(self, method, url, *, headers, timeout_seconds):
        self.request_count += 1
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return TencentHttpResponse(
            content=self.content,
            status_code=self.status_code,
            url=self.response_url or url,
        )


class SequencedTransport:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.request_count = 0
        self.calls = []

    def request(self, method, url, *, headers, timeout_seconds):
        self.request_count += 1
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        outcome = self.outcomes[self.request_count - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return TencentHttpResponse(
            content=outcome,
            status_code=200,
            url=url,
        )


def _clock(*values):
    iterator = iter(values)
    return lambda: next(iterator)


def _symbol(code: str) -> str:
    return ("sh" if code.startswith("6") else "sz") + code


def _values(code: str, **changes) -> list[str]:
    values = [""] * 88
    values[0] = "51"
    values[1] = f"股票{code}"
    values[2] = code
    values[3] = "10.50"
    values[4] = "10.00"
    values[5] = "10.10"
    for level in range(1, 6):
        values[9 + (level - 1) * 2] = f"{10.40 - level * 0.01:.2f}"
        values[10 + (level - 1) * 2] = str(1000 + level)
        values[19 + (level - 1) * 2] = f"{10.50 + level * 0.01:.2f}"
        values[20 + (level - 1) * 2] = str(2000 + level)
    values[30] = SOURCE_TIME
    values[32] = "5.00"
    values[33] = "10.80"
    values[34] = "9.90"
    values[36] = "123456"
    values[37] = "98765.43"
    values[38] = "2.50"
    values[39] = "12.30"
    values[43] = "8.88"
    values[44] = "500.10"
    values[45] = "450.20"
    values[46] = "1.23"
    values[47] = "11.00"
    values[48] = "9.00"
    values[52] = "13.40"
    for index, value in changes.items():
        values[int(index)] = value
    return values


def _line(code: str, values: list[str] | None = None) -> str:
    fields = values if values is not None else _values(code)
    return f'v_{_symbol(code)}="{"~".join(fields)}"'


def _response_bytes(
    codes=CODES,
    *,
    rows: list[str] | None = None,
) -> bytes:
    response_rows = rows or [_line(code) for code in codes]
    return (";\n".join(response_rows) + ";\n").encode("gbk")


def _provider(
    *,
    codes=CODES,
    content: bytes | None = None,
    clock_values=(OBSERVED_AT, AVAILABLE_AT),
    transport: FakeTransport | None = None,
):
    active_transport = transport or FakeTransport(
        content if content is not None else _response_bytes()
    )
    return (
        TencentDirectHttpProviders(
            codes,
            transport=active_transport,
            clock=_clock(*clock_values),
        ),
        active_transport,
    )


def _assert_record_contract(record, capability):
    assert record["capability"] == capability
    assert record["origin_source"] == TENCENT_ORIGIN_SOURCE
    assert record["adapter"] == TENCENT_ADAPTER
    assert record["source_version"] == TENCENT_SOURCE_VERSION
    assert record["event_time"] == "2026-08-25T14:30:00+08:00"
    assert record["observed_at"] == "2026-08-25T14:30:01.000000+08:00"
    assert record["available_at"] == "2026-08-25T14:30:02.000000+08:00"
    assert len(record["request_hash"]) == 64
    assert len(record["raw_hash"]) == 64


def _assert_safe(result):
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["automatic_configuration_change"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def _generate_success_evidence(monkeypatch, tmp_path, name="success.json"):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / name
    transport = FakeTransport(_response_bytes())
    result = validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=transport,
        clock=_clock(
            OBSERVED_AT,
            AVAILABLE_AT,
            OBSERVED_AT + timedelta(seconds=3),
            AVAILABLE_AT + timedelta(seconds=3),
        ),
    )
    return output, result, transport


def _resign_evidence(payload):
    material = deepcopy(payload)
    material.pop("evidence_hash", None)
    payload["evidence_hash"] = stable_hash(material)


def _file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_strict_v4(path):
    return evidence_verify.verify_tencent_provider_evidence(
        path,
        expected_file_sha256=_file_sha256(path),
    )


def _rejected_provenance(capability, records, **_kwargs):
    result = validate_source_provenance_batch(
        capability,
        records,
        require_hard_gate=False,
        environ={},
    )
    result["status"] = "PROVENANCE_CONTRACT_INCOMPLETE"
    result["provenance"] = None
    result["missing_fields"] = ["synthetic_contract_field"]
    return result


def _write_json(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _replace_raw_response(payload, capability, raw_response):
    raw_entry = payload["raw_responses"][capability]
    raw_entry["content_base64"] = base64.b64encode(raw_response).decode("ascii")
    raw_entry["byte_count"] = len(raw_response)
    raw_entry["raw_hash"] = hashlib.sha256(raw_response).hexdigest()


def _convert_v3_to_v2(payload):
    converted = deepcopy(payload)
    converted["evidence_schema_version"] = TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2
    converted.pop("capability_attempts")
    converted.pop("evidence_integrity_verified")
    converted.pop("provider_validation_passed")
    converted.pop("producer_commit_sha")
    converted.pop("provider_verifier_contract_hash")
    converted.pop("provenance_contract")
    converted.pop("provenance_results")
    for raw_entry in converted["raw_responses"].values():
        raw_entry.pop("http_status_code")
        raw_entry.pop("response_url")
    _resign_evidence(converted)
    return converted


def _convert_v4_to_v3(payload):
    converted = deepcopy(payload)
    converted["evidence_schema_version"] = TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3
    converted.pop("producer_commit_sha")
    converted.pop("provider_verifier_contract_hash")
    converted.pop("provenance_contract")
    converted.pop("provenance_results")
    _resign_evidence(converted)
    return converted


def _convert_v3_to_strict_v1(payload):
    converted = deepcopy(payload)
    for key in (
        "capability_attempts",
        "evidence_integrity_verified",
        "evidence_schema_version",
        "provider_validation_passed",
        "producer_commit_sha",
        "provider_verifier_contract_hash",
        "provenance_contract",
        "provenance_results",
        "raw_responses",
        "upstream_network_activity",
    ):
        converted.pop(key)
    _resign_evidence(converted)
    return converted


def _candidate_test_binding(capability, provider_key):
    return SourceAdapterBinding(
        capability=capability,
        origin_source=TENCENT_ORIGIN_SOURCE,
        adapter=TENCENT_ADAPTER,
        source_version=TENCENT_SOURCE_VERSION,
        provider_key=provider_key,
        implementation_status="bound",
        legacy_implementation_present=False,
    )


def _execute_candidate_test_only(capability, provider_key, provider_callable):
    return adapters._execute_source_adapter_with_bindings_for_test(
        capability,
        origin_source=TENCENT_ORIGIN_SOURCE,
        adapter=TENCENT_ADAPTER,
        source_version=TENCENT_SOURCE_VERSION,
        provider_envelope=SourceProviderEnvelope(
            provider_key=provider_key,
            provider_callable=provider_callable,
        ),
        entries=[_candidate_test_binding(capability, provider_key)],
        environ={},
    )


def test_quote_provider_emits_five_sorted_native_records_with_units_and_book():
    raw = _response_bytes(codes=reversed(CODES))
    provider, transport = _provider(content=raw)

    records = provider.collect_quote_records()

    assert [row["payload"]["code"] for row in records] == sorted(CODES)
    assert transport.request_count == 1
    assert transport.calls[0]["url"].endswith(
        ",".join(_symbol(code) for code in sorted(CODES))
    )
    assert {row["request_hash"] for row in records} == {
        records[0]["request_hash"]
    }
    assert {row["raw_hash"] for row in records} == {
        hashlib.sha256(raw).hexdigest()
    }
    for record in records:
        _assert_record_contract(record, "quote")
        payload = record["payload"]
        assert payload["price"] == 10.5
        assert payload["prev_close"] == 10.0
        assert payload["open"] == 10.1
        assert payload["high"] == 10.8
        assert payload["low"] == 9.9
        assert payload["change_pct"] == 5.0
        assert payload["volume"] == 123456.0
        assert payload["amount"] == 98765.43
        assert payload["turnover_pct"] == 2.5
        assert payload["limit_up"] == 11.0
        assert payload["limit_down"] == 9.0
        assert len(payload["order_book"]["bids"]) == 5
        assert len(payload["order_book"]["asks"]) == 5
        assert payload["field_units"]["volume"] == "lot"
        assert payload["field_units"]["amount"] == "CNY_10k"
    provenance = validate_source_provenance_batch(
        "quote", records, require_hard_gate=False, environ={}
    )
    assert provenance["status"] == "SOURCE_PROVENANCE_ACCEPTED"
    assert provenance["hard_gate_authorized"] is False


def test_valuation_indices_keep_amplitude_separate_from_pb():
    provider, _transport = _provider()

    records = provider.collect_valuation_records()

    for record in records:
        _assert_record_contract(record, "valuation")
        payload = record["payload"]
        assert payload["pe_ttm"] == 12.3
        assert payload["amplitude_pct"] == 8.88
        assert payload["market_cap"] == 500.1
        assert payload["float_market_cap"] == 450.2
        assert payload["pb"] == 1.23
        assert payload["pe_static"] == 13.4
        assert payload["field_indices"]["amplitude_pct"] == 43
        assert payload["field_indices"]["pb"] == 46
        assert payload["field_units"]["market_cap"] == "CNY_100m"
    provenance = validate_source_provenance_batch(
        "valuation", records, require_hard_gate=False, environ={}
    )
    assert provenance["status"] == "SOURCE_PROVENANCE_ACCEPTED"


def test_empty_valuation_fields_are_null_and_marked_missing_not_zero():
    rows = [
        _line(code, _values(code, **{"39": "", "44": "", "45": "", "46": "", "52": ""}))
        for code in CODES
    ]
    provider, _transport = _provider(content=_response_bytes(rows=rows))

    payload = provider.collect_valuation_records()[0]["payload"]

    for field in ("pe_ttm", "market_cap", "float_market_cap", "pb", "pe_static"):
        assert payload[field] is None
        assert payload["valuation_availability"][field] == "missing"


def test_input_order_does_not_change_request_hash_url_or_records():
    content = _response_bytes(codes=reversed(CODES))
    first, first_transport = _provider(codes=CODES, content=content)
    second, second_transport = _provider(codes=reversed(CODES), content=content)

    first_records = first.collect_quote_records()
    second_records = second.collect_quote_records()

    assert first_records == second_records
    assert first_transport.calls[0]["url"] == second_transport.calls[0]["url"]
    assert first_records[0]["request_hash"] == second_records[0]["request_hash"]


@pytest.mark.parametrize(
    "codes",
    [
        ("000001", "sz000001"),
        ("123456",),
        ("sh000001",),
        ("not-a-code",),
    ],
    ids=["duplicate", "unknown-prefix", "market-mismatch", "invalid"],
)
def test_invalid_or_duplicate_request_codes_fail_before_transport(codes):
    transport = FakeTransport(_response_bytes())

    with pytest.raises(TencentProviderContractError):
        TencentDirectHttpProviders(
            codes,
            transport=transport,
            clock=_clock(OBSERVED_AT, AVAILABLE_AT),
        )

    assert transport.request_count == 0


@pytest.mark.parametrize(
    ("rows", "error_code"),
    [
        ([_line(code) for code in CODES[:-1]], "TENCENT_RESPONSE_COVERAGE_INCOMPLETE"),
        (
            [_line(code) for code in CODES] + [_line(CODES[0])],
            "TENCENT_RESPONSE_DUPLICATE_CODE",
        ),
        (
            [_line(code) for code in CODES] + [_line("300001")],
            "TENCENT_RESPONSE_UNREQUESTED_CODE",
        ),
        (
            [_line(CODES[0], _values(CODES[0])[:52])]
            + [_line(code) for code in CODES[1:]],
            "TENCENT_RESPONSE_FIELD_COUNT_INVALID",
        ),
        (
            [_line(CODES[0], _values(CODES[0]) + ["unexpected"])]
            + [_line(code) for code in CODES[1:]],
            "TENCENT_RESPONSE_FIELD_COUNT_INVALID",
        ),
    ],
    ids=[
        "missing-code",
        "duplicate-code",
        "unrequested-code",
        "short-fields",
        "extra-fields",
    ],
)
def test_response_coverage_and_field_count_are_strict(rows, error_code):
    provider, _transport = _provider(content=_response_bytes(rows=rows))

    with pytest.raises(TencentProviderContractError, match=error_code):
        provider.collect_quote_records()


def test_gbk_decode_failure_is_rejected():
    provider, _transport = _provider(content=b"\x81")

    with pytest.raises(
        TencentProviderContractError,
        match="TENCENT_RESPONSE_GBK_INVALID",
    ):
        provider.collect_quote_records()


def test_missing_source_time_is_rejected():
    rows = [_line(code, _values(code, **{"30": ""})) for code in CODES]
    provider, _transport = _provider(content=_response_bytes(rows=rows))

    with pytest.raises(
        TencentProviderContractError,
        match="TENCENT_SOURCE_TIME_INVALID",
    ):
        provider.collect_quote_records()


def test_local_clock_before_source_time_is_rejected_without_repair():
    early = datetime(2026, 8, 25, 14, 29, 59, tzinfo=CN_TZ)
    provider, _transport = _provider(
        clock_values=(early, AVAILABLE_AT),
    )

    with pytest.raises(
        TencentProviderContractError,
        match="TENCENT_SOURCE_TIME_AFTER_OBSERVED_AT",
    ):
        provider.collect_quote_records()


def test_request_completion_before_start_is_rejected():
    provider, _transport = _provider(
        clock_values=(AVAILABLE_AT, OBSERVED_AT),
    )

    with pytest.raises(
        TencentProviderContractError,
        match="TENCENT_TIME_ORDER_INVALID",
    ):
        provider.collect_quote_records()


def test_timeout_is_stable_and_has_no_fallback():
    transport = FakeTransport(_response_bytes(), error=TimeoutError())
    provider, _transport = _provider(transport=transport)

    with pytest.raises(
        TencentProviderContractError,
        match="TENCENT_REQUEST_TIMEOUT",
    ):
        provider.collect_quote_records()

    assert transport.request_count == 1


def test_non_tencent_response_url_is_rejected_as_source_mixing():
    transport = FakeTransport(
        _response_bytes(),
        response_url="https://push2.eastmoney.com/api/quote",
    )
    provider, _transport = _provider(transport=transport)

    with pytest.raises(
        TencentProviderContractError,
        match="TENCENT_RESPONSE_SOURCE_INVALID",
    ):
        provider.collect_quote_records()


def test_tampered_short_hash_is_rejected_by_b1_provenance():
    provider, _transport = _provider()
    records = provider.collect_quote_records()
    tampered = deepcopy(records)
    tampered[0]["raw_hash"] = "a"

    result = validate_source_provenance_batch(
        "quote",
        tampered,
        require_hard_gate=False,
        environ={},
    )

    assert result["status"] == "PROVENANCE_HASH_INVALID"
    _assert_safe(result)


@pytest.mark.parametrize(
    ("capability", "provider_key", "method_name"),
    [
        ("quote", TENCENT_QUOTE_PROVIDER_KEY, "collect_quote_records"),
        (
            "valuation",
            TENCENT_VALUATION_PROVIDER_KEY,
            "collect_valuation_records",
        ),
    ],
)
def test_candidates_pass_private_test_only_execution_without_production_binding(
    capability,
    provider_key,
    method_name,
):
    provider, _transport = _provider()
    result = _execute_candidate_test_only(
        capability,
        provider_key,
        getattr(provider, method_name),
    )

    assert result["status"] == SOURCE_ADAPTER_BOUND
    assert result["test_only"] is True
    assert result["binding"]["legacy_implementation_present"] is False
    assert result["selection_registry_scope"] == "test_only"
    assert (
        result["selection_adapter_registry_hash"]
        != result["production_adapter_registry_hash"]
    )
    _assert_safe(result)


def test_production_bindings_remain_legacy_and_bound_count_zero():
    audit = audit_source_adapters(environ={})
    production = [
        row
        for row in audit["adapter_matrix"]
        if row["origin_source"] == "tencent"
        and row["capability"] in {"quote", "valuation"}
    ]

    assert audit["bound_count"] == 0
    assert len(production) == 2
    assert all(row["legacy_implementation_present"] is True for row in production)
    assert all(
        row["implementation_status"] == "contract_incompatible"
        for row in production
    )
    _assert_safe(audit)


def test_offline_validation_audit_makes_zero_network_requests(monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError("offline audit must not access the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    result = validation.run_tencent_provider_validation(network=False)

    assert result["status"] == "TENCENT_PROVIDER_NETWORK_NOT_REQUESTED"
    assert result["network_requests_made"] == 0
    _assert_safe(result)


def test_network_validation_failure_writes_only_ignored_cache_and_stays_safe(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / "failed.json"
    transport = FakeTransport(_response_bytes(), error=TimeoutError())
    clock_values = (
        OBSERVED_AT,
        AVAILABLE_AT,
        OBSERVED_AT + timedelta(seconds=3),
        AVAILABLE_AT + timedelta(seconds=3),
    )

    result = validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=transport,
        clock=_clock(*clock_values),
    )

    assert result["status"] == "TENCENT_PROVIDER_NETWORK_VALIDATION_FAILED"
    assert result["evidence_schema_version"] == TENCENT_PROVIDER_EVIDENCE_SCHEMA_V4
    assert result["evidence_integrity_verified"] is False
    assert result["provider_validation_passed"] is False
    assert result["network_requests_made"] == 2
    assert output.exists()
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored["status"] == result["status"]
    assert "records_by_capability" in stored
    assert all(not rows for rows in stored["records_by_capability"].values())
    verified = _verify_strict_v4(output)
    assert verified["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert verified["evidence_integrity_verified"] is True
    assert verified["provider_validation_passed"] is False
    assert verified["verified_capabilities"] == []
    assert verified["covered_codes"] == []
    assert verified["failure_reason_verification"]["quote"] == {
        "failure_reason_verified": False,
        "status": "UNCORROBORATED",
        "error_code": "TENCENT_REQUEST_TIMEOUT",
        "raw_hash": "",
    }
    _assert_safe(result)
    _assert_safe(stored)
    _assert_safe(verified)


def test_network_validation_success_uses_injected_transport_and_writes_utf8(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / "success.json"
    transport = FakeTransport(_response_bytes())
    clock_values = (
        OBSERVED_AT,
        AVAILABLE_AT,
        OBSERVED_AT + timedelta(seconds=3),
        AVAILABLE_AT + timedelta(seconds=3),
    )

    result = validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=transport,
        clock=_clock(*clock_values),
    )

    assert result["status"] == "TENCENT_PROVIDER_NETWORK_VALIDATED"
    assert (
        result["evidence_schema_version"]
        == TENCENT_PROVIDER_EVIDENCE_SCHEMA_VERSION
    )
    assert result["evidence_schema_version"] == TENCENT_PROVIDER_EVIDENCE_SCHEMA_V4
    assert result["evidence_integrity_verified"] is False
    assert result["provider_validation_passed"] is True
    assert result["provider_verifier_contract_hash"] == (
        compute_tencent_provider_verifier_contract_hash()
    )
    assert result["network_requests_made"] == 2
    assert result["upstream_network_activity"] == "measured"
    assert result["capability_results"]["quote"]["record_count"] == 5
    assert result["capability_results"]["valuation"]["record_count"] == 5
    assert all(
        item["provenance_status"] == "SOURCE_PROVENANCE_ACCEPTED"
        for item in result["capability_results"].values()
    )
    raw = output.read_bytes()
    assert not raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf"))
    stored = json.loads(raw.decode("utf-8"))
    assert stored["evidence_hash"] == result["evidence_hash"]
    assert set(stored["raw_responses"]) == {"quote", "valuation"}
    for capability, raw_entry in stored["raw_responses"].items():
        decoded = base64.b64decode(raw_entry["content_base64"], validate=True)
        assert raw_entry["capability"] == capability
        assert raw_entry["byte_count"] == len(decoded)
        assert raw_entry["raw_hash"] == hashlib.sha256(decoded).hexdigest()
        assert type(raw_entry["http_status_code"]) is int
        assert raw_entry["http_status_code"] == 200
        assert raw_entry["response_url"].startswith("https://qt.gtimg.cn/")
    verified = _verify_strict_v4(output)
    assert verified["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert verified["evidence_integrity_verified"] is True
    assert verified["provider_validation_passed"] is True
    assert verified["stored_evidence_hash"] == result["evidence_hash"]
    assert verified["recomputed_evidence_hash"] == result["evidence_hash"]
    _assert_safe(result)
    _assert_safe(stored)
    _assert_safe(verified)


def test_existing_validation_output_rejects_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / "existing.json"
    original = b'{"immutable":true}\n'
    output.write_bytes(original)
    transport = FakeTransport(_response_bytes())

    with pytest.raises(FileExistsError):
        validation.run_tencent_provider_validation(
            network=True,
            output=output,
            transport=transport,
            clock=_clock(OBSERVED_AT, AVAILABLE_AT),
        )

    assert transport.request_count == 0
    assert output.read_bytes() == original


def test_evidence_verifier_replay_is_deterministic_and_preserves_original(
    monkeypatch,
    tmp_path,
):
    evidence, generated, _transport = _generate_success_evidence(
        monkeypatch,
        tmp_path,
    )
    original = evidence.read_bytes()
    original_hash = hashlib.sha256(original).hexdigest()
    replay_a = tmp_path / "replay_a.json"
    replay_b = tmp_path / "replay_b.json"

    expected_sha = _file_sha256(evidence)
    first = evidence_verify.verify_tencent_provider_evidence(
        evidence,
        expected_file_sha256=expected_sha,
    )
    second = evidence_verify.verify_tencent_provider_evidence(
        evidence,
        expected_file_sha256=expected_sha,
    )
    assert first == second
    assert first["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert first["stored_evidence_hash"] == generated["evidence_hash"]
    assert evidence_verify.main(
        [
            str(evidence),
            "--expected-file-sha256",
            expected_sha,
            "--output",
            str(replay_a),
        ]
    ) == 0
    assert evidence_verify.main(
        [
            str(evidence),
            "--expected-file-sha256",
            expected_sha,
            "--output",
            str(replay_b),
        ]
    ) == 0

    assert replay_a.read_bytes() == replay_b.read_bytes()
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == original_hash
    assert evidence.read_bytes() == original
    _assert_safe(first)


@pytest.mark.parametrize(
    "tamper",
    [
        "raw_hash",
        "field_count",
        "coverage",
        "capability",
        "origin_source",
        "safety_output",
        "network_request_count",
        "http_status_code",
        "response_url",
        "missing_http_status",
    ],
)
def test_evidence_verifier_rejects_resigned_contract_tampering(
    monkeypatch,
    tmp_path,
    tamper,
):
    evidence, _generated, _transport = _generate_success_evidence(
        monkeypatch,
        tmp_path,
        name=f"tamper_{tamper}.json",
    )
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    if tamper == "raw_hash":
        payload["raw_responses"]["quote"]["raw_hash"] = "f" * 64
    elif tamper == "field_count":
        rows = [
            _line(code, _values(code)[:-1] if code == CODES[0] else None)
            for code in CODES
        ]
        _replace_raw_response(
            payload,
            "quote",
            _response_bytes(rows=rows),
        )
    elif tamper == "coverage":
        _replace_raw_response(
            payload,
            "quote",
            _response_bytes(codes=CODES[:-1]),
        )
    elif tamper == "capability":
        payload["records_by_capability"]["quote"][0][
            "capability"
        ] = "valuation"
    elif tamper == "origin_source":
        payload["records_by_capability"]["quote"][0][
            "origin_source"
        ] = "eastmoney"
    elif tamper == "safety_output":
        payload["data_ready"] = True
    elif tamper == "network_request_count":
        payload["network_requests_made"] = 0
    elif tamper == "http_status_code":
        payload["raw_responses"]["quote"]["http_status_code"] = 201
    elif tamper == "response_url":
        payload["raw_responses"]["quote"][
            "response_url"
        ] = "https://push2.eastmoney.com/api/quote"
    elif tamper == "missing_http_status":
        payload["raw_responses"]["quote"].pop("http_status_code")
    _resign_evidence(payload)
    _write_json(evidence, payload)

    verified = _verify_strict_v4(evidence)

    assert verified["status"] == evidence_verify.EVIDENCE_INVALID
    assert verified["execution_ok"] is False
    _assert_safe(verified)


def test_legacy_evidence_is_audit_only_and_not_promoted(monkeypatch, tmp_path):
    evidence, _generated, _transport = _generate_success_evidence(
        monkeypatch,
        tmp_path,
        name="legacy.json",
    )
    payload = _convert_v3_to_strict_v1(
        json.loads(evidence.read_text(encoding="utf-8"))
    )
    _write_json(evidence, payload)

    verified = evidence_verify.verify_tencent_provider_evidence(evidence)

    assert verified["status"] == evidence_verify.EVIDENCE_LEGACY_AUDIT_ONLY
    assert verified["replay_hash"] == ""
    _assert_safe(verified)


def test_arbitrary_self_signed_legacy_json_is_rejected(tmp_path):
    evidence = tmp_path / "arbitrary_legacy.json"
    payload = {
        "candidate_provider_validation_only": True,
        "data_ready": False,
        "hard_gate_authorized": False,
        "automatic_configuration_change": False,
        "candidates": [],
        "tickets": [],
        "orders": [],
    }
    _resign_evidence(payload)
    _write_json(evidence, payload)

    verified = evidence_verify.verify_tencent_provider_evidence(evidence)

    assert verified["status"] == evidence_verify.EVIDENCE_INVALID
    assert verified["evidence_integrity_verified"] is False
    assert verified["provider_validation_passed"] is False
    _assert_safe(verified)


def test_v2_success_contract_keeps_original_success_only_semantics(
    monkeypatch,
    tmp_path,
):
    evidence, _generated, _transport = _generate_success_evidence(
        monkeypatch,
        tmp_path,
        name="v2_success.json",
    )
    payload = _convert_v3_to_v2(
        json.loads(evidence.read_text(encoding="utf-8"))
    )
    _write_json(evidence, payload)

    verified = evidence_verify.verify_tencent_provider_evidence(evidence)

    assert verified["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert verified["evidence_schema_version"] == TENCENT_PROVIDER_EVIDENCE_SCHEMA_V2
    assert "provider_validation_passed" not in verified
    assert "evidence_integrity_verified" not in verified
    _assert_safe(verified)


def test_v3_success_contract_remains_compatible_without_external_anchor(
    monkeypatch,
    tmp_path,
):
    evidence, _generated, _transport = _generate_success_evidence(
        monkeypatch,
        tmp_path,
        name="v3_success.json",
    )
    payload = _convert_v4_to_v3(
        json.loads(evidence.read_text(encoding="utf-8"))
    )
    _write_json(evidence, payload)

    verified = evidence_verify.verify_tencent_provider_evidence(evidence)

    assert verified["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert verified["evidence_schema_version"] == (
        TENCENT_PROVIDER_EVIDENCE_SCHEMA_V3
    )
    assert verified["evidence_integrity_verified"] is True
    assert verified["provider_validation_passed"] is True
    _assert_safe(verified)


def test_v4_single_capability_failure_is_integrity_verified_not_provider_passed(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / "partial_failure.json"
    transport = SequencedTransport([_response_bytes(), TimeoutError()])
    result = validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=transport,
        clock=_clock(
            OBSERVED_AT,
            AVAILABLE_AT,
            OBSERVED_AT + timedelta(seconds=3),
        ),
    )

    verified = _verify_strict_v4(output)

    assert result["provider_validation_passed"] is False
    assert result["capability_results"]["quote"]["status"] == (
        "TENCENT_PROVIDER_CAPABILITY_VALIDATED"
    )
    assert result["capability_results"]["valuation"]["error_code"] == (
        "TENCENT_REQUEST_TIMEOUT"
    )
    assert verified["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert verified["evidence_integrity_verified"] is True
    assert verified["provider_validation_passed"] is False
    assert verified["verified_capabilities"] == ["quote"]
    assert verified["failure_reason_verification"]["valuation"][
        "failure_reason_verified"
    ] is False
    _assert_safe(verified)


def test_v4_resigned_summary_and_attempt_failure_reason_remains_uncorroborated(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / "paired_failure_tamper.json"
    validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=FakeTransport(_response_bytes(), error=TimeoutError()),
        clock=_clock(OBSERVED_AT, AVAILABLE_AT),
    )
    original_anchor = _file_sha256(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["capability_results"]["quote"][
        "error_code"
    ] = "TENCENT_REQUEST_FAILED"
    payload["capability_attempts"]["quote"][
        "error_code"
    ] = "TENCENT_REQUEST_FAILED"
    _resign_evidence(payload)
    _write_json(output, payload)

    anchored_to_original = evidence_verify.verify_tencent_provider_evidence(
        output,
        expected_file_sha256=original_anchor,
    )
    verified = _verify_strict_v4(output)

    assert anchored_to_original["status"] == evidence_verify.EVIDENCE_INVALID
    assert anchored_to_original["error_code"] == (
        "TENCENT_EVIDENCE_EXTERNAL_ANCHOR_MISMATCH"
    )
    assert verified["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert verified["evidence_integrity_verified"] is True
    assert verified["provider_validation_passed"] is False
    assert verified["failure_reason_verification"]["quote"] == {
        "failure_reason_verified": False,
        "status": "UNCORROBORATED",
        "error_code": "TENCENT_REQUEST_FAILED",
        "raw_hash": "",
    }
    _assert_safe(anchored_to_original)
    _assert_safe(verified)


def test_v4_captured_parse_failure_is_replayed_from_raw_response(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / "captured_parse_failure.json"
    invalid_response = _response_bytes(
        rows=[_line(code, _values(code)[:-1]) for code in CODES],
    )
    result = validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=FakeTransport(invalid_response),
        clock=_clock(
            OBSERVED_AT,
            AVAILABLE_AT,
            OBSERVED_AT + timedelta(seconds=3),
            AVAILABLE_AT + timedelta(seconds=3),
        ),
    )

    verified = _verify_strict_v4(output)

    assert result["provider_validation_passed"] is False
    assert set(result["raw_responses"]) == {"quote", "valuation"}
    assert all(
        attempt["response_captured"] is True
        for attempt in result["capability_attempts"].values()
    )
    assert all(
        item["error_code"] == "TENCENT_RESPONSE_FIELD_COUNT_INVALID"
        for item in result["capability_results"].values()
    )
    assert verified["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert verified["provider_validation_passed"] is False
    assert all(
        item["failure_reason_verified"] is True
        and item["status"] == "REPLAY_VERIFIED"
        and item["error_code"] == "TENCENT_RESPONSE_FIELD_COUNT_INVALID"
        for item in verified["failure_reason_verification"].values()
    )
    _assert_safe(verified)


def test_v4_provenance_rejection_is_integrity_verified_when_reproducible(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    monkeypatch.setattr(
        validation,
        "validate_source_provenance_batch",
        _rejected_provenance,
    )
    monkeypatch.setattr(
        evidence_verify,
        "validate_source_provenance_batch",
        _rejected_provenance,
    )
    output = tmp_path / "provenance_rejected.json"
    result = validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=FakeTransport(_response_bytes()),
        clock=_clock(
            OBSERVED_AT,
            AVAILABLE_AT,
            OBSERVED_AT + timedelta(seconds=3),
            AVAILABLE_AT + timedelta(seconds=3),
        ),
    )

    verified = _verify_strict_v4(output)

    assert result["provider_validation_passed"] is False
    assert all(
        item["status"] == "TENCENT_PROVIDER_PROVENANCE_REJECTED"
        for item in result["capability_results"].values()
    )
    assert verified["status"] == evidence_verify.EVIDENCE_VERIFIED
    assert verified["evidence_integrity_verified"] is True
    assert verified["provider_validation_passed"] is False
    assert verified["verified_capabilities"] == []
    _assert_safe(verified)


def test_v4_provenance_rejection_contract_drift_has_distinct_status(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    monkeypatch.setattr(
        validation,
        "validate_source_provenance_batch",
        _rejected_provenance,
    )
    output = tmp_path / "provenance_contract_drift.json"
    validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=FakeTransport(_response_bytes()),
        clock=_clock(
            OBSERVED_AT,
            AVAILABLE_AT,
            OBSERVED_AT + timedelta(seconds=3),
            AVAILABLE_AT + timedelta(seconds=3),
        ),
    )

    verified = _verify_strict_v4(output)

    assert verified["status"] == (
        evidence_verify.PROVENANCE_CONTRACT_VERSION_MISMATCH
    )
    assert verified["execution_ok"] is True
    assert verified["external_anchor_verified"] is True
    assert verified["provider_validation_passed"] is False
    _assert_safe(verified)


@pytest.mark.parametrize(
    ("field", "value", "error_code"),
    [
        (
            "producer_commit_sha",
            "f" * 40,
            "TENCENT_EVIDENCE_PRODUCER_COMMIT_MISMATCH",
        ),
        (
            "provider_verifier_contract_hash",
            "f" * 64,
            "TENCENT_EVIDENCE_PROVIDER_CONTRACT_MISMATCH",
        ),
    ],
)
def test_v4_rejects_resigned_wrong_commit_or_contract_hash(
    monkeypatch,
    tmp_path,
    field,
    value,
    error_code,
):
    output, _result, _transport = _generate_success_evidence(
        monkeypatch,
        tmp_path,
        name=f"wrong_{field}.json",
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload[field] = value
    _resign_evidence(payload)
    _write_json(output, payload)

    verified = _verify_strict_v4(output)

    assert verified["status"] == evidence_verify.EVIDENCE_INVALID
    assert verified["error_code"] == error_code
    _assert_safe(verified)


def test_v4_requires_matching_external_file_sha256(monkeypatch, tmp_path):
    output, _result, _transport = _generate_success_evidence(
        monkeypatch,
        tmp_path,
        name="external_anchor.json",
    )

    missing = evidence_verify.verify_tencent_provider_evidence(output)
    mismatched = evidence_verify.verify_tencent_provider_evidence(
        output,
        expected_file_sha256="f" * 64,
    )

    assert missing["status"] == evidence_verify.EVIDENCE_INVALID
    assert missing["error_code"] == (
        "TENCENT_EVIDENCE_EXTERNAL_ANCHOR_REQUIRED"
    )
    assert mismatched["status"] == evidence_verify.EVIDENCE_INVALID
    assert mismatched["error_code"] == (
        "TENCENT_EVIDENCE_EXTERNAL_ANCHOR_MISMATCH"
    )
    _assert_safe(missing)
    _assert_safe(mismatched)


def test_resigned_failed_evidence_tampering_is_rejected(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    output = tmp_path / "tampered_failure.json"
    validation.run_tencent_provider_validation(
        network=True,
        output=output,
        transport=FakeTransport(_response_bytes(), error=TimeoutError()),
        clock=_clock(OBSERVED_AT, AVAILABLE_AT),
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["capability_results"]["quote"][
        "error_code"
    ] = "TENCENT_REQUEST_FAILED"
    _resign_evidence(payload)
    _write_json(output, payload)

    verified = _verify_strict_v4(output)

    assert verified["status"] == evidence_verify.EVIDENCE_INVALID
    assert verified["evidence_integrity_verified"] is False
    assert verified["provider_validation_passed"] is False
    _assert_safe(verified)


def test_unmeasured_provider_activity_is_unknown_never_zero(monkeypatch, tmp_path):
    class UnmeasuredTransport:
        def __init__(self):
            self.delegate = FakeTransport(_response_bytes())

        def request(self, *args, **kwargs):
            return self.delegate.request(*args, **kwargs)

    monkeypatch.setattr(validation, "CACHE_ROOT", tmp_path.resolve())
    transport = UnmeasuredTransport()
    result = validation.run_tencent_provider_validation(
        network=True,
        output=tmp_path / "unknown_network_count.json",
        transport=transport,
        clock=_clock(
            OBSERVED_AT,
            AVAILABLE_AT,
            OBSERVED_AT + timedelta(seconds=3),
            AVAILABLE_AT + timedelta(seconds=3),
        ),
    )

    assert transport.delegate.request_count == 2
    assert result["network_requests_made"] is None
    assert result["upstream_network_activity"] == "unknown"
    _assert_safe(result)


def test_validation_writer_allows_exactly_one_cross_process_winner(tmp_path):
    target = tmp_path / "concurrent.json"
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_validation_writer,
            args=(
                str(target),
                str(tmp_path),
                {"writer": writer},
                start_event,
                result_queue,
            ),
        )
        for writer in (1, 2)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0

    outcomes = [result_queue.get(timeout=5) for _ in processes]
    assert [status for status, _detail in outcomes].count("written") == 1
    assert [status for status, _detail in outcomes].count("exists") == 1
    assert not [item for item in outcomes if item[0] == "error"]
    winning_bytes = target.read_bytes()
    winning_hash = hashlib.sha256(winning_bytes).hexdigest()
    assert json.loads(winning_bytes.decode("utf-8"))["writer"] in {1, 2}
    assert hashlib.sha256(target.read_bytes()).hexdigest() == winning_hash
    assert not list(tmp_path.glob("concurrent.json.*.tmp"))
