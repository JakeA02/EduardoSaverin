"""Phase 4 consensus metrics and pass/fail gate."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from natgas.models.evaluate import rolling_mae_vs_consensus, surprise_direction_hit_rate


@dataclass
class ConsensusReport:
    n_weeks: int
    model_mae: float
    consensus_mae: float
    rolling_26_model_mae: float | None
    rolling_26_consensus_mae: float | None
    surprise_hit_rate: float
    large_surprise_hit_rate: float | None
    large_surprise_n: int
    interval_coverage_90: float | None
    beats_consensus: bool | None
    gate_pass: bool | None
    gate_reason: str


def interval_coverage(pred_df: pd.DataFrame, actual_col: str = "storage_change_bcf") -> float | None:
    if not {"interval_lower", "interval_upper"}.issubset(pred_df.columns):
        return None
    inside = (pred_df[actual_col] >= pred_df["interval_lower"]) & (
        pred_df[actual_col] <= pred_df["interval_upper"]
    )
    return float(inside.mean())


def large_surprise_hit_rate(
    pred_df: pd.DataFrame,
    consensus_df: pd.DataFrame,
    threshold: float = 5.0,
) -> tuple[float | None, int]:
    merged = rolling_mae_vs_consensus(pred_df, consensus_df, window=1)
    surprise = merged["storage_change_bcf"] - merged["consensus_median"]
    big = surprise.abs() > threshold
    n = int(big.sum())
    if n == 0:
        return None, 0
    pred_s = merged.loc[big, "predicted"] - merged.loc[big, "consensus_median"]
    act_s = merged.loc[big, "storage_change_bcf"] - merged.loc[big, "consensus_median"]
    hits = np.sign(pred_s.to_numpy()) == np.sign(act_s.to_numpy())
    return float(hits.mean()), n


def evaluate_vs_consensus(
    pred_df: pd.DataFrame,
    consensus_df: pd.DataFrame,
    window: int = 26,
    large_threshold: float = 5.0,
) -> ConsensusReport:
    cons = consensus_df.copy()
    preds = pred_df.copy()
    cons["week_ending"] = pd.to_datetime(cons["week_ending"]).dt.date
    preds["week_ending"] = pd.to_datetime(preds["week_ending"]).dt.date
    merged = rolling_mae_vs_consensus(preds, cons, window=window)
    if merged.empty:
        return ConsensusReport(
            n_weeks=0,
            model_mae=float("nan"),
            consensus_mae=float("nan"),
            rolling_26_model_mae=None,
            rolling_26_consensus_mae=None,
            surprise_hit_rate=float("nan"),
            large_surprise_hit_rate=None,
            large_surprise_n=0,
            interval_coverage_90=None,
            beats_consensus=None,
            gate_pass=None,
            gate_reason="No overlapping weeks between predictions and consensus.",
        )

    model_mae = float(merged["model_abs_err"].mean())
    cons_mae = float(merged["consensus_abs_err"].mean())
    rolled = merged.dropna(subset=["rolling_model_mae", "rolling_consensus_mae"])
    r_model = float(rolled["rolling_model_mae"].iloc[-1]) if not rolled.empty else None
    r_cons = float(rolled["rolling_consensus_mae"].iloc[-1]) if not rolled.empty else None
    hit = surprise_direction_hit_rate(preds, cons)
    large_hit, large_n = large_surprise_hit_rate(preds, cons, threshold=large_threshold)
    coverage = interval_coverage(merged) if "interval_lower" in merged.columns else interval_coverage(preds)

    beats = None if r_model is None or r_cons is None else r_model < r_cons
    if r_model is None or r_cons is None:
        reason = f"Need {window} overlapping weeks for the rolling gate; have {len(merged)}."
        gate = None
    elif beats and hit > 0.5:
        reason = "Pass: rolling MAE below consensus and surprise-direction hit rate > 50%."
        gate = True
    else:
        parts = []
        if not beats:
            parts.append(f"rolling MAE {r_model:.2f} is not below consensus {r_cons:.2f}")
        if hit <= 0.5:
            parts.append(f"surprise hit rate {hit:.1%} is not above 50%")
        reason = "Fail: " + "; ".join(parts) + "."
        gate = False

    return ConsensusReport(
        n_weeks=len(merged),
        model_mae=model_mae,
        consensus_mae=cons_mae,
        rolling_26_model_mae=r_model,
        rolling_26_consensus_mae=r_cons,
        surprise_hit_rate=hit,
        large_surprise_hit_rate=large_hit,
        large_surprise_n=large_n,
        interval_coverage_90=coverage,
        beats_consensus=beats,
        gate_pass=gate,
        gate_reason=reason,
    )
