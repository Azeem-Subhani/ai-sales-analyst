"""Which period a scheduled run reports on (the "data clock", docs/decisions.md Week 3).

The dummy data ends on DATA_END_DATE (2026-09-30), but the scheduler runs on real
dates, when "yesterday" has no data. So every run reports the latest *complete*
period on or before a reference date:

    reference = min(yesterday, DATA_END_DATE)   if DATA_END_DATE is set
    reference = yesterday                       otherwise (real data)

    daily   -> the reference date itself
    weekly  -> the last Sunday on or before it (weeks run Monday to Sunday)
    monthly -> the last month-end on or before it

"Yesterday" is taken in TIMEZONE, so a 07:00 run in New York and one in Karachi
each mean their own local yesterday.
"""

import calendar
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.analytics import kpis

DATA_END_ENV = "DATA_END_DATE"
TIMEZONE_ENV = "TIMEZONE"
DEFAULT_TIMEZONE = "UTC"


def data_end_from_env() -> date | None:
    """DATA_END_DATE as a date, or None when unset or empty (real dates)."""
    value = os.environ.get(DATA_END_ENV, "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{DATA_END_ENV} must be YYYY-MM-DD; got {value!r}.") from error


def timezone_name() -> str:
    """TIMEZONE from the environment, falling back to UTC."""
    return os.environ.get(TIMEZONE_ENV, "").strip() or DEFAULT_TIMEZONE


def today_in(tz_name: str) -> date:
    """Today's date in the given IANA time zone."""
    return datetime.now(ZoneInfo(tz_name)).date()


def reference_date(today: date, data_end: date | None) -> date:
    """The latest day with data: yesterday, capped at data_end when it is set."""
    yesterday = today - timedelta(days=1)
    return min(yesterday, data_end) if data_end else yesterday


def last_sunday_on_or_before(day: date) -> date:
    """The Sunday ending the latest full Monday-to-Sunday week up to day."""
    return day - timedelta(days=(day.weekday() - kpis.SUNDAY) % kpis.DAYS_IN_WEEK)


def last_month_end_on_or_before(day: date) -> date:
    """day itself if it is a month-end, otherwise the last day of the previous month."""
    if day.day == calendar.monthrange(day.year, day.month)[1]:
        return day
    return day.replace(day=1) - timedelta(days=1)


def reporting_as_of(kind: str, today: date, data_end: date | None) -> date:
    """The as_of date (period end) a run on `today` should report for a period kind."""
    reference = reference_date(today, data_end)
    if kind == "day":
        return reference
    if kind == "week":
        return last_sunday_on_or_before(reference)
    if kind == "month":
        return last_month_end_on_or_before(reference)
    raise ValueError(f"Unknown period kind {kind!r}; expected one of {kpis.PERIOD_KINDS}.")
