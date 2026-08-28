"""Walk-forward backtest using as-of forecast weather instead of observed GWDD."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from natgas.calendar import storage_week_start
from natgas.features.blend import build_asof_panel
from natgas.models.evaluate import BacktestResult, naive_seasonal_baseline


def saturday_as_of(week_ending: date) -> date:
    return storage_week_start(week_ending)


def wednesday_as_of(week_ending: date) -> date:
    return storage_week_start(week_ending) + timedelta(days=4)


def run_forecast_backtest(
    panel: pd.DataFrame,
    observed_daily: pd.DataFrame,
    forecasts: pd.DataFrame,
    *,
    as_of: str = "saturday",
    n_splits: int = 5,
    min_train: int = 156,
    model_type: str = "ols",
) -> BacktestResult:
    """Train on observed weather; score with blended forecast GWDD (no look-ahead)."""
    from natgas.models.baseline import fit_baseline_ols, predict as predict_ols
    from natgas.models.ensemble import fit_lightgbm, predict as predict_lgbm

    data = panel.sort_values("week_ending").reset_index(drop=True)
    data["week_ending"] = pd.to_datetime(data["week_ending"]).dt.date
    as_of_fn = saturday_as_of if as_of == "saturday" else wednesday_as_of

    tscv = TimeSeriesSplit(n_splits=n_splits, test_size=max(26, len(data) // (n_splits + 1)))
    preds = []
    fold_maes = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(data)):
        if len(train_idx) < min_train:
            continue
        train = data.iloc[train_idx]
        test = data.iloc[test_idx]
        cutoff = train["week_ending"].iloc[-1]
        blended = build_asof_panel(
            list(test["week_ending"]),
            as_of_fn,
            observed_daily,
            forecasts,
            fit_bias_before=cutoff,
        )
        if blended.empty:
            continue
        blended["week_ending"] = pd.to_datetime(blended["week_ending"]).dt.date
        b = blended.set_index("week_ending")
        test_use = test[test["week_ending"].isin(b.index)].copy()
        if test_use.empty:
            continue
        for col in ("gw_hdd", "gw_cdd", "cpc_hdd", "cpc_cdd"):
            test_use[col] = test_use["week_ending"].map(b[col])
        context = pd.concat([train, test_use], ignore_index=True)

        if model_type == "lgbm":
            model = fit_lightgbm(train)
            y_hat = predict_lgbm(model, context)[-len(test_use) :]
        else:
            model = fit_baseline_ols(train)
            y_hat = predict_ols(model, context)[-len(test_use) :]

        y_true = test_use["storage_change_bcf"].to_numpy(dtype=float)
        fold_maes.append(float(np.mean(np.abs(y_hat - y_true))))
        fold_df = test_use[["week_ending", "storage_change_bcf"]].copy()
        fold_df["as_of"] = test_use["week_ending"].map(b["as_of"])
        fold_df["forecast_days"] = test_use["week_ending"].map(b["forecast_days"])
        fold_df["predicted"] = y_hat
        q05 = getattr(model, "residual_q05", -20.0)
        q95 = getattr(model, "residual_q95", 20.0)
        fold_df["interval_lower"] = y_hat + q05
        fold_df["interval_upper"] = y_hat + q95
        fold_df["fold"] = fold
        preds.append(fold_df)

    if not preds:
        raise ValueError("Forecast backtest produced no scored weeks; ingest CPC forecasts.")

    pred_df = pd.concat(preds, ignore_index=True)
    aligned = data[["week_ending"]].copy()
    aligned["naive"] = naive_seasonal_baseline(data.assign(week_ending=pd.to_datetime(data["week_ending"]))).values
    aligned["week_ending"] = pd.to_datetime(aligned["week_ending"]).dt.date
    pred_df["week_ending"] = pd.to_datetime(pred_df["week_ending"]).dt.date
    pred_df = pred_df.merge(aligned, on="week_ending", how="left")

    err = pred_df["predicted"] - pred_df["storage_change_bcf"]
    months = pd.to_datetime(pred_df["week_ending"]).dt.month
    winter = months.isin([11, 12, 1, 2, 3])
    winter_mae = float(np.mean(np.abs(err[winter]))) if winter.any() else None

    return BacktestResult(
        fold_mae=pd.Series(fold_maes),
        predictions=pred_df,
        naive_mae=float(np.mean(np.abs(pred_df["naive"] - pred_df["storage_change_bcf"]))),
        model_mae=float(np.mean(np.abs(err))),
        winter_model_mae=winter_mae,
        bias=float(err.mean()),
    )
