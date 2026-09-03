"""Prediction log DB helpers."""

from natgas.db import connect, init_schema
from natgas.predictions import backfill_outcomes, load_json_log, log_prediction


def test_log_prefers_cpc_over_weathernext(tmp_path, monkeypatch) -> None:
    from natgas import config

    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "predictions")
    monkeypatch.setattr(config, "WAREHOUSE_PATH", tmp_path / "warehouse.duckdb")
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    init_schema()
    log_prediction(
        target_week_ending="2026-08-28",
        model_version="test",
        predicted_change_bcf=10.0,
        nwp_source="cpc-ndfd",
        horizon_weeks=1,
    )
    log_prediction(
        target_week_ending="2026-08-28",
        model_version="test",
        predicted_change_bcf=99.0,
        nwp_source="weathernext3",
        horizon_weeks=1,
    )
    latest = load_json_log()
    assert len(latest) == 1
    assert latest.iloc[0]["predicted_change_bcf"] == 10.0
    assert latest.iloc[0]["nwp_source"] == "cpc-ndfd"


def test_log_filename_includes_source_and_horizon(tmp_path, monkeypatch) -> None:
    from natgas import config

    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "predictions")
    monkeypatch.setattr(config, "WAREHOUSE_PATH", tmp_path / "warehouse.duckdb")
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    init_schema()
    path = log_prediction(
        target_week_ending="2026-09-04",
        model_version="test",
        predicted_change_bcf=12.0,
        nwp_source="weathernext3",
        horizon_weeks=2,
    )
    assert "weathernext3-h2.json" in path.name


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
