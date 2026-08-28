"""Lead-time / month bias correction for forecast degree days vs CPC observed."""

from __future__ import annotations

import pandas as pd


def fit_forecast_bias(forecasts: pd.DataFrame, observed: pd.DataFrame) -> pd.DataFrame:
    """Mean (forecast − observed) HDD/CDD by region, lead_days, and valid month."""
    fcst = forecasts.copy()
    obs = observed.copy()
    fcst["date"] = pd.to_datetime(fcst["date"])
    obs["date"] = pd.to_datetime(obs["date"])
    if "lead_days" not in fcst.columns:
        fcst["lead_days"] = (fcst["date"] - pd.to_datetime(fcst["issued"])).dt.days
    merged = fcst.merge(obs, on=["date", "region"], suffixes=("_fcst", "_obs"))
    if merged.empty:
        return pd.DataFrame(columns=["region", "lead_days", "month", "hdd_bias", "cdd_bias", "n"])
    merged["month"] = merged["date"].dt.month
    merged["hdd_bias"] = merged["hdd_fcst"] - merged["hdd_obs"]
    merged["cdd_bias"] = merged["cdd_fcst"] - merged["cdd_obs"]
    return (
        merged.groupby(["region", "lead_days", "month"], as_index=False)
        .agg(hdd_bias=("hdd_bias", "mean"), cdd_bias=("cdd_bias", "mean"), n=("hdd_bias", "size"))
    )


def apply_forecast_bias(forecasts: pd.DataFrame, bias: pd.DataFrame) -> pd.DataFrame:
    if bias is None or bias.empty:
        return forecasts
    out = forecasts.copy()
    out["date"] = pd.to_datetime(out["date"])
    if "lead_days" not in out.columns:
        out["lead_days"] = (out["date"] - pd.to_datetime(out["issued"])).dt.days
    out["month"] = out["date"].dt.month
    merged = out.merge(
        bias[["region", "lead_days", "month", "hdd_bias", "cdd_bias"]],
        on=["region", "lead_days", "month"],
        how="left",
    )
    merged["hdd_bias"] = merged["hdd_bias"].fillna(0.0)
    merged["cdd_bias"] = merged["cdd_bias"].fillna(0.0)
    merged["hdd"] = (merged["hdd"] - merged["hdd_bias"]).clip(lower=0)
    merged["cdd"] = (merged["cdd"] - merged["cdd_bias"]).clip(lower=0)
    return merged.drop(columns=["hdd_bias", "cdd_bias", "month"])
