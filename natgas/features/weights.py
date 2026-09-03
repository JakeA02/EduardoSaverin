"""Gas-weighted degree-day weights."""

from __future__ import annotations

import pandas as pd

from natgas.config import CPC_DIVISIONS, DEFAULT_GAS_HEATING_SHARE


def population_series() -> pd.Series:
    """Approximate 2020 census division populations (millions)."""
    return pd.Series(
        {
            "NEW ENGLAND": 15.0,
            "EAST NORTH CENTRAL": 46.7,
            "WEST NORTH CENTRAL": 21.4,
            "SOUTH ATLANTIC": 68.0,
            "EAST SOUTH CENTRAL": 19.2,
            "WEST SOUTH CENTRAL": 40.5,
            "MOUNTAIN": 25.0,
            "PACIFIC": 55.0,
        }
    )


def population_weights() -> pd.Series:
    """Population shares by census division (sums to 1)."""
    pop = population_series()
    return pop / pop.sum()


def demographic_weights() -> pd.Series:
    """Population × gas-heating-share weights by census division."""
    population = population_series()
    gas_share = pd.Series(DEFAULT_GAS_HEATING_SHARE)
    raw = population * gas_share.reindex(population.index)
    return raw / raw.sum()


def apply_gwdd(
    div_df: pd.DataFrame,
    weights: pd.Series | None = None,
    key: str = "week_ending",
) -> pd.DataFrame:
    """
    Compute gas-weighted national HDD/CDD from division-level CPC data.

    div_df columns: {key}, region, hdd, cdd
    """
    weights = demographic_weights() if weights is None else weights
    div = div_df[div_df["region"].isin(CPC_DIVISIONS)].copy()
    div["weight"] = div["region"].map(weights)
    div["weight"] = div["weight"] / div.groupby(key)["weight"].transform("sum")
    div["gw_hdd"] = div["hdd"] * div["weight"]
    div["gw_cdd"] = div["cdd"] * div["weight"]
    return (
        div.groupby(key, as_index=False)[["gw_hdd", "gw_cdd"]]
        .sum()
        .sort_values(key)
        .reset_index(drop=True)
    )


def calibrate_weights(
    div_df: pd.DataFrame,
    storage_change: pd.Series,
    hdd_col: str = "hdd",
) -> pd.Series:
    """
    Empirical calibration: regress storage change on division HDD,
    normalize positive coefficients to weights.
    """
    from sklearn.linear_model import LinearRegression

    div = div_df[div_df["region"].isin(CPC_DIVISIONS)].copy()
    pivot = div.pivot(index="week_ending", columns="region", values=hdd_col)
    y = storage_change.reindex(pivot.index)
    mask = y.notna()
    X = pivot.loc[mask].fillna(0)
    y = y.loc[mask]
    if len(y) < 52:
        return demographic_weights()

    model = LinearRegression(positive=True, fit_intercept=True)
    model.fit(X, y)
    coef = pd.Series(model.coef_, index=X.columns)
    if coef.sum() <= 0:
        return demographic_weights()
    return coef / coef.sum()
