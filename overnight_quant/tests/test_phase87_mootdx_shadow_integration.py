from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
import time

import pytest

from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.probe_worker_process import run_probe_worker_process
from overnight_quant.data.real_point_in_time_collectors import SourceContractError
from overnight_quant.data.mootdx_shadow_providers import (
    MOOTDX_MINUTE_PROVIDER_KEY,
    MOOTDX_QUALIFICATION_RECORD_SHA256,
    MOOTDX_QUALIFIED_CODES,
    MOOTDX_QUALIFIED_ENDPOINT,
    MOOTDX_SHADOW_DATA_UNAVAILABLE,
    MOOTDX_SHADOW_NETWORK_NOT_REQUESTED,
    MOOTDX_SHADOW_RECORDS_READY,
    MOOTDX_TRANSACTION_PROVIDER_KEY,
    MootdxQualifiedShadowProviders,
    build_mootdx_shadow_records,
)
from overnight_quant.data.source_capability_adapters import (
    SOURCE_ADAPTER_BOUND,
    SOURCE_ADAPTER_REQUEST_INVALID,
    SourceProviderEnvelope,
    audit_source_adapters,
    execute_source_adapter,
)
from overnight_quant.data.source_capability_registry import (
    QUALIFICATION_PROGRESS_MOOTDX,
    QUALIFICATION_PROGRESS_MOOTDX_UNQUALIFIED,
    get_source_capability_registry,
    route_source_capability,
    validate_source_provenance_batch,
)


class Frame:
    def __init__(self, rows):
        self.rows = list(rows)
        self.empty = not self.rows

    def to_dict(self, orient):
        assert orient == "records"
        return list(self.rows)


class Client:
    def __init__(
        self,
        *,
        fail=False,
        omit_1450=False,
        one_minute=False,
        invalid_ohlcv=False,
        trade_date="2026-09-17",
    ):
        self.fail = fail
        self.omit_1450 = omit_1450
        self.one_minute = one_minute
        self.invalid_ohlcv = invalid_ohlcv
        self.trade_date = trade_date
        self.closed = False
        self.bar_calls = []
        self.transaction_calls = []

    def bars(self, *, symbol, frequency, start, offset):
        self.bar_calls.append((symbol, frequency, start, offset))
        if self.fail:
            raise TimeoutError("fixed endpoint unavailable")
        rows = []
        minutes = [50] if self.one_minute else list(range(39, 52))
        if self.omit_1450:
            minutes = [minute for minute in minutes if minute != 50]
        for minute in minutes:
            rows.append(
                {
                    "datetime": (
                        f"{self.trade_date}T14:{minute:02d}:00+08:00"
                    ),
                    "open": 10,
                    "high": 9.8 if self.invalid_ohlcv else 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "vol": 1000,
                    "amount": 10100,
                }
            )
        return Frame(rows)

    def transaction(self, *, symbol, start, offset):
        self.transaction_calls.append((symbol, start, offset))
        if self.fail:
            raise TimeoutError("fixed endpoint unavailable")
        if start:
            return Frame([])
        return Frame(
            [
                {
                    "time": "14:49:00",
                    "price": 10,
                    "vol": 10,
                    "num": 1,
                    "buyorsell": 0,
                },
                {
                    "time": "14:50:59",
                    "price": 10.1,
                    "vol": 12,
                    "num": 2,
                    "buyorsell": 1,
                },
            ]
        )

    def close(self):
        self.closed = True


class Clock:
    def __init__(self, *, decision_ready=False, transaction_late=False):
        self.base = datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ)
        self.calls = 0
        self.decision_ready = decision_ready
        self.transaction_late = transaction_late

    def __call__(self):
        self.calls += 1
        if self.transaction_late and self.calls == 12:
            return datetime(
                2026, 9, 17, 14, 51, 5, 1000, tzinfo=CN_TZ
            )
        if self.decision_ready and self.calls >= 13:
            return datetime(2026, 9, 17, 14, 51, 10, tzinfo=CN_TZ)
        return self.base + timedelta(milliseconds=100 * self.calls)


def _assert_safe(result):
    assert result["data_ready"] is False
    assert result["hard_gate_authorized"] is False
    assert result["automatic_configuration_change"] is False
    assert result["candidates"] == []
    assert result["tickets"] == []
    assert result["orders"] == []


def test_registry_qualifies_only_fixed_mootdx_minute_and_transaction_scope():
    rows = {
        row["capability"]: row
        for row in get_source_capability_registry()
        if row["adapter"] == "mootdx"
    }

    for capability in ("minute_bar", "transaction"):
        row = rows[capability]
        assert row["role"] == "primary"
        assert row["hard_gate_eligible"] is True
        assert row["qualification_status"] == "qualified"
        assert row["qualification_progress"] == QUALIFICATION_PROGRESS_MOOTDX
        route = route_source_capability(
            capability,
            origin_source="tongdaxin",
            adapter="mootdx",
            require_hard_gate=True,
        )
        assert route["status"] == "SOURCE_ROUTE_SELECTED"
        assert route["hard_gate_authorized"] is True

    order_book = rows["order_book"]
    assert order_book["role"] == "audit_only"
    assert order_book["hard_gate_eligible"] is False
    assert order_book["qualification_status"] == "unqualified"
    assert order_book["qualification_progress"] == (
        QUALIFICATION_PROGRESS_MOOTDX_UNQUALIFIED
    )
    _assert_safe(
        route_source_capability(
            "order_book",
            origin_source="tongdaxin",
            adapter="mootdx",
            require_hard_gate=True,
        )
    )


def test_adapter_registry_binds_exactly_two_qualified_mootdx_providers():
    audit = audit_source_adapters(environ={})
    rows = [
        row
        for row in audit["adapter_matrix"]
        if row["adapter"] == "mootdx"
        and row["capability"] in {"minute_bar", "transaction"}
    ]

    assert audit["bound_count"] == 8
    assert {row["provider_key"] for row in rows} == {
        MOOTDX_MINUTE_PROVIDER_KEY,
        MOOTDX_TRANSACTION_PROVIDER_KEY,
    }
    assert all(row["implementation_status"] == "bound" for row in rows)
    assert all(row["qualification_progress"] == "3/3" for row in rows)
    _assert_safe(audit)


def test_explicit_fixed_endpoint_provider_emits_provenance_records():
    client = Client()
    provider = MootdxQualifiedShadowProviders(
        reversed(MOOTDX_QUALIFIED_CODES),
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(),
        client_factory=lambda: client,
    )
    try:
        minute = provider.collect_minute_records()
        transaction = provider.collect_transaction_records()
    finally:
        provider.close()

    assert len(minute) == 12 * 5
    assert {row["capability"] for row in minute} == {"minute_bar"}
    assert {row["origin_source"] for row in minute} == {"tongdaxin"}
    assert {row["adapter"] for row in minute} == {"mootdx"}
    assert all(row["is_final"] is True for row in minute)
    assert all(row["event_time"][11:16] <= "14:50" for row in minute)
    assert all(
        row["payload"]["endpoint_id"] == MOOTDX_QUALIFIED_ENDPOINT["id"]
        for row in minute
    )
    assert all(
        row["probe_evidence_hash"] == MOOTDX_QUALIFICATION_RECORD_SHA256
        for row in minute
    )
    assert len(transaction) == 10
    assert {row["capability"] for row in transaction} == {"transaction"}
    assert client.bar_calls == [
        (code, "1m", 0, 800) for code in sorted(MOOTDX_QUALIFIED_CODES)
    ]
    assert all(call[1:] == (0, 800) for call in client.transaction_calls)
    assert client.closed is True


def test_public_adapter_requires_exact_mootdx_provider_key():
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return []

    result = execute_source_adapter(
        "minute_bar",
        origin_source="tongdaxin",
        adapter="mootdx",
        source_version="mootdx_0.11.7_tdx_std_bars_1m_v2026-07-31",
        provider_envelope=SourceProviderEnvelope(
            provider_key="tests.Wrong.provider",
            provider_callable=provider,
        ),
        environ={},
    )

    assert result["status"] == SOURCE_ADAPTER_REQUEST_INVALID
    assert calls == 0
    _assert_safe(result)


def test_direct_provenance_cannot_expand_beyond_qualified_endpoint_scope():
    rows = []
    for code in MOOTDX_QUALIFIED_CODES:
        rows.append(
            {
                "capability": "minute_bar",
                "origin_source": "tongdaxin",
                "adapter": "mootdx",
                "source_version": (
                    "mootdx_0.11.7_tdx_std_bars_1m_v2026-07-31"
                ),
                "event_time": "2026-09-17T14:50:00+08:00",
                "observed_at": "2026-09-17T14:50:05+08:00",
                "available_at": "2026-09-17T14:50:06+08:00",
                "request_hash": "a" * 64,
                "raw_hash": "b" * 64,
                "payload": {
                    "code": code,
                    "endpoint_id": "mootdx_unqualified@1.2.3.4:7709",
                    "endpoint": {"host": "1.2.3.4", "port": 7709},
                    "qualification_record_sha256": (
                        MOOTDX_QUALIFICATION_RECORD_SHA256
                    ),
                },
            }
        )

    result = validate_source_provenance_batch(
        "minute_bar", rows, require_hard_gate=True, environ={}
    )

    assert result["status"] == "PROVENANCE_SCOPE_REJECTED"
    assert result["scope_errors"] == [
        "qualified_mootdx_endpoint_address_mismatch",
        "qualified_mootdx_endpoint_mismatch",
    ]
    assert result["hard_gate_authorized"] is False
    _assert_safe(result)


def test_complete_fixed_endpoint_batch_is_shadow_ready_but_not_data_ready():
    client = Client()
    result = build_mootdx_shadow_records(
        MOOTDX_QUALIFIED_CODES,
        network=True,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(decision_ready=True),
        client_factory=lambda: client,
    )

    assert result["status"] == MOOTDX_SHADOW_RECORDS_READY
    assert result["adapter_statuses"] == {
        "minute_bar": SOURCE_ADAPTER_BOUND,
        "transaction": SOURCE_ADAPTER_BOUND,
    }
    assert result["covered_codes"] == sorted(MOOTDX_QUALIFIED_CODES)
    assert result["source"]["endpoint"] == MOOTDX_QUALIFIED_ENDPOINT
    assert result["records_hash"]
    assert {row["capability"] for row in result["records"]} == {
        "minute_bar"
    }
    assert {row["capability"] for row in result["audit_records"]} == {
        "transaction"
    }
    assert all(
        row["feature_scoring_eligible"] is False
        for row in result["audit_records"]
    )
    assert client.bar_calls == [
        (code, "1m", 0, 800) for code in sorted(MOOTDX_QUALIFIED_CODES)
    ]
    assert len(client.transaction_calls) == len(MOOTDX_QUALIFIED_CODES)
    _assert_safe(result)


@pytest.mark.parametrize(
    ("client", "expected_error"),
    [
        (Client(omit_1450=True), "mootdx_minute_1450_missing:000001"),
        (
            Client(one_minute=True),
            "mootdx_minute_unique_minutes_insufficient:000001",
        ),
        (
            Client(invalid_ohlcv=True),
            "mootdx_minute_ohlcv_invalid:000001:range",
        ),
    ],
)
def test_minute_batch_rejects_incomplete_or_invalid_stock_data(
    client,
    expected_error,
):
    provider = MootdxQualifiedShadowProviders(
        MOOTDX_QUALIFIED_CODES,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(),
        client_factory=lambda: client,
    )
    try:
        with pytest.raises(SourceContractError, match=expected_error):
            provider.collect_minute_records()
    finally:
        provider.close()


def test_minute_batch_rejects_mixed_trade_dates():
    provider = MootdxQualifiedShadowProviders(
        MOOTDX_QUALIFIED_CODES,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(),
        client_factory=Client,
    )
    try:
        batch = provider.collector.collect_minute_bars(provider.observed_at)
        records = [dict(row) for row in batch.records]
        records[0]["event_time"] = "2026-09-16T14:39:00+08:00"
        with pytest.raises(
            SourceContractError,
            match="mootdx_minute_trade_date_mismatch:000001",
        ):
            provider._validate_and_finalize_minute_records(records)
    finally:
        provider.close()


@pytest.mark.parametrize(
    "client",
    [
        Client(omit_1450=True),
        Client(one_minute=True),
        Client(invalid_ohlcv=True),
    ],
)
def test_invalid_minute_batch_never_returns_partial_shadow_records(client):
    result = build_mootdx_shadow_records(
        MOOTDX_QUALIFIED_CODES,
        network=True,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(decision_ready=True),
        client_factory=lambda: client,
    )

    assert result["status"] == MOOTDX_SHADOW_DATA_UNAVAILABLE
    assert result["records"] == []
    assert result["audit_records"] == []
    _assert_safe(result)


def test_transaction_batch_completed_after_collection_deadline_is_rejected():
    provider = MootdxQualifiedShadowProviders(
        MOOTDX_QUALIFIED_CODES,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(transaction_late=True),
        client_factory=Client,
    )
    try:
        provider.collect_minute_records()
        with pytest.raises(
            SourceContractError,
            match="mootdx_transaction_collection_deadline_exceeded",
        ):
            provider.collect_transaction_records()
    finally:
        provider.close()

    result = build_mootdx_shadow_records(
        MOOTDX_QUALIFIED_CODES,
        network=True,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(transaction_late=True, decision_ready=True),
        client_factory=Client,
    )
    assert result["status"] == MOOTDX_SHADOW_DATA_UNAVAILABLE
    assert result["reason"] == "transaction_provider_not_ready"
    assert result["records"] == []
    assert result["audit_records"] == []
    _assert_safe(result)


def test_shadow_batch_cannot_return_before_decision_time():
    result = build_mootdx_shadow_records(
        MOOTDX_QUALIFIED_CODES,
        network=True,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(decision_ready=False),
        client_factory=Client,
    )

    assert result["status"] == MOOTDX_SHADOW_DATA_UNAVAILABLE
    assert result["reason"] == "decision_time_not_reached"
    assert result["records"] == []
    assert result["audit_records"] == []
    _assert_safe(result)


def test_blocked_batch_is_terminated_without_late_write(tmp_path):
    worker = tmp_path / "blocking_shadow_worker.py"
    started = tmp_path / "started.txt"
    completed = tmp_path / "completed.txt"
    worker.write_text(
        "\n".join(
            [
                "import json, pathlib, sys, time",
                "task = json.loads(sys.stdin.read())",
                "pathlib.Path(task['started']).write_text('started')",
                "time.sleep(40)",
                "pathlib.Path(task['completed']).write_text('completed')",
                "print(json.dumps({'ok': True, 'payload': {}}))",
            ]
        ),
        encoding="utf-8",
    )

    def blocked_runner(task, deadline_ms):
        return run_probe_worker_process(
            {"started": str(started), "completed": str(completed)},
            deadline_ms,
            worker_command=[sys.executable, str(worker)],
        )

    result = build_mootdx_shadow_records(
        MOOTDX_QUALIFIED_CODES,
        network=True,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(decision_ready=True),
        worker_runner=blocked_runner,
    )

    assert result["status"] == MOOTDX_SHADOW_DATA_UNAVAILABLE
    assert result["reason"] == "minute_provider_not_ready"
    assert result["batch_audit"]["shadow_minute_batch"] == {
        "request_deadline_ms": 2000,
        "request_timed_out": True,
        "worker_terminated": True,
        "elapsed_ms": result["batch_audit"]["shadow_minute_batch"][
            "elapsed_ms"
        ],
        "error_code": "REQUEST_DEADLINE_EXCEEDED",
    }
    assert started.exists()
    time.sleep(0.25)
    assert not completed.exists()
    assert not list(Path(tmp_path).glob("*.tmp"))
    _assert_safe(result)


def test_blocked_transaction_batch_is_terminated_without_residual_worker(
    tmp_path,
):
    worker = tmp_path / "blocking_transaction_worker.py"
    completed = tmp_path / "completed.txt"
    worker.write_text(
        "\n".join(
            [
                "import json, pathlib, sys, time",
                "task = json.loads(sys.stdin.read())",
                "time.sleep(40)",
                "pathlib.Path(task['completed']).write_text('completed')",
            ]
        ),
        encoding="utf-8",
    )

    def blocked_runner(task, deadline_ms):
        return run_probe_worker_process(
            {"completed": str(completed)},
            deadline_ms,
            worker_command=[sys.executable, str(worker)],
        )

    provider = MootdxQualifiedShadowProviders(
        MOOTDX_QUALIFIED_CODES,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(decision_ready=True),
        worker_runner=blocked_runner,
    )
    try:
        with pytest.raises(
            SourceContractError,
            match="mootdx_shadow_transaction_batch_deadline_exceeded",
        ):
            provider._run_worker_batch("shadow_transaction_batch")
    finally:
        provider.close()

    time.sleep(0.25)
    assert not completed.exists()
    assert provider.batch_audit["shadow_transaction_batch"][
        "worker_terminated"
    ] is True


def test_fixed_endpoint_failure_closes_without_fallback_or_outputs():
    client = Client(fail=True)
    result = build_mootdx_shadow_records(
        MOOTDX_QUALIFIED_CODES,
        network=True,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(),
        client_factory=lambda: client,
    )

    assert result["status"] == MOOTDX_SHADOW_DATA_UNAVAILABLE
    assert result["reason"] == "minute_provider_not_ready"
    assert len(client.bar_calls) == 1
    assert client.transaction_calls == []
    assert result["records"] == []
    _assert_safe(result)


def test_network_is_explicit_and_stock_scope_cannot_expand():
    no_network = build_mootdx_shadow_records(
        MOOTDX_QUALIFIED_CODES,
        network=False,
    )
    expanded = build_mootdx_shadow_records(
        [*MOOTDX_QUALIFIED_CODES, "000002"],
        network=True,
        observed_at=datetime(2026, 9, 17, 14, 51, 0, tzinfo=CN_TZ),
        clock=Clock(),
        client_factory=Client,
    )

    assert no_network["status"] == MOOTDX_SHADOW_NETWORK_NOT_REQUESTED
    assert expanded["status"] == MOOTDX_SHADOW_DATA_UNAVAILABLE
    assert expanded["reason"] == "mootdx_qualified_stock_scope_mismatch"
    _assert_safe(no_network)
    _assert_safe(expanded)


@pytest.mark.parametrize("capability", ["minute_bar", "transaction"])
def test_qualified_mootdx_adapter_never_authorizes_hard_gate_directly(capability):
    provider_key = (
        MOOTDX_MINUTE_PROVIDER_KEY
        if capability == "minute_bar"
        else MOOTDX_TRANSACTION_PROVIDER_KEY
    )
    version = (
        "mootdx_0.11.7_tdx_std_bars_1m_v2026-07-31"
        if capability == "minute_bar"
        else "mootdx_0.11.7_tdx_std_transaction_v2026-08-06"
    )
    result = execute_source_adapter(
        capability,
        origin_source="tongdaxin",
        adapter="mootdx",
        source_version=version,
        provider_envelope=SourceProviderEnvelope(
            provider_key=provider_key,
            provider_callable=lambda: [],
        ),
        environ={},
    )

    assert result["hard_gate_authorized"] is False
    _assert_safe(result)
