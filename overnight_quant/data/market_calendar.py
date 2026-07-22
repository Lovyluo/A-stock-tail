from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone


CN_TZ = timezone(timedelta(hours=8))

PRE_MARKET = "PRE_MARKET"
CALL_AUCTION = "CALL_AUCTION"
MORNING_SESSION = "MORNING_SESSION"
LUNCH_BREAK = "LUNCH_BREAK"
AFTERNOON_SESSION = "AFTERNOON_SESSION"
TAIL_SESSION = "TAIL_SESSION"
AFTER_CLOSE = "AFTER_CLOSE"
NON_TRADING_DAY = "NON_TRADING_DAY"


def is_weekday(value: date | datetime) -> bool:
    day = value.date() if isinstance(value, datetime) else value
    return day.weekday() < 5


def is_likely_cn_trade_day(value: date | datetime) -> bool:
    return is_weekday(value)


def previous_likely_cn_trade_day(value: date | datetime) -> date:
    current = (value.date() if isinstance(value, datetime) else value) - timedelta(days=1)
    while not is_likely_cn_trade_day(current):
        current -= timedelta(days=1)
    return current


def effective_after_close_trade_day(value: datetime) -> date | None:
    """Trading day that a live after-close run should summarize.

    Before the market opens, the previous trading day is still the effective
    after-close session. This lets Monday pre-market also behave as Friday
    after-close for review/watchlist generation.
    """
    current = value if value.tzinfo else value.replace(tzinfo=CN_TZ)
    current = current.astimezone(CN_TZ)
    if not is_likely_cn_trade_day(current):
        return None
    session = get_session_state(current)
    if session in {PRE_MARKET, CALL_AUCTION}:
        return previous_likely_cn_trade_day(current)
    if session == AFTER_CLOSE:
        return current.date()
    return None


def effective_after_close_observation_trade_day(value: datetime, live_start: str = "14:50") -> date | None:
    """Trade date available to the after-close observation workflow.

    The after-close strategy may start shortly before the close, but remains
    separate from the 14:25-14:55 tail scan. Pre-market and non-trading-day
    runs replay the previous likely trading day.
    """
    current = value if value.tzinfo else value.replace(tzinfo=CN_TZ)
    current = current.astimezone(CN_TZ)
    if not is_likely_cn_trade_day(current):
        return previous_likely_cn_trade_day(current)
    if get_session_state(current) in {PRE_MARKET, CALL_AUCTION}:
        return previous_likely_cn_trade_day(current)
    hour, minute = (int(item) for item in str(live_start).split(":")[:2])
    if current.time() >= time(hour, minute):
        return current.date()
    return None


def get_session_state(now: datetime | None = None) -> str:
    current = now or datetime.now(CN_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CN_TZ)
    current = current.astimezone(CN_TZ)
    if not is_likely_cn_trade_day(current):
        return NON_TRADING_DAY

    current_time = current.time()
    if current_time < time(9, 15):
        return PRE_MARKET
    if time(9, 15) <= current_time < time(9, 30):
        return CALL_AUCTION
    if time(9, 30) <= current_time < time(11, 30):
        return MORNING_SESSION
    if time(11, 30) <= current_time < time(13, 0):
        return LUNCH_BREAK
    if time(14, 25) <= current_time <= time(14, 55):
        return TAIL_SESSION
    if time(13, 0) <= current_time < time(15, 0):
        return AFTERNOON_SESSION
    return AFTER_CLOSE
