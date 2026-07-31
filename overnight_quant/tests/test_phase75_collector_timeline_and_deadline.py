from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import threading
import time

import pytest

from overnight_quant.data.close_confirmation_readiness import (
    validate_close_confirmation_readiness,
)
from overnight_quant.data.close_time_contract import (
    CloseTimeContract,
    MINUTE_LABEL_END,
    MINUTE_LABEL_START,
    build_close_time_contract,
)
from overnight_quant.data.collector_stress import run_provider_stress
from overnight_quant.data.market_calendar import CN_TZ
from overnight_quant.data.minute_label_probe import (
    classify_minute_label_samples,
    run_scheduled_minute_label_probe,
)
from overnight_quant.data.point_in_time import (
    build_point_in_time_record,
)
from overnight_quant.data.real_point_in_time_collectors import (
    EASTMONEY_BOARD_URL,
    EASTMONEY_FUND_URL,
    EASTMONEY_INDUSTRY_LIST_URL,
    EASTMONEY_INDUSTRY_MAP_URL,
    SINA_FUND_URL,
    TENCENT_QUOTE_URL,
    RawHttpResponse,
    RealPointInTimeCollectors,
    RealSourceError,
    RequestsTransport,
)
from overnight_quant.data.snapshot_store import (
    CloseWindowCollector,
    ImmutableSnapshotStore,
    ProviderBatch,
    ProviderSpec,
    close_snapshot_hash,
)
from overnight_quant.reports.close_confirmation_report import (
    write_close_confirmation_report,
)
from overnight_quant.strategy.close_confirmation_v1.strategy import (
    CloseConfirmationStrategy,
)


TRADE_DATE = "2026-07-30"
PROBE_HASH = "e" * 64


def test_four_stage_timeline_is_conservative_until_probe_verifies():
    unverified = build_close_time_contract(TRADE_DATE)
    minute_start = build_close_time_contract(
        TRADE_DATE,
        minute_label_semantics=MINUTE_LABEL_START,
        verified=True,
        probe_evidence_hash=PROBE_HASH,
    )
    minute_end = build_close_time_contract(
        TRADE_DATE,
        minute_label_semantics=MINUTE_LABEL_END,
        verified=True,
        probe_evidence_hash=PROBE_HASH,
    )

    assert unverified.minute_label_verified is False
    assert unverified.feature_event_cutoff[11:19] == "14:50:00"
    assert unverified.collection_deadline[11:19] == "14:51:05"
    assert unverified.decision_time[11:19] == "14:51:10"
    assert unverified.execution_not_before[11:19] == "14:52:00"
    assert minute_start.minute_label_verified is True
    assert minute_start.execution_not_before[11:19] == "14:52:00"
    assert minute_end.collection_deadline[11:19] == "14:50:30"
    assert minute_end.execution_not_before[11:19] == "14:51:00"


def test_minute_label_change_detector_distinguishes_start_and_end():
    start_samples = _probe_samples(
        hashes=["missing", "a", "b", "b"]
    )
    end_samples = _probe_samples(
        hashes=["a", "a", "a", "a"]
    )

    start = classify_minute_label_samples(start_samples)
    end = classify_minute_label_samples(end_samples)

    assert start["minute_label_semantics"] == MINUTE_LABEL_START
    assert start["minute_label_validation_status"] == "VERIFIED"
    assert end["minute_label_semantics"] == MINUTE_LABEL_END
    assert end["minute_label_validation_status"] == "VERIFIED"
    assert len(start["probe_evidence_hash"]) == 64
    assert (
        start["recommended_time_contract"]["probe_evidence_hash"]
        == start["probe_evidence_hash"]
    )


def test_absent_then_unchanged_minute_label_is_inconclusive():
    result = classify_minute_label_samples(
        _probe_samples(["missing", "a", "a", "a"])
    )

    assert result["minute_label_validation_status"] == (
        "INCONCLUSIVE"
    )


@pytest.mark.parametrize(
    "mutation",
    ["late", "failed", "coverage"],
)
def test_probe_timing_failure_or_coverage_gap_is_inconclusive(
    mutation,
):
    samples = _probe_samples(["missing", "a", "b", "b"])
    if mutation == "late":
        samples[1]["request_started_at"] = (
            "2026-07-30T14:50:08+08:00"
        )
        samples[1]["request_completed_at"] = (
            "2026-07-30T14:50:09+08:00"
        )
    elif mutation == "failed":
        samples[1]["error"] = "timeout"
    else:
        samples[1]["covered_codes"] = []

    result = classify_minute_label_samples(samples)

    assert result["minute_label_validation_status"] == (
        "INCONCLUSIVE"
    )


def test_verified_time_contract_requires_evidence_and_valid_order():
    with pytest.raises(
        ValueError,
        match="requires_probe_evidence_hash",
    ):
        build_close_time_contract(
            TRADE_DATE,
            minute_label_semantics=MINUTE_LABEL_START,
            verified=True,
        )

    with pytest.raises(
        ValueError,
        match="close_time_contract_order_invalid",
    ):
        CloseTimeContract(
            feature_event_cutoff=(
                "2026-07-30T14:50:00+08:00"
            ),
            collection_deadline=(
                "2026-07-30T14:49:59+08:00"
            ),
            decision_time="2026-07-30T14:51:10+08:00",
            execution_not_before=(
                "2026-07-30T14:52:00+08:00"
            ),
        )


def test_scheduled_probe_records_request_evidence():
    clock = _AdvancingClock(
        datetime.fromisoformat(
            "2026-07-30T14:49:50+08:00"
        )
    )
    collectors = _ProbeCollectors(
        ["000001", "600000"],
        clock,
    )

    result = run_scheduled_minute_label_probe(
        collectors.codes,
        trade_date=TRADE_DATE,
        collectors=collectors,
        clock=clock,
        sleep=clock.advance,
        monotonic=lambda: clock.now.timestamp(),
    )

    assert result["status"] == "MINUTE_LABEL_VERIFIED"
    assert result["minute_label_semantics"] == MINUTE_LABEL_START
    assert result["requires_manual_review"] is True
    assert len(result["probe_evidence_hash"]) == 64
    assert len(result["samples"]) == 4
    assert all(
        sample["request_started_at"]
        and sample["request_completed_at"]
        and sample["request_elapsed_ms"] == pytest.approx(200)
        and sample["source_versions"] == ["unit_minute_v1"]
        and sample["sample_trade_date"] == TRADE_DATE
        for sample in result["samples"]
    )
    assert result["samples"][0]["presence_by_code"] == {
        "000001": False,
        "600000": False,
    }


def test_unverified_timeline_cannot_be_data_ready():
    contract = build_close_time_contract(TRADE_DATE)
    readiness = validate_close_confirmation_readiness(
        _complete_snapshot(contract)
    )

    assert readiness["data_ready"] is False
    assert "minute_label_semantics_unverified" in readiness[
        "readiness_errors"
    ]


def test_global_deadline_marks_unfinished_provider_and_returns(tmp_path):
    contract = CloseTimeContract(
        feature_event_cutoff="2026-07-30T14:50:00+08:00",
        collection_deadline="2026-07-30T14:50:01+08:00",
        decision_time="2026-07-30T14:50:02+08:00",
        execution_not_before="2026-07-30T14:51:00+08:00",
        minute_label_semantics=MINUTE_LABEL_END,
        minute_label_validation_status="VERIFIED",
        probe_evidence_hash=PROBE_HASH,
    )

    cancelled = threading.Event()

    def slow_provider(_):
        cancelled.wait(2)
        return []

    collector = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path),
        {
            "slow": ProviderSpec(
                slow_provider,
                ["quote"],
                "unit_slow_v1",
                cancel_setter=cancelled.set,
            )
        },
        time_contract=contract,
    )
    started = time.perf_counter()
    result = collector.collect(
        datetime.fromisoformat("2026-07-30T14:50:00+08:00")
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 1.2
    assert result["data_ready"] is False
    assert result["provider_metrics"]["slow"]["status"] == (
        "DEADLINE_EXCEEDED"
    )
    assert result["collection_metrics"][
        "deadline_exceeded_count"
    ] == 1
    assert result["records"] == []
    _assert_no_outputs(result)


def test_collection_has_no_background_metrics_and_next_run_is_clean(
    tmp_path,
):
    session = _SlowSession([0.15, 0.0, 0.0])
    transport = RequestsTransport(
        session=session,
        timeout_seconds=1,
        max_attempts=2,
        min_host_interval_seconds=0,
    )
    requester = _RequestingProvider(transport)
    short_contract = CloseTimeContract(
        feature_event_cutoff=(
            "2026-07-30T14:50:00+08:00"
        ),
        collection_deadline=(
            "2026-07-30T14:50:00.050000+08:00"
        ),
        decision_time="2026-07-30T14:50:01+08:00",
        execution_not_before=(
            "2026-07-30T14:51:00+08:00"
        ),
        minute_label_semantics=MINUTE_LABEL_END,
        minute_label_validation_status="VERIFIED",
        probe_evidence_hash=PROBE_HASH,
    )
    observed = datetime.fromisoformat(
        "2026-07-30T14:50:00+08:00"
    )
    first = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path / "first"),
        {
            "requester": ProviderSpec(
                requester.collect,
                ["quote"],
                "unit_requester_v1",
                priority=1,
                stage="tail",
            )
        },
        clock=lambda: observed,
        time_contract=short_contract,
    ).collect(observed)
    metrics_after_return = transport.metrics_snapshot()
    calls_after_return = session.calls
    time.sleep(0.1)

    assert transport.metrics_snapshot() == metrics_after_return
    assert session.calls == calls_after_return == 1
    assert first["collection_metrics"]["late_provider_count"] == 1

    long_contract = CloseTimeContract(
        feature_event_cutoff=(
            "2026-07-30T14:50:00+08:00"
        ),
        collection_deadline=(
            "2026-07-30T14:50:01+08:00"
        ),
        decision_time="2026-07-30T14:50:02+08:00",
        execution_not_before=(
            "2026-07-30T14:51:00+08:00"
        ),
        minute_label_semantics=MINUTE_LABEL_END,
        minute_label_validation_status="VERIFIED",
        probe_evidence_hash=PROBE_HASH,
    )
    second = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path / "second"),
        {
            "requester": ProviderSpec(
                requester.collect,
                ["quote"],
                "unit_requester_v1",
                priority=1,
                stage="tail",
            )
        },
        clock=lambda: observed,
        time_contract=long_contract,
    ).collect(observed)
    second_metrics = transport.metrics_snapshot()
    second_calls = session.calls
    time.sleep(0.1)

    assert second["collection_metrics"]["late_provider_count"] == 0
    assert session.calls == second_calls == 3
    assert transport.metrics_snapshot() == second_metrics


def test_provider_priority_and_prewarm_groups_are_explicit(tmp_path):
    order = []
    contract = build_close_time_contract(
        TRADE_DATE,
        minute_label_semantics=MINUTE_LABEL_START,
        verified=True,
        probe_evidence_hash=PROBE_HASH,
    )

    def provider(name):
        def collect(_):
            order.append(name)
            return ProviderBatch()

        return collect

    providers = {
        "audit": ProviderSpec(
            provider("audit"),
            priority=4,
            stage="audit",
        ),
        "news": ProviderSpec(
            provider("news"),
            priority=3,
            stage="news",
        ),
        "static": ProviderSpec(
            provider("static"),
            priority=2,
            stage="prewarm",
        ),
        "tail": ProviderSpec(
            provider("tail"),
            priority=1,
            stage="tail",
        ),
    }
    observed = datetime.fromisoformat(
        "2026-07-30T14:50:00+08:00"
    )
    CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path),
        providers,
        clock=lambda: observed,
        time_contract=contract,
        max_workers=1,
    ).collect(observed)

    assert order == ["tail", "static", "news", "audit"]
    collectors = RealPointInTimeCollectors(["000001"])
    assert all(
        item.priority == 1 and item.stage == "tail"
        for item in collectors.tail_provider_map().values()
    )
    assert "eastmoney_industry_mapping" in (
        collectors.prewarm_provider_map()
    )


def test_concurrent_completion_order_does_not_change_hash_or_decision(
    tmp_path,
):
    contract = build_close_time_contract(
        TRADE_DATE,
        minute_label_semantics=MINUTE_LABEL_START,
        verified=True,
        probe_evidence_hash=PROBE_HASH,
    )
    records = _complete_records(contract)
    midpoint = len(records) // 2

    def collect_with_delays(root, first_delay, second_delay):
        def first(_):
            time.sleep(first_delay)
            return records[:midpoint]

        def second(_):
            time.sleep(second_delay)
            return records[midpoint:]

        collector = CloseWindowCollector(
            ImmutableSnapshotStore(root),
                {
                    "a": ProviderSpec(
                        first,
                        ["news"],
                        "unit_a_v1",
                    ),
                "b": ProviderSpec(second, [], "unit_b_v1"),
            },
            clock=lambda: datetime.fromisoformat(
                "2026-07-30T14:50:20+08:00"
            ),
            time_contract=contract,
            max_workers=2,
        )
        return collector.collect(
            datetime.fromisoformat(
                "2026-07-30T14:50:05+08:00"
            )
        )

    first = collect_with_delays(tmp_path / "first", 0.02, 0.0)
    second = collect_with_delays(tmp_path / "second", 0.0, 0.02)
    first_hash = close_snapshot_hash(
        decision_time=contract.decision_time,
        records=first["records"],
        source_status=first["source_status"],
        time_contract=contract,
    )
    second_hash = close_snapshot_hash(
        decision_time=contract.decision_time,
        records=second["records"],
        source_status=second["source_status"],
        time_contract=contract,
    )
    strategy = CloseConfirmationStrategy()
    first_result = strategy.evaluate_snapshot(
        {
            **first,
            "trade_date": TRADE_DATE,
        },
        mode="shadow",
    )
    second_result = strategy.evaluate_snapshot(
        {
            **second,
            "trade_date": TRADE_DATE,
        },
        mode="shadow",
    )

    assert first_hash == second_hash
    assert first_result["data_ready"] is True
    assert second_result["data_ready"] is True
    assert (
        first_result["scored"][0]["decision_hash"]
        == second_result["scored"][0]["decision_hash"]
    )


def test_fund_flow_primary_skips_backup_and_proxy_is_isolated():
    primary_transport = _FundTransport(primary_ok=True)
    primary = RealPointInTimeCollectors(
        ["000001"],
        transport=primary_transport,
        clock=lambda: datetime.fromisoformat(
            "2026-07-30T14:50:20+08:00"
        ),
    ).collect_selected_fund_flow(
        datetime.fromisoformat("2026-07-30T14:50:05+08:00")
    )
    assert [call["url"] for call in primary_transport.calls] == [
        EASTMONEY_FUND_URL
    ]
    assert primary.records[0]["payload"]["is_proxy"] is False
    assert primary.records[0]["payload"][
        "eligible_for_hard_gate"
    ] is True

    backup_transport = _FundTransport(primary_ok=False)
    backup = RealPointInTimeCollectors(
        ["000001"],
        transport=backup_transport,
        clock=lambda: datetime.fromisoformat(
            "2026-07-30T14:50:20+08:00"
        ),
    ).collect_selected_fund_flow(
        datetime.fromisoformat("2026-07-30T14:50:05+08:00")
    )
    assert [call["url"] for call in backup_transport.calls] == [
        EASTMONEY_FUND_URL,
        SINA_FUND_URL,
    ]
    assert len(backup.records) == 1
    assert backup.records[0]["payload"]["is_proxy"] is True
    assert backup.records[0]["payload"][
        "eligible_for_hard_gate"
    ] is False


def test_industry_backup_requires_full_breadth_semantics():
    transport = _IndustryBackupTransport()
    batch = RealPointInTimeCollectors(
        ["000001"],
        transport=transport,
        clock=lambda: datetime.fromisoformat(
            "2026-07-30T14:50:20+08:00"
        ),
    ).collect_industry(
        datetime.fromisoformat("2026-07-30T14:50:05+08:00")
    )
    row = batch.records[0]

    assert EASTMONEY_BOARD_URL in transport.calls
    assert EASTMONEY_INDUSTRY_LIST_URL in transport.calls
    assert row["source"].startswith("eastmoney_industry_clist")
    assert row["payload"]["industry_source_role"] == "backup"
    assert row["payload"]["name"] == "银行"
    assert row["payload"]["up_count"] == 30
    assert row["payload"]["down_count"] == 18
    assert row["payload"]["flat_count"] == 2
    assert row["payload"]["breadth_ratio"] == pytest.approx(0.6)
    assert row["payload"]["relative_strength_pct"] == (
        pytest.approx(1.43)
    )


def test_proxy_does_not_change_formal_score_or_decision_hash():
    contract = build_close_time_contract(
        TRADE_DATE,
        minute_label_semantics=MINUTE_LABEL_START,
        verified=True,
        probe_evidence_hash=PROBE_HASH,
    )
    snapshot = _complete_snapshot(contract)
    strategy = CloseConfirmationStrategy()
    base = strategy.evaluate_snapshot(snapshot, mode="shadow")
    with_proxy = deepcopy(snapshot)
    with_proxy["records"].append(
        _record(
            contract,
            "fund_flow",
            {
                "code": "000001",
                "main_net": -999999999,
                "semantic_class": "current_snapshot_money_flow_proxy",
                "timestamp_quality": "collector_completion_only",
                "is_proxy": True,
                "eligible_for_hard_gate": False,
                "field_definition_version": "unit_proxy_v1",
            },
            event_time="2026-07-30 14:50:00",
            source="sina_money_flow_current",
        )
    )
    changed = strategy.evaluate_snapshot(with_proxy, mode="shadow")

    assert base["scored"][0]["total_score"] == (
        changed["scored"][0]["total_score"]
    )
    assert base["scored"][0]["decision_hash"] == (
        changed["scored"][0]["decision_hash"]
    )


def test_proxy_only_fails_gate_and_dashboard_report_is_explicit(
    tmp_path,
):
    contract = build_close_time_contract(
        TRADE_DATE,
        minute_label_semantics=MINUTE_LABEL_START,
        verified=True,
        probe_evidence_hash=PROBE_HASH,
    )
    snapshot = _complete_snapshot(contract)
    for row in snapshot["records"]:
        if row["data_type"] == "fund_flow":
            row["payload"].update(
                {
                    "is_proxy": True,
                    "eligible_for_hard_gate": False,
                    "semantic_class": (
                        "current_snapshot_money_flow_proxy"
                    ),
                }
            )
            row["source"] = "sina_money_flow_current"
    result = CloseConfirmationStrategy().evaluate_snapshot(
        snapshot,
        mode="shadow",
    )
    report_path = write_close_confirmation_report(
        result,
        tmp_path,
        TRADE_DATE,
    )
    text = open(report_path, encoding="utf-8").read()

    assert result["data_ready"] is False
    assert result["fund_flow_proxy_only"] is True
    assert result["fund_flow_gate_notice"] == (
        "资金流代理数据，不满足正式门禁"
    )
    assert "资金流代理数据，不满足正式门禁" in text
    _assert_no_outputs(result)


def test_late_fund_flow_cannot_change_score_or_snapshot_hash():
    contract = build_close_time_contract(
        TRADE_DATE,
        minute_label_semantics=MINUTE_LABEL_START,
        verified=True,
        probe_evidence_hash=PROBE_HASH,
    )
    records = _complete_records(contract)
    snapshot = _complete_snapshot(contract)
    base = CloseConfirmationStrategy().evaluate_snapshot(
        snapshot,
        mode="shadow",
    )
    late = _record(
        contract,
        "fund_flow",
        {
            "code": "000001",
            "main_net": -999999999,
            "is_proxy": False,
            "eligible_for_hard_gate": True,
        },
        event_time="2026-07-30 14:50:00",
        available_at="2026-07-30 14:51:06",
        source="late.formal.fund",
    )
    changed_snapshot = {
        **snapshot,
        "records": [*records, late],
    }
    changed = CloseConfirmationStrategy().evaluate_snapshot(
        changed_snapshot,
        mode="shadow",
    )
    first_hash = close_snapshot_hash(
        decision_time=contract.decision_time,
        records=records,
        source_status=snapshot["source_status"],
        time_contract=contract,
    )
    second_hash = close_snapshot_hash(
        decision_time=contract.decision_time,
        records=[*records, late],
        source_status=snapshot["source_status"],
        time_contract=contract,
    )

    assert first_hash == second_hash
    assert base["scored"][0]["decision_hash"] == (
        changed["scored"][0]["decision_hash"]
    )


def test_premarket_run_cannot_claim_close_sla(tmp_path):
    contract = build_close_time_contract(
        TRADE_DATE,
        minute_label_semantics=MINUTE_LABEL_START,
        verified=True,
        probe_evidence_hash=PROBE_HASH,
    )
    collector = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path),
        {"unit": lambda _: _complete_records(contract)},
        time_contract=contract,
    )
    result = collector.collect(
        datetime.fromisoformat("2026-07-30T09:20:00+08:00")
    )

    assert result["status"] == "NOT_COLLECTION_WINDOW"
    assert result["data_ready"] is False
    assert result["records"] == []


@pytest.mark.parametrize("stock_count", [1, 10, 30, 50])
def test_stress_runner_reports_each_candidate_scale(stock_count):
    records = [
        {
            "data_type": "quote",
            "payload": {"code": f"{index:06d}"},
        }
        for index in range(stock_count)
    ]
    provider = ProviderSpec(
        lambda _: ProviderBatch(
            records=records,
            data_types=["quote"],
            source_version="unit_stress_v1",
        ),
        ["quote"],
        "unit_stress_v1",
        metrics_getter=lambda: {
            "request_count": stock_count,
            "retry_count": 0,
            "failure_count": 0,
            "rate_limit_wait_count": 0,
            "deadline_trigger_count": 0,
        },
    )
    result = run_provider_stress(
        {"quote": provider},
        expected_codes=[
            f"{index:06d}"
            for index in range(stock_count)
        ],
        deadline_seconds=1,
    )

    assert result["request_count"] == stock_count
    assert result["formal_coverage_by_type"]["quote"] == (
        stock_count
    )
    assert result["proxy_coverage_by_type"] == {}
    assert result["provider_success_ratio"] == 1.0
    assert result["formal_complete_stock_count"] == 0
    assert result["formal_complete_stock_ratio"] == 0
    assert result["not_started_provider_count"] == 0
    assert result["late_provider_count"] == 0
    assert result["provider_latency_ms"]["p50"] is not None
    _assert_no_outputs(result)


def test_stress_metrics_separate_formal_and_proxy_coverage():
    rows = [
        {
            "data_type": "quote",
            "payload": {"code": "000001"},
        },
        {
            "data_type": "fund_flow",
            "payload": {
                "code": "000001",
                "is_proxy": True,
                "eligible_for_hard_gate": False,
            },
        },
    ]
    result = run_provider_stress(
        {
            "mixed": ProviderSpec(
                lambda _: ProviderBatch(records=rows),
                priority=1,
                stage="tail",
            )
        },
        expected_codes=["000001"],
        deadline_seconds=1,
    )

    assert result["formal_coverage_by_type"] == {"quote": 1}
    assert result["proxy_coverage_by_type"] == {
        "fund_flow": 1
    }
    assert result["formal_complete_stock_count"] == 0
    assert result["formal_complete_stock_ratio"] == 0
    _assert_no_outputs(result)


def _complete_snapshot(contract):
    return {
        "status": "FROZEN_1450",
        "trade_date": TRADE_DATE,
        "decision_time": contract.decision_time,
        "time_contract": contract.as_dict(),
        "records": _complete_records(contract),
        "source_status": [
            _source_status(contract, "news", "SUCCESS"),
        ],
    }


def _complete_records(contract):
    trade_dates = _trade_dates(60)
    rows = [
        _record(
            contract,
            "trading_calendar",
            {
                "calendar_kind": "benchmark_index_trade_dates",
                "calendar_name": "unit_cn_calendar",
                "trade_dates": trade_dates,
                "latest_completed_trade_date": trade_dates[-1],
            },
            event_time="2026-07-29 15:00:00",
            source="unit.calendar",
        ),
        _record(
            contract,
            "market",
            {
                "index_change_pct": 0.5,
                "breadth_ratio": 0.6,
            },
            source="unit.market",
        ),
        _record(
            contract,
            "industry",
            {
                "name": "银行",
                "change_pct": 0.8,
                "relative_strength_pct": 0.3,
                "breadth_ratio": 0.62,
                "up_count": 31,
                "down_count": 17,
                "flat_count": 2,
            },
            source="unit.industry",
        ),
        _record(
            contract,
            "quote",
            {
                "code": "000001",
                "name": "平安银行",
                "industry_name": "银行",
                "price": 10.2,
                "prev_close": 10.0,
                "amount_wan": 50000,
                "turnover_pct": 3.0,
                "change_pct": 2.0,
                "suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
            },
            source="unit.quote",
        ),
        _record(
            contract,
            "fund_flow",
            {
                "code": "000001",
                "main_net": 1000000,
                "large_net": 600000,
                "is_proxy": False,
                "eligible_for_hard_gate": True,
                "semantic_class": "formal_minute_main_force_flow",
                "timestamp_quality": "source_minute_event_time",
                "field_definition_version": "unit_formal_v1",
            },
            source="unit.formal.fund",
        ),
    ]
    for minute in range(39, 51):
        rows.append(
            _record(
                contract,
                "minute_bar",
                {
                    "code": "000001",
                    "open": 10.0,
                    "high": 10.3,
                    "low": 9.9,
                    "close": 10.2,
                    "volume": 1000 + minute,
                    "amount": (1000 + minute) * 10.2,
                },
                event_time=f"2026-07-30 14:{minute:02d}:00",
                source="unit.minute",
            )
        )
    for index, trade_day in enumerate(trade_dates):
        rows.append(
            _record(
                contract,
                "daily_bar",
                {
                    "code": "000001",
                    "date": trade_day,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10.0 + index * 0.001,
                    "volume": 100000 + index,
                    "adjustment": "qfq",
                },
                event_time=f"{trade_day} 15:00:00",
                source="unit.qfq",
            )
        )
    return rows


def _record(
    contract,
    data_type,
    payload,
    *,
    event_time="2026-07-30 14:50:00",
    available_at="2026-07-30 14:50:20",
    source,
):
    return build_point_in_time_record(
        payload,
        event_time=event_time,
        observed_at="2026-07-30 14:49:55",
        available_at=available_at,
        decision_cutoff=contract.decision_time,
        source=source,
        source_version="unit_v1",
        published_at=(
            event_time if data_type == "news" else None
        ),
        request={"source": source},
        raw=payload,
        data_type=data_type,
        time_contract=contract,
    ).as_dict()


def _source_status(contract, data_type, status):
    payload = {
        "source": f"unit.{data_type}",
        "status": status,
        "ok": status == "SUCCESS",
        "record_count": 0,
        "data_types": [data_type],
        "event_time": "2026-07-30T14:50:20+08:00",
        "observed_at": "2026-07-30T14:49:55+08:00",
        "available_at": "2026-07-30T14:50:20+08:00",
        "decision_cutoff": contract.decision_time,
        "source_version": "unit_status_v1",
        "raw_hash": "a" * 64,
    }
    payload.update(
        {
            "feature_event_cutoff": contract.feature_event_cutoff,
            "collection_deadline": contract.collection_deadline,
            "decision_time": contract.decision_time,
            "execution_not_before": contract.execution_not_before,
            "time_contract_version": contract.contract_version,
            "minute_label_semantics": (
                contract.minute_label_semantics
            ),
            "minute_label_validation_status": (
                contract.minute_label_validation_status
            ),
        }
    )
    return payload


def _trade_dates(count):
    current = date(2026, 7, 29)
    values = []
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current.isoformat())
        current -= timedelta(days=1)
    return sorted(values)


def _probe_samples(hashes):
    times = (
        "2026-07-30T14:49:55+08:00",
        "2026-07-30T14:50:05+08:00",
        "2026-07-30T14:50:30+08:00",
        "2026-07-30T14:51:05+08:00",
    )
    return [
        {
            "target_at": sampled_at,
            "sampled_at": sampled_at,
            "request_started_at": sampled_at,
            "request_completed_at": sampled_at,
            "request_elapsed_ms": 0,
            "requested_codes": ["000001"],
            "covered_codes": ["000001"],
            "presence_by_code": {
                "000001": value != "missing"
            },
            "signatures": (
                {}
                if value == "missing"
                else {
                    "000001": {
                        "ohlcv_hash": value,
                    }
                }
            ),
            "raw_response_hashes": ["a" * 64],
            "provider_raw_hash": "b" * 64,
            "source_versions": ["unit_minute_v1"],
            "sample_trade_date": TRADE_DATE,
            "error": "",
        }
        for sampled_at, value in zip(times, hashes)
    ]


class _AdvancingClock:
    def __init__(self, value):
        self.now = value

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=float(seconds))


class _ProbeCollectors:
    def __init__(self, codes, clock):
        self.codes = list(codes)
        self.clock = clock
        self.calls = 0

    def collect_minute_bars(self, observed_at):
        self.calls += 1
        values = [None, 1.0, 2.0, 2.0]
        value = values[self.calls - 1]
        rows = []
        for code in self.codes:
            minute = "14:49" if value is None else "14:50"
            close = 10.0 if value is None else value
            rows.append(
                {
                    "data_type": "minute_bar",
                    "event_time": (
                        f"{TRADE_DATE}T{minute}:00+08:00"
                    ),
                    "source": "unit_minute",
                    "source_version": "unit_minute_v1",
                    "raw_hash": (
                        f"{self.calls:x}" * 64
                    )[:64],
                    "payload": {
                        "code": code,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "volume": int(close),
                        "amount": int(close * 10),
                    },
                }
            )
        self.clock.advance(0.2)
        return ProviderBatch(
            records=rows,
            data_types=["minute_bar"],
            source_version="unit_minute_v1",
            raw_hash=(f"{self.calls:x}" * 64)[:64],
        )


class _UnitHttpResponse:
    def __init__(self, url):
        self.content = b"{}"
        self.status_code = 200
        self.url = url

    def raise_for_status(self):
        return None


class _SlowSession:
    def __init__(self, delays):
        self.delays = list(delays)
        self.calls = 0

    def request(self, method, url, **kwargs):
        index = self.calls
        self.calls += 1
        delay = (
            self.delays[index]
            if index < len(self.delays)
            else 0
        )
        if delay:
            time.sleep(delay)
        return _UnitHttpResponse(url)


class _RequestingProvider:
    def __init__(self, transport):
        self.transport = transport

    def collect(self, _):
        for index in range(2):
            self.transport.raise_if_cancelled()
            self.transport.request(
                "GET",
                f"https://unit.test/{index}",
            )
        return ProviderBatch(
            records=[],
            data_types=["quote"],
            source_version="unit_requester_v1",
        )


class _FundTransport:
    def __init__(self, *, primary_ok):
        self.primary_ok = primary_ok
        self.calls = []

    def request(
        self,
        method,
        url,
        *,
        params=None,
        data=None,
        headers=None,
    ):
        self.calls.append({"method": method, "url": url})
        if url == EASTMONEY_FUND_URL:
            if not self.primary_ok:
                raise RealSourceError("primary unavailable")
            value = {
                "data": {
                    "klines": [
                        (
                            "2026-07-30 14:50,100000,-20000,"
                            "10000,50000,50000"
                        )
                    ]
                }
            }
        elif url == SINA_FUND_URL:
            value = [
                {
                    "symbol": "sz000001",
                    "r0_net": "100000",
                    "netamount": "80000",
                }
            ]
        else:
            raise AssertionError(url)
        content = json.dumps(value).encode("utf-8")
        return RawHttpResponse(
            content=content,
            status_code=200,
            url=url,
            elapsed_ms=1.0,
        )


class _IndustryBackupTransport:
    def __init__(self):
        self.calls = []

    def request(
        self,
        method,
        url,
        *,
        params=None,
        data=None,
        headers=None,
    ):
        self.calls.append(url)
        if url.startswith(TENCENT_QUOTE_URL):
            values = [""] * 60
            values[1] = "上证指数"
            values[3] = "3500"
            values[4] = "3490"
            values[30] = "20260730145000"
            values[32] = "1.0"
            content = (
                'v_sh000001="' + "~".join(values) + '"'
            ).encode("gbk")
        elif url == EASTMONEY_INDUSTRY_MAP_URL:
            content = json.dumps(
                {
                    "ssbk": [
                        {
                            "BOARD_CODE": "1283",
                            "BOARD_NAME": "银行",
                            "BOARD_RANK": 1,
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")
        elif url == EASTMONEY_BOARD_URL:
            raise RealSourceError("primary board unavailable")
        elif url == EASTMONEY_INDUSTRY_LIST_URL:
            content = json.dumps(
                {
                    "data": {
                        "diff": [
                            {
                                "f12": "BK1283",
                                "f14": "银行",
                                "f3": 2.43,
                                "f104": 30,
                                "f105": 18,
                                "f106": 2,
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")
        else:
            raise AssertionError(url)
        return RawHttpResponse(
            content=content,
            status_code=200,
            url=url,
            elapsed_ms=1.0,
        )


def _assert_no_outputs(result):
    for field in ("candidates", "tickets", "orders"):
        assert result.get(field, []) == []
    assert result.get("shadow_candidates", []) == []
