"""DuckDB warehouse helpers."""

from __future__ import annotations

import duckdb

from natgas.config import WAREHOUSE_PATH, ensure_dirs


def connect() -> duckdb.DuckDBPyConnection:
    ensure_dirs()
    return duckdb.connect(str(WAREHOUSE_PATH))


def init_schema(conn: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    own_conn = conn is None
    db = conn or connect()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS weekly_panel (
            week_ending DATE PRIMARY KEY,
            storage_level_bcf DOUBLE,
            storage_change_bcf DOUBLE,
            cpc_hdd DOUBLE,
            cpc_cdd DOUBLE,
            gw_hdd DOUBLE,
            gw_cdd DOUBLE,
            production_bcf_d DOUBLE,
            net_imports_bcf_d DOUBLE,
            power_burn_bcf_d DOUBLE,
            industrial_bcf_d DOUBLE,
            lng_feedgas_bcf_d DOUBLE,
            exports_mx_bcf_d DOUBLE,
            vintage_date DATE,
            updated_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            ts_utc TIMESTAMP,
            target_week_ending DATE,
            model_version VARCHAR,
            nwp_cycle TIMESTAMP,
            nwp_source VARCHAR,
            predicted_change_bcf DOUBLE,
            prediction_interval_90_lower DOUBLE,
            prediction_interval_90_upper DOUBLE,
            consensus_median DOUBLE,
            actual DOUBLE,
            PRIMARY KEY (ts_utc, target_week_ending, model_version)
        )
        """
    )
    if own_conn:
        db.close()
    return db
