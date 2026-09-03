"""WeatherNext ingest, two-week coverage, and A/B log selection."""

from datetime import date, timedelta

import pandas as pd

from natgas.config import CPC_DIVISIONS
from natgas.features.blend import blend_is_complete, blend_week_daily
from natgas.ingest.nwp import attach_national, celsius_to_fahrenheit
from natgas.ingest.weathernext import (
    MAPS_MAX_HOURS,
    WN3_SYNOPTIC_HOURS,
    days_needed_for_week,
    hours_to_daily_dd,
    maps_api_covers_week,
)
from natgas.models.live import default_weeks_for_source


def _hour(year: int, month: int, day: int, hour: int, temp_c: float) -> dict:
    return {
        "interval": {
            "startTime": f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00Z",
            "endTime": f"{year:04d}-{month:02d}-{day:02d}T{(hour + 1) % 24:02d}:00:00Z",
        },
        "displayDateTime": {"year": year, "month": month, "day": day, "hours": hour},
        "temperature": {"degrees": temp_c, "unit": "CELSIUS"},
    }


def _forecast(issued: date, start: date, n_days: int) -> pd.DataFrame:
    rows = []
    regions = list(CPC_DIVISIONS) + ["NATIONAL"]
    for i in range(n_days):
        day = start + timedelta(days=i)
        for region in regions:
            rows.append(
                {
                    "issued": issued,
                    "date": day,
                    "region": region,
                    "hdd": 5.0,
                    "cdd": 0.0,
                    "lead_days": i,
                }
            )
    return pd.DataFrame(rows)


def test_celsius_conversion() -> None:
    assert abs(float(celsius_to_fahrenheit([0.0])[0]) - 32.0) < 1e-6
    assert abs(float(celsius_to_fahrenheit([10.0])[0]) - 50.0) < 1e-6


def test_hours_to_daily_dd_mean() -> None:
    # 0 C and 20 C → 32 F and 68 F, mean 50 F → HDD 15
    hours = [
        _hour(2026, 1, 15, 6, 0.0),
        _hour(2026, 1, 15, 18, 20.0),
    ]
    daily = hours_to_daily_dd(hours, issued=date(2026, 1, 15), region="NEW ENGLAND")
    assert len(daily) == 1
    assert daily.loc[0, "hdd"] == 15.0
    assert daily.loc[0, "cdd"] == 0.0
    assert daily.loc[0, "nwp_source"] == "weathernext3"


def test_attach_national_population_weights() -> None:
    issued = date(2026, 8, 22)
    rows = []
    for region in CPC_DIVISIONS:
        rows.append(
            {
                "issued": issued,
                "date": issued,
                "region": region,
                "hdd": 10.0,
                "cdd": 1.0,
                "nwp_source": "weathernext3",
            }
        )
    out = attach_national(pd.DataFrame(rows))
    nat = out[out["region"] == "NATIONAL"]
    assert len(nat) == 1
    assert abs(float(nat.iloc[0]["hdd"]) - 10.0) < 1e-9


def test_maps_10_day_cannot_cover_week2_from_saturday() -> None:
    saturday = date(2026, 8, 22)
    week2 = date(2026, 9, 4)
    assert days_needed_for_week(week2, saturday) == 14
    assert not maps_api_covers_week(week2, saturday, max_hours=MAPS_MAX_HOURS)
    assert maps_api_covers_week(week2, saturday, max_hours=WN3_SYNOPTIC_HOURS)


def test_maps_10_day_covers_week2_from_wednesday() -> None:
    wednesday = date(2026, 8, 26)
    week2 = date(2026, 9, 4)
    assert days_needed_for_week(week2, wednesday) == 10
    assert maps_api_covers_week(week2, wednesday, max_hours=MAPS_MAX_HOURS)


def test_blend_week2_incomplete_with_10_day_from_saturday() -> None:
    issued = date(2026, 8, 22)
    fcst = _forecast(issued, issued, n_days=10)
    obs = pd.DataFrame(columns=["date", "region", "hdd", "cdd"])
    daily = blend_week_daily(date(2026, 9, 4), issued, obs, fcst)
    assert daily["date"].nunique() == 3  # 8/29–8/31
    assert not blend_is_complete(daily)


def test_blend_week2_complete_with_15_day_from_saturday() -> None:
    issued = date(2026, 8, 22)
    fcst = _forecast(issued, issued, n_days=15)
    obs = pd.DataFrame(columns=["date", "region", "hdd", "cdd"])
    daily = blend_week_daily(date(2026, 9, 4), issued, obs, fcst)
    assert blend_is_complete(daily)


def test_blend_week2_complete_with_10_day_from_wednesday() -> None:
    issued = date(2026, 8, 26)
    fcst = _forecast(issued, issued, n_days=10)
    obs = pd.DataFrame(columns=["date", "region", "hdd", "cdd"])
    daily = blend_week_daily(date(2026, 9, 4), issued, obs, fcst)
    assert blend_is_complete(daily)


def test_default_weeks_weathernext_is_two() -> None:
    assert default_weeks_for_source("cpc-forecast", None) == 1
    assert default_weeks_for_source("weathernext", None) == 2
    assert default_weeks_for_source("cpc-forecast", 2) == 2
