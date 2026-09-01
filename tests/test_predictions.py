"""Prediction log DB helpers."""

from datetime import UTC, datetime

from natgas.db import connect, init_schema
from natgas.predictions import backfill_outcomes, log_prediction


def test_backfill_outcomes_counts_duckdb_updates(tmp_path, monkeypatch) -> None:
    from natgas import config

    db_path = tmp_path / "warehouse.duckdb"
    monkeypatch.setattr(config, "WAREHOUSE_PATH", db_path)
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "predictions")
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    init_schema()
    log_prediction(
        target_week_ending="2026-08-21",
        model_version="test",
        predicted_change_bcf=30.0,
    )
    n = backfill_outcomes("2026-08-21", consensus_median=20.0, actual=15.0)
    assert n == 2

    conn = connect()
    row = conn.execute(
        """
        SELECT consensus_median, actual
        FROM predictions
        WHERE target_week_ending = '2026-08-21'
        """
    ).fetchone()
    conn.close()
    assert row == (20.0, 15.0)
