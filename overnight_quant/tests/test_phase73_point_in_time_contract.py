from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import random

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
UNIT_CLOSED_DATES = {"2026-05-01"}


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
    assert readiness["normalized_snapshot"]["record_normalization_audit"][
        "duplicate_counts"
    ]["daily_bar"] == 59
    assert any(
        row.get("pit_reject_reason")
        == "superseded_duplicate_daily_bar"
        for row in readiness["normalized_snapshot"]["rejected_records"]
    )
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
    assert normalized["record_normalization_audit"][
        "input_order_changed"
    ] is True


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


def test_trading_calendar_record_is_covered_by_snapshot_hash():
    records = _complete_records()
    changed = [
        row
        for row in records
        if row["data_type"] != "trading_calendar"
    ]
    changed.append(_calendar_record(_prior_trading_dates()[:-1]))

    assert _effective_hash(records) != _effective_hash(changed)


def test_source_status_order_does_not_change_snapshot_hash():
    news_status = _source_status()
    market_status = {
        **_source_status(raw_hash="market-state"),
        "source": "unit.market",
        "data_type": "market",
    }
    records = _complete_records()

    first = close_snapshot_hash(
        decision_time=DECISION_TIME,
        records=records,
        source_status=[news_status, market_status],
    )
    second = close_snapshot_hash(
        decision_time=DECISION_TIME,
        records=records,
        source_status=[market_status, news_status],
    )

    assert first == second


def test_1449_event_arriving_at_1450_cannot_replace_1450_minute():
    records = [
        row
        for row in _complete_records()
        if not (
            row["data_type"] == "minute_bar"
            and row["event_time"][11:16] == "14:50"
        )
    ]
    records.append(
        _record(
            "minute_bar",
            {
                "code": "000001",
                "price": 99.0,
                "open": 99.0,
                "high": 99.0,
                "low": 99.0,
                "volume": 9999,
                "amount": 989901,
            },
            "14:50",
            event_time=f"{TRADE_DATE} 14:49",
        )
    )

    result = _evaluate(_snapshot(records))

    assert result["data_ready"] is False
    assert "000001:minute_bar_1450_event_missing" in result[
        "readiness_errors"
    ]
    _assert_no_outputs(result)


def test_twelve_duplicate_event_minutes_do_not_meet_minute_count():
    records = [
        row
        for row in _complete_records()
        if row["data_type"] != "minute_bar"
    ]
    for index in range(12):
        records.append(
            _record(
                "minute_bar",
                {
                    "code": "000001",
                    "price": 10.0 + index * 0.01,
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "volume": 1000 + index,
                    "amount": 10000 + index,
                },
                "14:50",
            )
        )

    result = _evaluate(_snapshot(records))
    readiness = _readiness(records)

    assert result["data_ready"] is False
    assert "000001:minute_bar_count_below_minimum" in result[
        "readiness_errors"
    ]
    assert readiness["stock_readiness"]["000001"]["minute_bar_count"] == 1
    assert readiness["normalized_snapshot"]["record_normalization_audit"][
        "duplicate_counts"
    ]["minute_bar"] == 11
    _assert_no_outputs(result)


def test_quote_order_does_not_change_readiness_score_or_hash():
    latest = _complete_records()
    older_quote = _record(
        "quote",
        {
            "code": "000001",
            "name": "older",
            "price": 8.0,
            "prev_close": 7.9,
            "change_pct": 1.0,
            "amount_wan": 20000,
            "turnover_pct": 6.0,
            "suspended": False,
            "is_limit_up": False,
            "is_limit_down": False,
            "industry_name": "bank",
        },
        "14:48",
    )

    _assert_same_decision(
        [older_quote, *latest],
        [*latest, older_quote],
    )


def test_market_industry_and_fund_flow_order_do_not_change_decision():
    records = _complete_records()
    extras = [
        _record(
            "market",
            {"index_change_pct": -1.0, "breadth_ratio": 0.2},
            "14:45",
        ),
        _record(
            "industry",
            {
                "name": "bank",
                "change_pct": -1.0,
                "relative_strength_pct": -0.5,
                "breadth_ratio": 0.2,
            },
            "14:45",
        ),
        _record(
            "fund_flow",
            {
                "code": "000001",
                "main_net": -500000,
                "large_net": -200000,
            },
            "14:41",
        ),
    ]

    _assert_same_decision(
        [*extras, *records],
        [*records, *reversed(extras)],
    )


def test_random_record_shuffle_preserves_all_decision_invariants():
    records = _complete_records()
    shuffled = deepcopy(records)
    random.Random(20260730).shuffle(shuffled)

    _assert_same_decision(records, shuffled)


def test_sixty_saturdays_cannot_satisfy_trading_calendar():
    records = _complete_records(include_daily=False)
    current = date.fromisoformat(TRADE_DATE) - timedelta(days=1)
    saturdays = []
    while len(saturdays) < 60:
        if current.weekday() == 5:
            saturdays.append(current.isoformat())
        current -= timedelta(days=1)
    records.extend(
        _daily_record(day, index=index)
        for index, day in enumerate(reversed(saturdays))
    )

    result = _evaluate(_snapshot(records))

    assert result["data_ready"] is False
    assert "000001:daily_bar_history_insufficient" in result[
        "readiness_errors"
    ]
    _assert_no_outputs(result)


def test_calendar_claiming_weekends_is_rejected():
    records = [
        row
        for row in _complete_records(include_daily=False)
        if row["data_type"] != "trading_calendar"
    ]
    current = date.fromisoformat(TRADE_DATE) - timedelta(days=1)
    saturdays = []
    while len(saturdays) < 60:
        if current.weekday() == 5:
            saturdays.append(current.isoformat())
        current -= timedelta(days=1)
    records.append(_calendar_record(saturdays))
    records.extend(
        _daily_record(day, index=index)
        for index, day in enumerate(reversed(saturdays))
    )

    result = _evaluate(_snapshot(records))
    readiness = _readiness(records)
    calendar_contract = readiness["trading_calendar_contract"]

    assert result["data_ready"] is False
    assert "trading_calendar_missing_or_invalid" in result[
        "readiness_errors"
    ]
    assert calendar_contract["available"] is False
    assert "trading_calendar_weekend_dates_invalid" in calendar_contract[
        "errors"
    ]
    _assert_no_outputs(result)


def test_declared_market_closure_cannot_count_toward_sixty_days():
    records = [
        row
        for row in _complete_records()
        if not (
            row["data_type"] == "daily_bar"
            and (row.get("payload") or {}).get("date")
            == _prior_trading_dates()[0]
        )
    ]
    records.append(
        _daily_record("2026-05-01", index=0)
    )

    result = _evaluate(_snapshot(records))
    readiness = _readiness(records)
    daily_rejections = readiness["stock_readiness"]["000001"][
        "daily_bar_audit"
    ]["rejected"]

    assert result["data_ready"] is False
    assert any(
        row.get("pit_reject_reason") == "daily_date_not_confirmed_open"
        for row in daily_rejections
    )
    _assert_no_outputs(result)


def test_missing_trusted_trading_calendar_is_not_data_ready():
    records = [
        row
        for row in _complete_records()
        if row["data_type"] != "trading_calendar"
    ]

    result = _evaluate(_snapshot(records))

    assert result["data_ready"] is False
    assert "trading_calendar_missing_or_invalid" in result[
        "readiness_errors"
    ]
    assert result["critical_source_status"]["trading_calendar"][
        "status"
    ] == "MISSING"
    _assert_no_outputs(result)


def test_provider_completing_after_cutoff_cannot_enter_collection(tmp_path):
    quote = next(
        row
        for row in _complete_records()
        if row["data_type"] == "quote"
    )
    collector = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path),
        {"unit.quote": lambda started_at: [quote]},
        clock=_clock(
            "2026-07-30T14:49:50+08:00",
            "2026-07-30T14:50:01+08:00",
        ),
    )

    result = collector.collect(
        datetime.fromisoformat("2026-07-30T14:49:50+08:00")
    )

    assert result["status"] == "NO_VALID_RECORDS"
    assert result["records"] == []
    assert result["rejected_records"][0]["pit_reject_reason"] == (
        "available_after_decision"
    )
    assert result["rejected_source_status"][0]["pit_reject_reason"] == (
        "event_after_decision"
    )
    assert not any(tmp_path.rglob("*"))


def test_provider_completing_before_cutoff_enters_collection(tmp_path):
    quote = next(
        row
        for row in _complete_records()
        if row["data_type"] == "quote"
    )
    collector = CloseWindowCollector(
        ImmutableSnapshotStore(tmp_path),
        {"unit.quote": lambda started_at: [quote]},
        clock=_clock(
            "2026-07-30T14:49:00+08:00",
            "2026-07-30T14:49:30+08:00",
        ),
    )

    result = collector.collect(
        datetime.fromisoformat("2026-07-30T14:49:00+08:00")
    )

    assert result["status"] == "COLLECTED"
    assert len(result["records"]) == 1
    assert result["records"][0]["available_at"] == (
        "2026-07-30T14:49:30+08:00"
    )
    assert result["source_status"][0]["started_at"] == (
        "2026-07-30T14:49:00+08:00"
    )
    assert result["source_status"][0]["completed_at"] == (
        "2026-07-30T14:49:30+08:00"
    )
    assert result.get("path")


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
        _calendar_record(),
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


def _calendar_record(
    trade_dates: list[str] | None = None,
) -> dict:
    dates = trade_dates or _prior_trading_dates()
    return _record(
        "trading_calendar",
        {
            "calendar_kind": "benchmark_index_trade_dates",
            "calendar_name": "unit_sh000001_trade_dates",
            "trade_dates": dates,
            "latest_completed_trade_date": max(dates),
        },
        "14:20",
    )


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
        if (
            current.weekday() < 5
            and current.isoformat() not in UNIT_CLOSED_DATES
        ):
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


def _assert_same_decision(
    first_records: list[dict],
    second_records: list[dict],
) -> None:
    first_readiness = _readiness(first_records)
    second_readiness = _readiness(second_records)
    first_result = _evaluate(_snapshot(first_records))
    second_result = _evaluate(_snapshot(second_records))

    assert first_readiness["data_ready"] == second_readiness["data_ready"]
    assert first_readiness["readiness_errors"] == second_readiness[
        "readiness_errors"
    ]
    assert first_readiness["coverage_by_type"] == second_readiness[
        "coverage_by_type"
    ]
    assert first_readiness["eligible_stock_codes"] == second_readiness[
        "eligible_stock_codes"
    ]
    assert first_result["scored"][0]["decision_hash"] == second_result[
        "scored"
    ][0]["decision_hash"]
    assert _effective_hash(first_records) == _effective_hash(second_records)


def _clock(*values: str):
    moments = iter(datetime.fromisoformat(value) for value in values)
    return lambda: next(moments)


def _assert_no_outputs(result: dict) -> None:
    assert result.get("shadow_candidates") == []
    assert result.get("selected") == []
    assert result.get("tickets") == []
    assert result.get("orders") == []
