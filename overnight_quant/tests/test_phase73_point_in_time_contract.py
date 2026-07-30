from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from overnight_quant.backtest.point_in_time_provider import PointInTimeProvider
from overnight_quant.data.close_confirmation_readiness import (
    normalize_close_confirmation_snapshot,
    validate_close_confirmation_readiness,
)
from overnight_quant.data.point_in_time import (
    build_point_in_time_record,
    records_available_at,
)
from overnight_quant.data.snapshot_store import (
    CloseWindowCollector,
    ImmutableSnapshotStore,
    close_snapshot_hash,
)
from overnight_quant.strategy.close_confirmation_v1.strategy import (
    CloseConfirmationStrategy,
)


TRADE_DATE = "2026-07-30"
DECISION_TIME = f"{TRADE_DATE}T14:50:00+08:00"


def test_late_news_source_success_cannot_make_snapshot_ready():
    result = _evaluate(
        _snapshot(
            _complete_records(),
            [_source_status(clock="14:51", cutoff="15:00")],
        )
    )

    assert result["status"] == "POINT_IN_TIME_DATA_INCOMPLETE"
    assert result["data_ready"] is False
    assert result["critical_source_status"]["news"]["status"] == "MISSING"
    assert "news_source_status_missing" in result["readiness_errors"]
    _assert_no_outputs(result)


def test_post_decision_daily_bar_cannot_change_score_or_decision_hash():
    records = _complete_records()
    base = _evaluate(_snapshot(records))
    changed_records = deepcopy(records)
    changed_records.append(
        _record(
            "daily_bar",
            {
                "code": "000001",
                "date": "2026-07-29",
                "high": 99.0,
                "low": 1.0,
                "close": 88.0,
                "volume": 999999999,
                "adjustment": "qfq",
            },
            "14:51",
            cutoff="15:00",
            event_time="2026-07-29 15:00",
        )
    )
    changed = _evaluate(_snapshot(changed_records))

    assert base["scored"][0]["decision_hash"] == changed["scored"][0]["decision_hash"]
    assert _effective_hash(records) == _effective_hash(changed_records)


def test_1451_fund_flow_cannot_change_score():
    records = _complete_records()
    base = _evaluate(_snapshot(records))
    changed_records = deepcopy(records)
    changed_records.append(
        _record(
            "fund_flow",
            {
                "code": "000001",
                "main_net": 999999999,
                "large_net": 999999999,
            },
            "14:51",
            cutoff="15:00",
        )
    )
    changed = _evaluate(_snapshot(changed_records))

    assert base["scored"][0]["decision_hash"] == changed["scored"][0]["decision_hash"]


def test_sixty_duplicate_daily_dates_do_not_satisfy_history_contract():
    records = _complete_records(include_daily=False)
    for index in range(60):
        records.append(
            _daily_record(
                "2026-07-29",
                index=index,
                observed_clock="14:30",
            )
        )

    result = _evaluate(_snapshot(records))
    readiness = _readiness(records)

    assert result["data_ready"] is False
    assert "000001:daily_bar_history_insufficient" in result["readiness_errors"]
    assert readiness["stock_readiness"]["000001"]["daily_bar_audit"][
        "duplicate_dates"
    ] == ["2026-07-29"]
    _assert_no_outputs(result)


def test_fifty_nine_unique_daily_dates_do_not_satisfy_history_contract():
    records = _complete_records(include_daily=False)
    records.extend(
        _daily_record(day, index=index)
        for index, day in enumerate(_prior_trading_dates()[-59:])
    )

    result = _evaluate(_snapshot(records))

    assert result["data_ready"] is False
    assert "000001:daily_bar_history_insufficient" in result["readiness_errors"]
    _assert_no_outputs(result)


def test_unknown_or_unadjusted_daily_data_cannot_enable_chip_dimension():
    for adjustment in ("", "none"):
        records = _complete_records(include_daily=False)
        records.extend(
            _daily_record(
                day,
                index=index,
                adjustment=adjustment,
            )
            for index, day in enumerate(_prior_trading_dates())
        )

        result = _evaluate(_snapshot(records))

        assert result["data_ready"] is False
        assert any(
            error in result["readiness_errors"]
            for error in (
                "000001:daily_adjustment_mixed_or_unknown",
                "000001:daily_adjustment_not_qfq",
            )
        )
        _assert_no_outputs(result)


def test_unsorted_daily_bars_are_normalized_deterministically():
    ordered = _complete_records()
    daily = [row for row in ordered if row["data_type"] == "daily_bar"]
    non_daily = [row for row in ordered if row["data_type"] != "daily_bar"]
    shuffled = [*non_daily, *reversed(daily)]

    ordered_result = _evaluate(_snapshot(ordered))
    shuffled_result = _evaluate(_snapshot(shuffled))
    normalized = normalize_close_confirmation_snapshot(
        _snapshot(shuffled),
        decision_time=DECISION_TIME,
    )
    dates = [
        row["date"]
        for row in normalized["stocks"][0]["daily_bars"]
    ]

    assert dates == sorted(dates)
    assert ordered_result["scored"][0]["decision_hash"] == (
        shuffled_result["scored"][0]["decision_hash"]
    )
    assert "daily_bars_reordered" in normalized["stocks"][0][
        "daily_bar_audit"
    ]["warnings"]


def test_current_day_completed_close_bar_is_prohibited_at_1450():
    records = _complete_records(include_daily=False)
    prior_dates = _prior_trading_dates()[-59:]
    records.extend(
        _daily_record(day, index=index)
        for index, day in enumerate(prior_dates)
    )
    records.append(
        _daily_record(
            TRADE_DATE,
            index=59,
            observed_clock="14:40",
            event_time=f"{TRADE_DATE} 14:40",
        )
    )

    result = _evaluate(_snapshot(records))
    readiness = _readiness(records)
    rejected = readiness["stock_readiness"]["000001"]["daily_bar_audit"][
        "rejected"
    ]

    assert result["data_ready"] is False
    assert any(
        row.get("pit_reject_reason") == "current_or_future_daily_bar_prohibited"
        for row in rejected
    )
    _assert_no_outputs(result)


def test_snapshot_hash_includes_effective_source_status_and_late_status_is_audit_only(
    tmp_path,
):
    records = _complete_records()
    first_status = _source_status(raw_hash="news-state-a")
    changed_status = _source_status(raw_hash="news-state-b")
    first_hash = close_snapshot_hash(
        decision_time=DECISION_TIME,
        records=records,
        source_status=[first_status],
    )
    changed_hash = close_snapshot_hash(
        decision_time=DECISION_TIME,
        records=records,
        source_status=[changed_status],
    )
    assert first_hash != changed_hash

    collector = CloseWindowCollector(ImmutableSnapshotStore(tmp_path), {})
    first = collector.freeze(
        TRADE_DATE,
        records,
        source_status=[first_status],
    )
    repeated = collector.freeze(
        TRADE_DATE,
        records,
        source_status=[
            first_status,
            _source_status(
                clock="14:51",
                cutoff="15:00",
                raw_hash="late-news-state",
            ),
        ],
    )

    assert first["snapshot_hash"] == repeated["snapshot_hash"]
    assert repeated["rejected_source_status"][0]["pit_reject_reason"] == (
        "event_after_decision"
    )
    assert repeated.get("source_status_audit_path")


def test_complete_records_only_snapshot_still_scores():
    result = _evaluate(_snapshot(_complete_records()))

    assert result["data_ready"] is True
    assert result["readiness_errors"] == []
    assert len(result["scored"]) == 1
    assert result["status"] in {
        "SHADOW_SIMULATION_READY",
        "NO_SHADOW_CONFIRMATION",
    }


def test_incomplete_contract_keeps_all_execution_outputs_empty():
    records = [
        row
        for row in _complete_records()
        if row["data_type"] != "industry"
    ]
    result = _evaluate(_snapshot(records))

    assert result["status"] == "POINT_IN_TIME_DATA_INCOMPLETE"
    assert result["execution_ok"] is True
    assert result["data_ready"] is False
    _assert_no_outputs(result)


def _snapshot(
    records: list[dict],
    source_status: list[dict] | None = None,
) -> dict:
    return {
        "status": "FROZEN_1450",
        "trade_date": TRADE_DATE,
        "decision_time": DECISION_TIME,
        "records": records,
        "source_status": (
            source_status
            if source_status is not None
            else [_source_status()]
        ),
    }


def _complete_records(*, include_daily: bool = True) -> list[dict]:
    records = [
        _record(
            "market",
            {"index_change_pct": 0.5, "breadth_ratio": 0.62},
            "14:49",
        ),
        _record(
            "industry",
            {
                "name": "bank",
                "change_pct": 1.0,
                "relative_strength_pct": 0.8,
                "breadth_ratio": 0.68,
            },
            "14:49",
        ),
        _record(
            "quote",
            {
                "code": "000001",
                "name": "sample",
                "price": 10.11,
                "prev_close": 9.8,
                "change_pct": 3.0,
                "amount_wan": 30000,
                "turnover_pct": 8.0,
                "suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
                "industry_name": "bank",
            },
            "14:49",
        ),
    ]
    for minute in range(39, 51):
        price = 10.0 + (minute - 39) * 0.01
        records.append(
            _record(
                "minute_bar",
                {
                    "code": "000001",
                    "price": price,
                    "open": price,
                    "high": price + 0.01,
                    "low": price - 0.01,
                    "volume": 1000 + minute,
                    "amount": price * (1000 + minute),
                    "bid_vol1": 500,
                    "ask_vol1": 300,
                },
                f"14:{minute:02d}",
            )
        )
    if include_daily:
        records.extend(
            _daily_record(day, index=index)
            for index, day in enumerate(_prior_trading_dates())
        )
    records.append(
        _record(
            "fund_flow",
            {
                "code": "000001",
                "main_net": 1000000,
                "large_net": 600000,
            },
            "14:40",
        )
    )
    return records


def _daily_record(
    day: str,
    *,
    index: int,
    adjustment: str = "qfq",
    observed_clock: str = "14:30",
    event_time: str | None = None,
) -> dict:
    close = 9.0 + index * 0.02
    return _record(
        "daily_bar",
        {
            "code": "000001",
            "date": day,
            "close": close,
            "high": close + 0.05,
            "low": close - 0.05,
            "volume": 100000 + index * 1000,
            "adjustment": adjustment,
        },
        observed_clock,
        event_time=event_time or f"{day} 15:00",
    )


def _record(
    data_type: str,
    payload: dict,
    clock: str,
    *,
    cutoff: str = "14:50",
    event_time: str | None = None,
) -> dict:
    event = event_time or f"{TRADE_DATE} {clock}"
    return build_point_in_time_record(
        payload,
        event_time=event,
        observed_at=f"{TRADE_DATE} {clock}",
        available_at=f"{TRADE_DATE} {clock}",
        decision_cutoff=f"{TRADE_DATE} {cutoff}",
        source=f"unit.{data_type}",
        source_version="1",
        request={"data_type": data_type},
        raw=payload,
        data_type=data_type,
    ).as_dict()


def _source_status(
    *,
    clock: str = "14:40",
    cutoff: str = "14:50",
    raw_hash: str = "news-state",
) -> dict:
    instant = f"{TRADE_DATE}T{clock}:00+08:00"
    return {
        "source": "unit.news",
        "data_type": "news",
        "status": "SUCCESS",
        "record_count": 0,
        "event_time": instant,
        "observed_at": instant,
        "available_at": instant,
        "decision_cutoff": f"{TRADE_DATE}T{cutoff}:00+08:00",
        "source_version": "1",
        "raw_hash": raw_hash,
    }


def _prior_trading_dates() -> list[str]:
    current = date.fromisoformat(TRADE_DATE) - timedelta(days=1)
    days = []
    while len(days) < 60:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current -= timedelta(days=1)
    return list(reversed(days))


def _evaluate(snapshot: dict) -> dict:
    frozen = PointInTimeProvider([snapshot]).snapshot_at(
        TRADE_DATE,
        "14:50",
    )
    return CloseConfirmationStrategy({"min_shadow_score": 0}).evaluate_snapshot(
        frozen,
        mode="shadow",
    )


def _readiness(records: list[dict]) -> dict:
    frozen = PointInTimeProvider([_snapshot(records)]).snapshot_at(
        TRADE_DATE,
        "14:50",
    )
    return validate_close_confirmation_readiness(frozen)


def _effective_hash(records: list[dict]) -> str:
    accepted, _ = records_available_at(records, DECISION_TIME)
    return close_snapshot_hash(
        decision_time=DECISION_TIME,
        records=accepted,
        source_status=[_source_status()],
    )


def _assert_no_outputs(result: dict) -> None:
    assert result.get("shadow_candidates") == []
    assert result.get("selected") == []
    assert result.get("tickets") == []
    assert result.get("orders") == []
