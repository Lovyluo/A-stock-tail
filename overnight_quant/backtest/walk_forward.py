from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable


@dataclass(frozen=True)
class WalkForwardWindow:
    train_dates: tuple[date, ...]
    validation_dates: tuple[date, ...]
    test_dates: tuple[date, ...]
    purge_dates: tuple[date, ...]
    embargo_dates: tuple[date, ...]


def build_walk_forward_windows(
    trading_dates: Iterable[str | date],
    *,
    train_months: int = 36,
    validation_months: int = 6,
    test_months: int = 6,
    purge_days: int = 5,
    embargo_days: int = 5,
    step_months: int = 1,
    seal_recent_months: int = 12,
) -> dict:
    dates = sorted({_as_date(item) for item in trading_dates})
    if not dates:
        return {"windows": [], "sealed_dates": []}
    seal_start = _add_months(dates[-1], -seal_recent_months)
    research_dates = [day for day in dates if day < seal_start]
    sealed_dates = [day for day in dates if day >= seal_start]
    windows: list[WalkForwardWindow] = []
    cursor = _add_months(research_dates[0], train_months) if research_dates else dates[-1]
    last_research = research_dates[-1] if research_dates else dates[-1]
    while True:
        validation_start = cursor
        test_start = _add_months(validation_start, validation_months)
        test_end = _add_months(test_start, test_months)
        if test_end > _add_months(last_research, 1):
            break
        train_start = _add_months(validation_start, -train_months)
        train = [day for day in research_dates if train_start <= day < validation_start]
        validation_all = [day for day in research_dates if validation_start <= day < test_start]
        test_all = [day for day in research_dates if test_start <= day < test_end]
        purge = tuple(validation_all[:purge_days])
        embargo = tuple(test_all[:embargo_days])
        validation = tuple(validation_all[purge_days:])
        test = tuple(test_all[embargo_days:])
        if train and validation and test:
            windows.append(WalkForwardWindow(tuple(train), validation, test, purge, embargo))
        cursor = _add_months(cursor, step_months)
    return {"windows": windows, "sealed_dates": sealed_dates}


class SealedHoldout:
    def __init__(self, dates: Iterable[str | date]) -> None:
        self._dates = tuple(sorted({_as_date(item) for item in dates}))
        self._opened = False

    def open_once(self) -> tuple[date, ...]:
        if self._opened:
            raise RuntimeError("SEALED_HOLDOUT_ALREADY_OPENED")
        self._opened = True
        return self._dates

    @property
    def opened(self) -> bool:
        return self._opened


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    max_day = [31, 29 if _leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(value.day, max_day))


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _as_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))
