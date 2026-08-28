"""Tests for week alignment logic."""

from datetime import date

import pytest

from natgas.calendar import (
    current_target_week,
    eia_storage_week_ending,
    last_completed_friday,
    report_release_datetime,
    storage_week_start,
    week_ending_friday,
)


@pytest.mark.parametrize(
    "input_date, expected_friday",
    [
        (date(2026, 8, 24), date(2026, 8, 28)),  # Monday -> that week's Friday
        (date(2026, 8, 28), date(2026, 8, 28)),  # Friday
        (date(2026, 8, 29), date(2026, 8, 28)),  # Saturday -> prior Friday
        (date(2026, 8, 30), date(2026, 8, 28)),  # Sunday -> prior Friday
    ],
)
def test_week_ending_friday(input_date: date, expected_friday: date) -> None:
    assert week_ending_friday(input_date) == expected_friday


def test_storage_week_span() -> None:
    week_end = date(2026, 8, 28)
    assert storage_week_start(week_end) == date(2026, 8, 22)


def test_report_release_after_friday_week() -> None:
    week_end = date(2026, 8, 28)  # Friday
    release = report_release_datetime(week_end)
    assert release.date() == date(2026, 9, 3)  # following Thursday
    assert release.hour == 10 and release.minute == 30


def test_eia_storage_week_saturday_starts_new_week() -> None:
    assert eia_storage_week_ending(date(2026, 8, 22)) == date(2026, 8, 28)
    assert eia_storage_week_ending(date(2026, 8, 29)) == date(2026, 9, 4)


def test_current_target_week_before_and_after_release() -> None:
    from datetime import datetime
    from natgas.calendar import ET

    before = datetime(2026, 9, 3, 10, 0, tzinfo=ET)
    after = datetime(2026, 9, 3, 11, 0, tzinfo=ET)
    assert current_target_week(before) == date(2026, 8, 28)
    assert current_target_week(after) == date(2026, 9, 4)
    assert last_completed_friday(date(2026, 9, 1)) == date(2026, 8, 28)
