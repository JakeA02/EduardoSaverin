"""Degree-day computation utilities."""

from __future__ import annotations

import numpy as np
import pandas as pd

from natgas.config import STORAGE_BASE_TEMP_F


def daily_degree_days(tmax: pd.Series, tmin: pd.Series, base: float = STORAGE_BASE_TEMP_F) -> pd.DataFrame:
    tmean = (tmax + tmin) / 2
    hdd = (base - tmean).clip(lower=0)
    cdd = (tmean - base).clip(lower=0)
    return pd.DataFrame({"hdd": hdd, "cdd": cdd})


def weekly_from_daily(daily: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    daily = daily.copy()
    daily[date_col] = pd.to_datetime(daily[date_col])
    daily["week_ending"] = daily[date_col] + pd.to_timedelta(4 - daily[date_col].dt.weekday, unit="D")
    weekly = daily.groupby("week_ending", as_index=False)[["hdd", "cdd"]].sum()
    weekly["week_ending"] = weekly["week_ending"].dt.date
    return weekly


def spline_cdd(cdd: pd.Series, knot: float = 15.0) -> pd.DataFrame:
    """Piecewise linear CDD: base term + excess above knot (proxy for convex cooling)."""
    excess = (cdd - knot).clip(lower=0)
    return pd.DataFrame({"cdd": cdd, "cdd_excess": excess})


def extreme_hdd(hdd: pd.Series, threshold: float = 20.0) -> pd.Series:
    """Extra heating demand at extreme cold."""
    return (hdd - threshold).clip(lower=0)
