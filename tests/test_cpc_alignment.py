"""Tests for EIA storage week alignment of CPC degree days."""

from datetime import date

from natgas.ingest.cpc import daily_to_storage_weeks
import pandas as pd


def test_storage_week_sums_saturday_through_friday() -> None:
    # Week ending 2026-08-28: Sat 8/22 through Fri 8/28
    daily = pd.DataFrame(
        {
            "date": [
                date(2026, 8, 22),  # Saturday
                date(2026, 8, 28),  # Friday
            ],
            "region": ["NATIONAL", "NATIONAL"],
            "hdd": [10.0, 5.0],
            "cdd": [0.0, 2.0],
        }
    )
    weekly = daily_to_storage_weeks(daily)
    row = weekly.iloc[0]
    assert row["week_ending"] == date(2026, 8, 28)
    assert row["hdd"] == 15.0
    assert row["cdd"] == 2.0
