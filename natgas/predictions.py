"""Append-only prediction logging."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from natgas import config
from natgas.config import ensure_dirs
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
    horizon_weeks: int = 1,
) -> Path:
    """Write prediction to JSON log and DuckDB. Never overwrites prior predictions."""
    ensure_dirs()
    ts = datetime.now(UTC)
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    source_slug = (nwp_source or "na").replace(":", "-")
    fname = f"{ts_str}-{source_slug}-h{horizon_weeks}.json"

    record: dict[str, Any] = {
        "ts_utc": ts_str,
        "target_week_ending": target_week_ending,
        "model_version": model_version,
        "nwp_cycle": nwp_cycle,
        "nwp_source": nwp_source,
        "horizon_weeks": int(horizon_weeks),
        "predicted_change_bcf": predicted_change_bcf,
        "prediction_interval_90": list(prediction_interval_90) if prediction_interval_90 else None,
        "consensus_median": consensus_median,
        "actual": actual,
    }

    path = config.LOGS_DIR / fname
    path.write_text(json.dumps(record, indent=2) + "\n")

    interval = prediction_interval_90 or (None, None)
    conn = connect()
    conn.execute(
        """
        INSERT OR IGNORE INTO predictions (
            ts_utc, target_week_ending, model_version, nwp_cycle, nwp_source,
            predicted_change_bcf, prediction_interval_90_lower,
            prediction_interval_90_upper, consensus_median, actual, horizon_weeks
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            int(horizon_weeks),
        ],
    )
    conn.close()
    return path


def _read_log_records() -> pd.DataFrame:
    ensure_dirs()
    records: list[dict[str, Any]] = []
    for path in sorted(config.LOGS_DIR.glob("*.json")):
        try:
            records.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    empty_cols = [
        "ts_utc",
        "target_week_ending",
        "model_version",
        "nwp_cycle",
        "nwp_source",
        "horizon_weeks",
        "predicted_change_bcf",
        "interval_lower",
        "interval_upper",
    ]
    if not records:
        return pd.DataFrame(columns=empty_cols)
    df = pd.DataFrame(records)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df["target_week_ending"] = pd.to_datetime(df["target_week_ending"]).dt.date
    if "horizon_weeks" not in df.columns:
        df["horizon_weeks"] = 1
    df["horizon_weeks"] = pd.to_numeric(df["horizon_weeks"], errors="coerce").fillna(1).astype(int)
    if "nwp_source" not in df.columns:
        df["nwp_source"] = None
    if "prediction_interval_90" in df.columns:
        bounds = df["prediction_interval_90"].apply(
            lambda v: v if isinstance(v, list) and len(v) == 2 else [None, None]
        )
        df["interval_lower"] = bounds.map(lambda v: v[0])
        df["interval_upper"] = bounds.map(lambda v: v[1])
    return df.sort_values("ts_utc")


def load_json_log(*, prefer_source: str | None = "cpc-ndfd") -> pd.DataFrame:
    """Latest prediction per target week. Prefers the production NWP source when present."""
    df = _read_log_records()
    if df.empty:
        return df
    if prefer_source:
        preferred = df[df["nwp_source"] == prefer_source]
        fallback = df[~df["target_week_ending"].isin(set(preferred["target_week_ending"]))]
        df = pd.concat([preferred, fallback], ignore_index=True).sort_values("ts_utc")
    return df.groupby("target_week_ending", as_index=False).tail(1).reset_index(drop=True)


def load_json_log_by_source() -> pd.DataFrame:
    """Latest prediction per (week, nwp_source, horizon_weeks)."""
    df = _read_log_records()
    if df.empty:
        return df
    df["nwp_source"] = df["nwp_source"].fillna("unknown")
    return (
        df.groupby(["target_week_ending", "nwp_source", "horizon_weeks"], as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )


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
        rows = conn.execute(
            """
            UPDATE predictions
            SET consensus_median = ?
            WHERE target_week_ending = ? AND consensus_median IS NULL
            RETURNING 1
            """,
            [consensus_median, target_week_ending],
        ).fetchall()
        updated += len(rows)
    if actual is not None:
        rows = conn.execute(
            """
            UPDATE predictions
            SET actual = ?
            WHERE target_week_ending = ? AND actual IS NULL
            RETURNING 1
            """,
            [actual, target_week_ending],
        ).fetchall()
        updated += len(rows)
    conn.close()
    return updated
