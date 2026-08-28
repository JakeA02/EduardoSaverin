"""Gradient boosting model for storage change."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from natgas.models.baseline import build_feature_matrix
from natgas.models.calibrate import MonthlyBiasCalibrator, fit_monthly_bias


@dataclass
class BoostedModel:
    name: str
    model: object
    feature_columns: list[str]
    calibrator: MonthlyBiasCalibrator | None = None
    residual_q05: float = -20.0
    residual_q95: float = 20.0


def fit_lightgbm(df: pd.DataFrame, use_gwdd: bool = True, calibrate: bool = True) -> BoostedModel:
    import lightgbm as lgb

    X, y = build_feature_matrix(df, use_gwdd=use_gwdd)
    X = pd.get_dummies(X, columns=["month"], drop_first=True)
    model = lgb.LGBMRegressor(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=5,
        min_child_samples=15,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1,
    )
    model.fit(X, y)
    fitted = BoostedModel(name="lightgbm-v0.2", model=model, feature_columns=list(X.columns))
    raw = model.predict(X)
    resid = raw - y.to_numpy()
    fitted.residual_q05 = float(np.quantile(resid, 0.05))
    fitted.residual_q95 = float(np.quantile(resid, 0.95))
    if calibrate:
        fitted.calibrator = fit_monthly_bias(df["week_ending"], raw, y)
        fitted.name = "lightgbm-v0.2-calibrated"
        cal = fitted.calibrator.apply(df["week_ending"], raw)
        resid = cal - y.to_numpy()
        fitted.residual_q05 = float(np.quantile(resid, 0.05))
        fitted.residual_q95 = float(np.quantile(resid, 0.95))
    return fitted


def predict(model: BoostedModel, df: pd.DataFrame, use_gwdd: bool = True) -> np.ndarray:
    X, _ = build_feature_matrix(df, use_gwdd=use_gwdd)
    X = pd.get_dummies(X, columns=["month"], drop_first=True)
    for col in model.feature_columns:
        if col not in X.columns:
            X[col] = 0
    X = X[model.feature_columns]
    raw = model.model.predict(X)
    if model.calibrator is not None:
        return model.calibrator.apply(df["week_ending"], raw)
    return raw
