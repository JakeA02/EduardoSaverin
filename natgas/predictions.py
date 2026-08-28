"""Append-only prediction logging."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from natgas.config import LOGS_DIR, ensure_dirs
from natgas.db import connect


def log_prediction(
    *,
    target_week_ending: str,
    model_version: str,
    predicted_change_bcf: float,
    nwp_cycle: str | None = None,
    nwp_source: str | None = None,
    prediction_interval_90: tuple[float, float] | None = None,
    consensus_median: float | None = None,
    actual: float | None = None,
) -> Path:
    """Write prediction to JSON log and DuckDB. Never overwrites prior predictions."""
    ensure_dirs()
    ts = datetime.now(UTC)
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    record: dict[str, Any] = {
        "ts_utc": ts_str,
        "target_week_ending": target_week_ending,
        "model_version": model_version,
        "nwp_cycle": nwp_cycle,
        "nwp_source": nwp_source,
        "predicted_change_bcf": predicted_change_bcf,
        "prediction_interval_90": list(prediction_interval_90) if prediction_interval_90 else None,
        "consensus_median": consensus_median,
        "actual": actual,
    }

    path = LOGS_DIR / f"{ts_str}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")

    interval = prediction_interval_90 or (None, None)
    conn = connect()
    conn.execute(
        """
        INSERT OR IGNORE INTO predictions (
            ts_utc, target_week_ending, model_version, nwp_cycle, nwp_source,
            predicted_change_bcf, prediction_interval_90_lower,
            prediction_interval_90_upper, consensus_median, actual
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ts,
            target_week_ending,
            model_version,
            nwp_cycle,
            nwp_source,
            predicted_change_bcf,
            interval[0],
            interval[1],
            consensus_median,
            actual,
        ],
    )
    conn.close()
    return path


def load_json_log() -> pd.DataFrame:
    """Latest prediction per target week from the append-only JSON log."""
    ensure_dirs()
    records: list[dict[str, Any]] = []
    for path in sorted(LOGS_DIR.glob("*.json")):
        try:
            records.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    if not records:
        return pd.DataFrame(
            columns=[
                "ts_utc",
                "target_week_ending",
                "model_version",
                "nwp_cycle",
                "nwp_source",
                "predicted_change_bcf",
                "interval_lower",
                "interval_upper",
            ]
        )
    df = pd.DataFrame(records)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df["target_week_ending"] = pd.to_datetime(df["target_week_ending"]).dt.date
    if "prediction_interval_90" in df.columns:
        bounds = df["prediction_interval_90"].apply(
            lambda v: v if isinstance(v, list) and len(v) == 2 else [None, None]
        )
        df["interval_lower"] = bounds.map(lambda v: v[0])
        df["interval_upper"] = bounds.map(lambda v: v[1])
    df = df.sort_values("ts_utc")
    return df.groupby("target_week_ending", as_index=False).tail(1).reset_index(drop=True)


def backfill_outcomes(
    target_week_ending: str,
    *,
    consensus_median: float | None = None,
    actual: float | None = None,
) -> int:
    """Update consensus/actual on existing records without touching predictions."""
    conn = connect()
    updated = 0
    if consensus_median is not None:
        conn.execute(
            """
            UPDATE predictions
            SET consensus_median = ?
            WHERE target_week_ending = ? AND consensus_median IS NULL
            """,
            [consensus_median, target_week_ending],
        )
        updated += conn.execute("SELECT changes()").fetchone()[0]
    if actual is not None:
        conn.execute(
            """
            UPDATE predictions
            SET actual = ?
            WHERE target_week_ending = ? AND actual IS NULL
            """,
            [actual, target_week_ending],
        )
        updated += conn.execute("SELECT changes()").fetchone()[0]
    conn.close()
    return updated
