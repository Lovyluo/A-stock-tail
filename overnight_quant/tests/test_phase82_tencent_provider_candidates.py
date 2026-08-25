from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import socket

import pytest

from overnight_quant.data import source_capability_adapters as adapters
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_BOUND,
    SourceProviderEnvelope,
    audit_source_adapters,
)
from overnight_quant.data.source_capability_registry import (
    validate_source_provenance_batch,
)
from overnight_quant.data.tencent_direct_http_providers import (
    TENCENT_ADAPTER,
    TENCENT_ORIGIN_SOURCE,
    TENCENT_QUOTE_PROVIDER_KEY,
    TENCENT_SOURCE_VERSION,
    TENCENT_VALUATION_PROVIDER_KEY,
    TencentDirectHttpProviders,
    TencentHttpResponse,
    TencentProviderContractError,
)
from overnight_quant.scripts import run_tencent_provider_validation as validation


CODES = ("000001", "000333", "600000", "600519", "601318")
SOURCE_TIME = "20260825143000"
OBSERVED_AT = datetime(2026, 8, 25, 14, 30, 1, tzinfo=CN_TZ)
AVAILABLE_AT = datetime(2026, 8, 25, 14, 30, 2, tzinfo=CN_TZ)


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


def _candidate_test_binding(capability, provider_key):
    # This mapping is deliberately non-registerable: it preserves the fact
    # that a legacy provider still exists. B2.2b must model both keys before a
    # production binding can be represented.
    return {
        "capability": capability,
        "origin_source": TENCENT_ORIGIN_SOURCE,
        "adapter": TENCENT_ADAPTER,
        "source_version": TENCENT_SOURCE_VERSION,
        "provider_key": provider_key,
        "implementation_status": "bound",
        "legacy_implementation_present": True,
    }


def _execute_candidate_test_only(capability, provider_key, provider_callable):
    return adapters._execute_source_adapter_core(
        capability,
        origin_source=TENCENT_ORIGIN_SOURCE,
        adapter=TENCENT_ADAPTER,
        source_version=TENCENT_SOURCE_VERSION,
        provider_envelope=SourceProviderEnvelope(
            provider_key=provider_key,
            provider_callable=provider_callable,
        ),
        environ={},
        bindings=[_candidate_test_binding(capability, provider_key)],
        test_only=True,
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
    assert result["network_requests_made"] == 2
    assert output.exists()
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored["status"] == result["status"]
    assert "records_by_capability" in stored
    assert all(not rows for rows in stored["records_by_capability"].values())
    _assert_safe(result)
    _assert_safe(stored)


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
    assert result["network_requests_made"] == 2
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
    _assert_safe(result)
    _assert_safe(stored)


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
