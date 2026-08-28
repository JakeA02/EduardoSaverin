"""Blend observed CPC degree days with NDFD/GFS forecasts as of a cutoff date."""

from __future__ import annotations

from datetime import date

import pandas as pd

from natgas.calendar import storage_week_days
from natgas.config import CPC_DIVISIONS
from natgas.features.weights import apply_gwdd, demographic_weights
from natgas.models.nwp_bias import apply_forecast_bias, fit_forecast_bias


def _as_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.date


def blend_week_daily(
    week_ending: date,
    as_of: date,
    observed: pd.DataFrame,
    forecasts: pd.DataFrame,
    bias: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Region-level daily HDD/CDD for one storage week as of `as_of`.

    Days strictly before `as_of` use observations. Remaining days use the
    forecast issued on `as_of`, or the latest issue at or before `as_of`.
    """
    days = storage_week_days(week_ending)
    obs = observed.copy()
    obs["date"] = _as_date(obs["date"])
    fcst = forecasts.copy()
    fcst["date"] = _as_date(fcst["date"])
    fcst["issued"] = _as_date(fcst["issued"])

    issued_ok = fcst.loc[fcst["issued"] <= as_of, "issued"]
    if issued_ok.empty:
        issue = None
    else:
        issue = max(issued_ok)

    rows = []
    for day in days:
        if day < as_of:
            src = obs[(obs["date"] == day) & (obs["region"].isin(CPC_DIVISIONS + ["NATIONAL"]))]
            for _, r in src.iterrows():
                rows.append(
                    {
                        "date": day,
                        "region": r["region"],
                        "hdd": r["hdd"],
                        "cdd": r["cdd"],
                        "source": "observed",
                    }
                )
            continue
        if issue is None:
            continue
        src = fcst[(fcst["issued"] == issue) & (fcst["date"] == day)]
        if bias is not None and not src.empty:
            src = apply_forecast_bias(src, bias)
        for _, r in src.iterrows():
            rows.append(
                {
                    "date": day,
                    "region": r["region"],
                    "hdd": r["hdd"],
                    "cdd": r["cdd"],
                    "source": "forecast",
                }
            )
    return pd.DataFrame(rows)


def week_totals(
    daily_blend: pd.DataFrame,
    weights: pd.Series | None = None,
) -> dict[str, float]:
    """National CPC and gas-weighted weekly HDD/CDD from a daily blend."""
    if daily_blend.empty:
        return {"cpc_hdd": float("nan"), "cpc_cdd": float("nan"), "gw_hdd": float("nan"), "gw_cdd": float("nan")}
    nat = daily_blend[daily_blend["region"] == "NATIONAL"]
    if nat.empty:
        cpc_hdd = float("nan")
        cpc_cdd = float("nan")
    else:
        cpc_hdd = float(nat["hdd"].sum())
        cpc_cdd = float(nat["cdd"].sum())
    weekly_div = (
        daily_blend[daily_blend["region"].isin(CPC_DIVISIONS)]
        .groupby("region", as_index=False)[["hdd", "cdd"]]
        .sum()
    )
    weekly_div["week_ending"] = 0
    gw = apply_gwdd(weekly_div, weights if weights is not None else demographic_weights())
    return {
        "cpc_hdd": cpc_hdd,
        "cpc_cdd": cpc_cdd,
        "gw_hdd": float(gw["gw_hdd"].iloc[0]) if not gw.empty else float("nan"),
        "gw_cdd": float(gw["gw_cdd"].iloc[0]) if not gw.empty else float("nan"),
    }


def build_asof_panel(
    week_endings: list[date],
    as_of_fn,
    observed: pd.DataFrame,
    forecasts: pd.DataFrame,
    *,
    fit_bias_before: date | None = None,
) -> pd.DataFrame:
    """One row per week with blended GWDD. `as_of_fn(week_ending) -> date`."""
    bias = None
    if fit_bias_before is not None:
        obs = observed.copy()
        obs["date"] = _as_date(obs["date"])
        fcst = forecasts.copy()
        fcst["issued"] = _as_date(fcst["issued"])
        train_fcst = fcst[fcst["issued"] < fit_bias_before]
        bias = fit_forecast_bias(train_fcst, obs)

    records = []
    for we in week_endings:
        as_of = as_of_fn(we)
        daily = blend_week_daily(we, as_of, observed, forecasts, bias=bias)
        if daily.empty or daily["date"].nunique() < 7:
            continue
        if (daily["hdd"] < 0).any() or (daily["cdd"] < 0).any():
            continue
        totals = week_totals(daily)
        if any(pd.isna(v) for v in totals.values()):
            continue
        n_fcst = int(daily.loc[daily["source"] == "forecast", "date"].nunique())
        records.append({"week_ending": we, "as_of": as_of, "forecast_days": n_fcst, **totals})
    return pd.DataFrame(records)
