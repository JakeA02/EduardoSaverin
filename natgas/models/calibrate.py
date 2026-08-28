"""Out-of-sample residual calibration (no look-ahead)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MonthlyBiasCalibrator:
    """Subtract train-period mean residual (pred − actual) by calendar month."""

    global_bias: float
    bias_by_month: dict[int, float]

    def apply(self, week_ending: pd.Series, predictions: np.ndarray) -> np.ndarray:
        months = pd.to_datetime(week_ending).dt.month
        adj = months.map(lambda m: self.bias_by_month.get(int(m), self.global_bias)).astype(float)
        return np.asarray(predictions) - adj.to_numpy()


def fit_monthly_bias(
    week_ending: pd.Series,
    predicted: np.ndarray,
    actual: pd.Series | np.ndarray,
) -> MonthlyBiasCalibrator:
    resid = np.asarray(predicted) - np.asarray(actual)
    months = pd.to_datetime(week_ending).dt.month.to_numpy()
    bias_by_month = {}
    for month in range(1, 13):
        mask = months == month
        if mask.sum() >= 8:
            bias_by_month[month] = float(resid[mask].mean())
    global_bias = float(resid.mean()) if len(resid) else 0.0
    return MonthlyBiasCalibrator(global_bias=global_bias, bias_by_month=bias_by_month)
