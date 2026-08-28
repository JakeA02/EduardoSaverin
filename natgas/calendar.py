"""EIA storage week alignment utilities."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def week_ending_friday(d: date) -> date:
    """Return the Friday ending the storage week containing `d`."""
    days_to_friday = (4 - d.weekday()) % 7
    if d.weekday() == 4:
        return d
    if d.weekday() > 4:
        days_to_friday -= 7
    return d + timedelta(days=days_to_friday)


def storage_week_start(week_ending: date) -> date:
    """Storage week runs Saturday through Friday."""
    return week_ending - timedelta(days=6)


def eia_storage_week_ending(d: date) -> date:
    """Friday ending the EIA storage week that contains `d` (Saturday–Friday)."""
    if d.weekday() == 5:  # Saturday -> next Friday
        return d + timedelta(days=6)
    if d.weekday() == 6:  # Sunday -> next Friday
        return d + timedelta(days=5)
    return week_ending_friday(d)


def last_completed_friday(d: date) -> date:
    if d.weekday() == 4:
        return d
    return d - timedelta(days=(d.weekday() - 4) % 7)


def current_target_week(now: datetime | None = None) -> date:
    """Unreported (or in-progress) storage week as of `now` in ET."""
    now = now or datetime.now(tz=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)
    completed = last_completed_friday(now.date())
    if now < report_release_datetime(completed):
        return completed
    return completed + timedelta(days=7)


def storage_week_days(week_ending: date) -> list[date]:
    start = storage_week_start(week_ending)
    return [start + timedelta(days=i) for i in range(7)]


def report_release_datetime(week_ending: date) -> datetime:
    """EIA WNGSR releases Thursday 10:30 ET for the prior Friday week-ending."""
    days_after = (3 - week_ending.weekday()) % 7
    if week_ending.weekday() == 3:
        release_date = week_ending
    else:
        release_date = week_ending + timedelta(days=days_after if days_after else 7)
    return datetime(release_date.year, release_date.month, release_date.day, 10, 30, tzinfo=ET)


def week_ending_range(start: date, end: date) -> list[date]:
    """All Friday week-ending dates between start and end inclusive."""
    current = week_ending_friday(start)
    end_friday = week_ending_friday(end)
    weeks: list[date] = []
    while current <= end_friday:
        weeks.append(current)
        current += timedelta(days=7)
    return weeks
