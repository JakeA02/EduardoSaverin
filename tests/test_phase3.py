"""Phase 3 blend, bias correction, and NWP sampling tests."""

from datetime import date, datetime

import numpy as np
import pandas as pd

from natgas.features.blend import blend_week_daily, week_totals
from natgas.ingest.cpc import _parse_pipe_file
from natgas.ingest.nwp import kelvin_to_fahrenheit, nearest_index, sample_grid, temps_to_daily_dd
from natgas.models.nwp_bias import apply_forecast_bias, fit_forecast_bias


FCST_TXT = """Product: Daily Heating Degree Days Forecast
Region|20260822|20260823|20260824|20260825|20260826|20260827|20260828|Total
1|1|1|1|1|1|1|1|7
CONUS|2|2|2|2|2|2|2|14
"""


def test_parse_forecast_skips_total_column() -> None:
    df = _parse_pipe_file(FCST_TXT, "hdd")
    assert "Total" not in df.columns
    assert set(df["region"]) == {"NEW ENGLAND", "NATIONAL"}
    assert df["date"].nunique() == 7
    assert df.loc[df["region"] == "NATIONAL", "hdd"].sum() == 14.0


def test_parse_forecast_skips_missing_sentinel() -> None:
    txt = """Region|20260715|20260716
CONUS|-9999|5
"""
    df = _parse_pipe_file(txt, "hdd")
    assert list(df["hdd"]) == [5.0]
    assert df["date"].tolist() == [date(2026, 7, 16)]
    df = _parse_pipe_file(FCST_TXT, "hdd")
    assert "Total" not in df.columns
    assert set(df["region"]) == {"NEW ENGLAND", "NATIONAL"}
    assert df["date"].nunique() == 7
    assert df.loc[df["region"] == "NATIONAL", "hdd"].sum() == 14.0


def test_blend_uses_observed_before_as_of() -> None:
    days = pd.date_range("2026-08-22", periods=7, freq="D")
    regions = ["NEW ENGLAND", "NATIONAL"]
    obs_rows = []
    fcst_rows = []
    for d in days:
        for r in regions:
            obs_rows.append({"date": d.date(), "region": r, "hdd": 10.0, "cdd": 1.0})
            fcst_rows.append(
                {
                    "issued": date(2026, 8, 22),
                    "date": d.date(),
                    "region": r,
                    "hdd": 99.0,
                    "cdd": 9.0,
                    "lead_days": (d.date() - date(2026, 8, 22)).days,
                }
            )
    # Remaining divisions so GWDD has weights
    for name in [
        "EAST NORTH CENTRAL",
        "WEST NORTH CENTRAL",
        "SOUTH ATLANTIC",
        "EAST SOUTH CENTRAL",
        "WEST SOUTH CENTRAL",
        "MOUNTAIN",
        "PACIFIC",
    ]:
        for d in days:
            obs_rows.append({"date": d.date(), "region": name, "hdd": 10.0, "cdd": 1.0})
            fcst_rows.append(
                {
                    "issued": date(2026, 8, 22),
                    "date": d.date(),
                    "region": name,
                    "hdd": 99.0,
                    "cdd": 9.0,
                    "lead_days": (d.date() - date(2026, 8, 22)).days,
                }
            )
    obs = pd.DataFrame(obs_rows)
    fcst = pd.DataFrame(fcst_rows)
    # As-of Wednesday 8/26: Sat-Tue observed (4 days), Wed-Fri forecast (3)
    blend = blend_week_daily(date(2026, 8, 28), date(2026, 8, 26), obs, fcst)
    nat = blend[blend["region"] == "NATIONAL"]
    assert (nat.loc[nat["source"] == "observed", "date"].nunique()) == 4
    assert (nat.loc[nat["source"] == "forecast", "date"].nunique()) == 3
    totals = week_totals(blend)
    # 4*10 + 3*99 = 337 national HDD
    assert totals["cpc_hdd"] == 4 * 10 + 3 * 99


def test_bias_correction_subtracts_mean_error() -> None:
    fcst = pd.DataFrame(
        {
            "issued": [date(2026, 1, 1)] * 2,
            "date": [date(2026, 1, 1), date(2026, 1, 2)],
            "region": ["NATIONAL", "NATIONAL"],
            "hdd": [12.0, 12.0],
            "cdd": [0.0, 0.0],
            "lead_days": [0, 1],
        }
    )
    obs = pd.DataFrame(
        {
            "date": [date(2026, 1, 1), date(2026, 1, 2)],
            "region": ["NATIONAL", "NATIONAL"],
            "hdd": [10.0, 10.0],
            "cdd": [0.0, 0.0],
        }
    )
    bias = fit_forecast_bias(fcst, obs)
    assert (bias["hdd_bias"] == 2.0).all()
    corrected = apply_forecast_bias(fcst, bias)
    assert (corrected["hdd"] == 10.0).all()


def test_kelvin_and_nearest_neighbor() -> None:
    assert abs(kelvin_to_fahrenheit(np.array([273.15]))[0] - 32.0) < 1e-6
    lons = np.linspace(-100, -70, 31)
    lats = np.linspace(30, 50, 21)
    field = np.zeros((21, 31))
    field[nearest_index(lats, 42.5), nearest_index(lons, -71.5)] = 40.0
    lon2d, lat2d = np.meshgrid(lons, lats)
    assert sample_grid(lon2d, lat2d, field, -71.5, 42.5) == 40.0


def test_temps_to_daily_dd() -> None:
    times = pd.date_range("2026-01-15 00:00", periods=8, freq="3h")
    t = np.array([20.0, 25.0, 30.0, 35.0, 40.0, 30.0, 20.0, 10.0])  # max 40 min 10 mean 25 -> HDD 40
    daily = temps_to_daily_dd(times, t)
    assert daily.loc[0, "hdd"] == 40.0
    assert daily.loc[0, "cdd"] == 0.0
