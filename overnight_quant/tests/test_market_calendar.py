from datetime import datetime, timezone, timedelta

from overnight_quant.data.market_calendar import (
    AFTERNOON_SESSION,
    CALL_AUCTION,
    LUNCH_BREAK,
    MORNING_SESSION,
    NON_TRADING_DAY,
    TAIL_SESSION,
    get_session_state,
    is_likely_cn_trade_day,
    is_weekday,
)


CN = timezone(timedelta(hours=8))


def test_weekday_and_likely_trade_day_use_weekday_first_pass():
    assert is_weekday(datetime(2026, 5, 22, tzinfo=CN)) is True
    assert is_likely_cn_trade_day(datetime(2026, 5, 22, tzinfo=CN)) is True
    assert is_weekday(datetime(2026, 5, 23, tzinfo=CN)) is False
    assert is_likely_cn_trade_day(datetime(2026, 5, 23, tzinfo=CN)) is False


def test_session_state_boundaries():
    assert get_session_state(datetime(2026, 5, 23, 14, 30, tzinfo=CN)) == NON_TRADING_DAY
    assert get_session_state(datetime(2026, 5, 22, 9, 20, tzinfo=CN)) == CALL_AUCTION
    assert get_session_state(datetime(2026, 5, 22, 10, 0, tzinfo=CN)) == MORNING_SESSION
    assert get_session_state(datetime(2026, 5, 22, 12, 0, tzinfo=CN)) == LUNCH_BREAK
    assert get_session_state(datetime(2026, 5, 22, 14, 0, tzinfo=CN)) == AFTERNOON_SESSION
    assert get_session_state(datetime(2026, 5, 22, 14, 30, tzinfo=CN)) == TAIL_SESSION
