"""Phase 4 consensus gate tests. Consensus is never a model feature."""

from datetime import date, timedelta

import pandas as pd

from natgas.models.consensus_eval import evaluate_vs_consensus, interval_coverage
from natgas.models.evaluate import rolling_mae_vs_consensus


def _weeks(n: int, start: date = date(2024, 1, 5)) -> list[date]:
    return [start + timedelta(days=7 * i) for i in range(n)]


def test_gate_passes_when_better_mae_and_direction() -> None:
    weeks = _weeks(30)
    actual = pd.Series(range(30), dtype=float) * 2  # 0, 2, 4, ...
    consensus = actual + 10  # systematically high
    predicted = actual + 1  # closer, same surprise sign vs consensus
    pred = pd.DataFrame(
        {
            "week_ending": weeks,
            "storage_change_bcf": actual,
            "predicted": predicted,
            "interval_lower": predicted - 5,
            "interval_upper": predicted + 5,
        }
    )
    cons = pd.DataFrame({"week_ending": weeks, "consensus_median": consensus})
    report = evaluate_vs_consensus(pred, cons)
    assert report.n_weeks == 30
    assert report.model_mae < report.consensus_mae
    assert report.surprise_hit_rate == 1.0
    assert report.gate_pass is True
    assert report.interval_coverage_90 == 1.0


def test_gate_fails_when_worse_than_consensus() -> None:
    weeks = _weeks(30)
    actual = pd.Series([0.0] * 30)
    cons_med = pd.Series([1.0] * 30)
    predicted = pd.Series([20.0] * 30)
    pred = pd.DataFrame(
        {"week_ending": weeks, "storage_change_bcf": actual, "predicted": predicted}
    )
    cons = pd.DataFrame({"week_ending": weeks, "consensus_median": cons_med})
    report = evaluate_vs_consensus(pred, cons)
    assert report.gate_pass is False
    assert "rolling MAE" in report.gate_reason


def test_gate_incomplete_without_26_weeks() -> None:
    weeks = _weeks(10)
    pred = pd.DataFrame(
        {"week_ending": weeks, "storage_change_bcf": [0.0] * 10, "predicted": [1.0] * 10}
    )
    cons = pd.DataFrame({"week_ending": weeks, "consensus_median": [0.5] * 10})
    report = evaluate_vs_consensus(pred, cons)
    assert report.gate_pass is None
    assert report.n_weeks == 10


def test_large_surprise_hit_rate_subset() -> None:
    weeks = _weeks(6)
    pred = pd.DataFrame(
        {
            "week_ending": weeks,
            "storage_change_bcf": [0, 0, 0, 20, 20, 20],
            "predicted": [1, 1, 1, 18, 5, 18],
            "consensus_median": [0, 0, 0, 10, 10, 10],
        }
    )
    cons = pred[["week_ending", "consensus_median"]]
    report = evaluate_vs_consensus(pred, cons, window=26, large_threshold=5.0)
    assert report.large_surprise_n == 3
    # Weeks 3-5: surprises +10,+10,+10; preds +8,-5,+8 → hit, miss, hit
    assert report.large_surprise_hit_rate == 2 / 3


def test_interval_coverage() -> None:
    df = pd.DataFrame(
        {
            "storage_change_bcf": [0.0, 10.0, 20.0],
            "interval_lower": [-1, 9, 0],
            "interval_upper": [1, 11, 1],
        }
    )
    assert interval_coverage(df) == 2 / 3


def test_consensus_merge_does_not_require_matching_index() -> None:
    pred = pd.DataFrame(
        {
            "week_ending": ["2026-01-02", "2026-01-09"],
            "storage_change_bcf": [1.0, 2.0],
            "predicted": [1.5, 2.5],
        }
    )
    cons = pd.DataFrame({"week_ending": [date(2026, 1, 9)], "consensus_median": [3.0]})
    merged = rolling_mae_vs_consensus(pred, cons, window=1)
    assert len(merged) == 1
    assert merged.iloc[0]["consensus_abs_err"] == 1.0
