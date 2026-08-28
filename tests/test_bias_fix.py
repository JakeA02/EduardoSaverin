"""Feature construction and naive baseline leakage tests."""

from datetime import date

import numpy as np
import pandas as pd

from natgas.models.baseline import ORIGIN, build_feature_matrix
from natgas.models.calibrate import fit_monthly_bias
from natgas.models.evaluate import naive_seasonal_baseline


def test_trend_is_calendar_not_row_index() -> None:
    df = pd.DataFrame(
        {
            "week_ending": [date(2020, 1, 3), date(2021, 1, 1)],
            "gw_hdd": [10.0, 10.0],
            "gw_cdd": [0.0, 0.0],
            "storage_change_bcf": [-20.0, -20.0],
        }
    )
    X, _ = build_feature_matrix(df)
    expected = np.array(
        [
            (pd.Timestamp("2020-01-03") - ORIGIN).days / 365.25,
            (pd.Timestamp("2021-01-01") - ORIGIN).days / 365.25,
        ]
    )
    np.testing.assert_allclose(X["trend"].to_numpy(), expected)
    assert X.loc[0, "is_shoulder"] == 0
    assert X.loc[1, "is_shoulder"] == 0


def test_naive_excludes_current_week() -> None:
    fridays = []
    year = 2010
    while len(fridays) < 6:
        d = date(year, 5, 14)
        while d.isocalendar()[1] != 20:
            d = d.replace(day=d.day + 1) if d.day < 28 else date(year, 5, 1)
            if d.month != 5:
                break
        # First Friday of ISO week 20
        start = date.fromisocalendar(year, 20, 5)
        fridays.append(start)
        year += 1
    df = pd.DataFrame(
        {
            "week_ending": fridays,
            "storage_change_bcf": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        }
    )
    naive = naive_seasonal_baseline(df)
    assert pd.isna(naive.iloc[0])
    assert naive.iloc[3] == 20.0  # mean of 10, 20, 30


def test_monthly_bias_subtracts_train_residual() -> None:
    weeks = pd.to_datetime(["2024-01-05", "2024-01-12", "2024-02-02"] + ["2023-03-03"] * 8)
    pred = np.array([10.0] * 11)
    actual = np.array([0.0] * 11)
    cal = fit_monthly_bias(pd.Series(weeks), pred, actual)
    out = cal.apply(pd.Series(pd.to_datetime(["2024-01-19"])), np.array([10.0]))
    assert out[0] == 0.0
