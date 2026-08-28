"""Time-series cross-validation and backtesting."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit


@dataclass
class BacktestResult:
    fold_mae: pd.Series
    predictions: pd.DataFrame
    naive_mae: float
    model_mae: float
    winter_model_mae: float | None
    bias: float


def naive_seasonal_baseline(df: pd.DataFrame) -> pd.Series:
    """Same week-of-year historical average, excluding the current week."""
    data = df.copy()
    data["week_ending"] = pd.to_datetime(data["week_ending"])
    data["woy"] = data["week_ending"].dt.isocalendar().week.astype(int)
    return data.groupby("woy")["storage_change_bcf"].transform(
        lambda s: s.shift(1).expanding(min_periods=3).mean()
    )


def run_backtest(
    df: pd.DataFrame,
    n_splits: int = 5,
    min_train: int = 156,
    use_gwdd: bool = True,
    model_type: str = "ols",
    calibrate: bool = True,
) -> BacktestResult:
    from natgas.models.baseline import fit_baseline_ols, predict as predict_ols
    from natgas.models.ensemble import fit_lightgbm, predict as predict_lgbm

    data = df.sort_values("week_ending").reset_index(drop=True)
    tscv = TimeSeriesSplit(n_splits=n_splits, test_size=max(26, len(data) // (n_splits + 1)))

    preds = []
    fold_maes = []

    for fold, (train_idx, test_idx) in enumerate(tscv.split(data)):
        if len(train_idx) < min_train:
            continue
        train = data.iloc[train_idx]
        test = data.iloc[test_idx]
        # Features that use rolling windows need history through the test week.
        context = data.iloc[: test_idx[-1] + 1]
        n_test = len(test)

        if model_type == "lgbm":
            model = fit_lightgbm(train, use_gwdd=use_gwdd, calibrate=calibrate)
            y_hat = predict_lgbm(model, context, use_gwdd=use_gwdd)[-n_test:]
        else:
            model = fit_baseline_ols(train, use_gwdd=use_gwdd, calibrate=calibrate)
            y_hat = predict_ols(model, context, use_gwdd=use_gwdd)[-n_test:]

        y_true = test["storage_change_bcf"].values
        mae = float(np.mean(np.abs(y_hat - y_true)))
        fold_maes.append(mae)
        fold_df = test[["week_ending", "storage_change_bcf"]].copy()
        fold_df["predicted"] = y_hat
        fold_df["fold"] = fold
        fold_df["train_end"] = train["week_ending"].iloc[-1]
        preds.append(fold_df)

    if not preds:
        raise ValueError("Not enough data for backtest; ingest more history.")

    pred_df = pd.concat(preds, ignore_index=True)
    aligned = data[["week_ending"]].copy()
    aligned["naive"] = naive_seasonal_baseline(data).values
    pred_df = pred_df.merge(aligned, on="week_ending", how="left")

    model_mae = float(np.mean(np.abs(pred_df["predicted"] - pred_df["storage_change_bcf"])))
    naive_mae = float(np.mean(np.abs(pred_df["naive"] - pred_df["storage_change_bcf"])))
    bias = float(np.mean(pred_df["predicted"] - pred_df["storage_change_bcf"]))

    months = pd.to_datetime(pred_df["week_ending"]).dt.month
    winter_mask = months.isin([11, 12, 1, 2, 3])
    winter_mae = None
    if winter_mask.any():
        winter_mae = float(
            np.mean(
                np.abs(
                    pred_df.loc[winter_mask, "predicted"]
                    - pred_df.loc[winter_mask, "storage_change_bcf"]
                )
            )
        )

    return BacktestResult(
        fold_mae=pd.Series(fold_maes),
        predictions=pred_df,
        naive_mae=naive_mae,
        model_mae=model_mae,
        winter_model_mae=winter_mae,
        bias=bias,
    )


def rolling_mae_vs_consensus(
    pred_df: pd.DataFrame,
    consensus_df: pd.DataFrame,
    window: int = 26,
) -> pd.DataFrame:
    left = pred_df.copy()
    right = consensus_df.copy()
    left["week_ending"] = pd.to_datetime(left["week_ending"]).dt.date
    right["week_ending"] = pd.to_datetime(right["week_ending"]).dt.date
    left = left.drop(columns=["consensus_median"], errors="ignore")
    merged = left.merge(right, on="week_ending", how="inner")
    merged["model_abs_err"] = (merged["predicted"] - merged["storage_change_bcf"]).abs()
    merged["consensus_abs_err"] = (merged["consensus_median"] - merged["storage_change_bcf"]).abs()
    merged["model_beats_consensus"] = merged["model_abs_err"] < merged["consensus_abs_err"]
    merged["rolling_model_mae"] = merged["model_abs_err"].rolling(window).mean()
    merged["rolling_consensus_mae"] = merged["consensus_abs_err"].rolling(window).mean()
    return merged


def surprise_direction_hit_rate(
    pred_df: pd.DataFrame,
    consensus_df: pd.DataFrame,
) -> float:
    merged = rolling_mae_vs_consensus(pred_df, consensus_df, window=1)
    if merged.empty:
        return float("nan")
    actual_surprise = merged["storage_change_bcf"] - merged["consensus_median"]
    pred_surprise = merged["predicted"] - merged["consensus_median"]
    hits = np.sign(actual_surprise) == np.sign(pred_surprise)
    return float(hits.mean())
