"""Baseline OLS model for storage change."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from natgas.features.degree_days import extreme_hdd, spline_cdd
from natgas.models.calibrate import MonthlyBiasCalibrator, fit_monthly_bias

ORIGIN = pd.Timestamp("2010-01-01")
SHOULDER_MONTHS = {4, 5, 9, 10}


@dataclass
class BaselineModel:
    name: str
    pipeline: Pipeline
    feature_names: list[str]
    calibrator: MonthlyBiasCalibrator | None = None
    residual_q05: float = -20.0
    residual_q95: float = 20.0


def _seasonal_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["week_ending"] = pd.to_datetime(out["week_ending"])
    out["month"] = out["week_ending"].dt.month
    out["is_shoulder"] = out["month"].isin(SHOULDER_MONTHS).astype(int)
    # Calendar time, not row index — row-index trend resets on each test fold and
    # injects a large systematic bias.
    out["trend"] = (out["week_ending"] - ORIGIN).dt.days / 365.25
    return out


def build_feature_matrix(df: pd.DataFrame, use_gwdd: bool = True) -> tuple[pd.DataFrame, pd.Series]:
    data = _seasonal_features(df)
    hdd_col = "gw_hdd" if use_gwdd else "cpc_hdd"
    cdd_col = "gw_cdd" if use_gwdd else "cpc_cdd"

    cdd_feats = spline_cdd(data[cdd_col])
    data["cdd"] = cdd_feats["cdd"]
    data["cdd_excess"] = cdd_feats["cdd_excess"]
    data["hdd"] = data[hdd_col]
    data["hdd_extreme"] = extreme_hdd(data[hdd_col])

    feature_cols = ["hdd", "hdd_extreme", "cdd", "cdd_excess", "trend", "is_shoulder"]
    if "production_bcf_d" in data.columns and data["production_bcf_d"].notna().any():
        data["production_bcf_d"] = data["production_bcf_d"].ffill()
        trailing = data["production_bcf_d"].rolling(52, min_periods=12).mean().shift(1)
        data["prod_dev"] = data["production_bcf_d"] - trailing
        data["prod_dev"] = data["prod_dev"].fillna(0.0)
        feature_cols.append("prod_dev")

    X = data[feature_cols + ["month"]].copy()
    y = data["storage_change_bcf"].astype(float)
    return X, y


def fit_baseline_ols(
    df: pd.DataFrame, use_gwdd: bool = True, calibrate: bool = True
) -> BaselineModel:
    X, y = build_feature_matrix(df, use_gwdd=use_gwdd)
    numeric = [c for c in X.columns if c != "month"]
    preprocessor = ColumnTransformer(
        [
            ("num", "passthrough", numeric),
            ("month", OneHotEncoder(drop="first", sparse_output=False), ["month"]),
        ]
    )
    pipeline = Pipeline(
        [
            ("prep", preprocessor),
            ("model", LinearRegression()),
        ]
    )
    pipeline.fit(X, y)
    fitted = BaselineModel(
        name="baseline-ols-v0.2", pipeline=pipeline, feature_names=list(X.columns)
    )
    raw = pipeline.predict(X)
    resid = raw - y.to_numpy()
    fitted.residual_q05 = float(np.quantile(resid, 0.05))
    fitted.residual_q95 = float(np.quantile(resid, 0.95))
    if calibrate:
        fitted.calibrator = fit_monthly_bias(df["week_ending"], raw, y)
        fitted.name = "baseline-ols-v0.2-calibrated"
        cal = fitted.calibrator.apply(df["week_ending"], raw)
        resid = cal - y.to_numpy()
        fitted.residual_q05 = float(np.quantile(resid, 0.05))
        fitted.residual_q95 = float(np.quantile(resid, 0.95))
    return fitted


def predict(model: BaselineModel, df: pd.DataFrame, use_gwdd: bool = True) -> np.ndarray:
    X, _ = build_feature_matrix(df, use_gwdd=use_gwdd)
    raw = model.pipeline.predict(X)
    if model.calibrator is not None:
        return model.calibrator.apply(df["week_ending"], raw)
    return raw
