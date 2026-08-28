"""Degree-day computation tests."""

import pandas as pd

from natgas.features.degree_days import daily_degree_days, spline_cdd


def test_daily_degree_days() -> None:
    tmax = pd.Series([80.0, 50.0])
    tmin = pd.Series([70.0, 40.0])
    dd = daily_degree_days(tmax, tmin, base=65.0)
    assert dd.loc[0, "cdd"] == 10.0  # mean 75
    assert dd.loc[0, "hdd"] == 0.0
    assert dd.loc[1, "hdd"] == 20.0  # mean 45
    assert dd.loc[1, "cdd"] == 0.0


def test_spline_cdd_knot() -> None:
    cdd = pd.Series([10.0, 20.0])
    feats = spline_cdd(cdd, knot=15.0)
    assert feats.loc[0, "cdd_excess"] == 0.0
    assert feats.loc[1, "cdd_excess"] == 5.0
