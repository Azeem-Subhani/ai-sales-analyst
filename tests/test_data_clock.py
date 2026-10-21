"""Data clock tests: which period a scheduled run reports on (docs/decisions.md, Week 3)."""

from datetime import date

import pytest

from src.reports import data_clock

DATA_END = date(2026, 9, 30)  # a Wednesday and a month-end


@pytest.mark.parametrize(
    ("today", "data_end", "expected"),
    [
        # Real today is weeks after the data ends: every run reports the last full periods.
        (
            date(2026, 10, 21),
            DATA_END,
            {"day": "2026-09-30", "week": "2026-09-27", "month": "2026-09-30"},
        ),
        # The run on the day after the data ends sees the same periods.
        (
            date(2026, 10, 1),
            DATA_END,
            {"day": "2026-09-30", "week": "2026-09-27", "month": "2026-09-30"},
        ),
        # Data ending on a Sunday mid-month: that week is complete, the month is not.
        (
            date(2026, 10, 21),
            date(2026, 9, 20),
            {"day": "2026-09-20", "week": "2026-09-20", "month": "2026-08-31"},
        ),
        # Today before the data end: yesterday wins (Monday run -> Sunday's data).
        (
            date(2026, 9, 14),
            DATA_END,
            {"day": "2026-09-13", "week": "2026-09-13", "month": "2026-08-31"},
        ),
        # No DATA_END_DATE (real data): plain yesterday on a Tuesday.
        (
            date(2026, 10, 20),
            None,
            {"day": "2026-10-19", "week": "2026-10-18", "month": "2026-09-30"},
        ),
        # No DATA_END_DATE, first of the month: last month is complete.
        (
            date(2026, 11, 1),
            None,
            {"day": "2026-10-31", "week": "2026-10-25", "month": "2026-10-31"},
        ),
        # Leap-year February end.
        (
            date(2028, 3, 1),
            None,
            {"day": "2028-02-29", "week": "2028-02-27", "month": "2028-02-29"},
        ),
    ],
)
def test_reporting_as_of(today: date, data_end: date | None, expected: dict[str, str]) -> None:
    for kind, as_of in expected.items():
        assert data_clock.reporting_as_of(kind, today, data_end) == date.fromisoformat(as_of)


def test_weekly_as_of_is_always_a_sunday() -> None:
    for offset in range(14):
        day = date(2026, 10, 1 + offset)
        assert data_clock.reporting_as_of("week", day, None).weekday() == 6


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        data_clock.reporting_as_of("quarter", date(2026, 10, 21), DATA_END)


def test_data_end_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_END_DATE", "2026-09-30")
    assert data_clock.data_end_from_env() == DATA_END
    monkeypatch.setenv("DATA_END_DATE", "")
    assert data_clock.data_end_from_env() is None
    monkeypatch.setenv("DATA_END_DATE", "30/09/2026")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        data_clock.data_end_from_env()
