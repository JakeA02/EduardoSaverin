"""Build the weekly analytical panel in DuckDB."""

from __future__ import annotations

from datetime import datetime, UTC

import pandas as pd

from natgas.db import connect, init_schema
from natgas.features.weights import apply_gwdd, calibrate_weights
from natgas.ingest.balance import ingest_balance_terms, save_balance_snapshot
from natgas.ingest.cpc import division_weekly, ingest as ingest_cpc, load_latest_vintage as load_cpc
from natgas.ingest.eia import ingest as ingest_eia, load_latest_vintage as load_eia


def build_panel(*, refresh: bool = False) -> pd.DataFrame:
    """Merge EIA storage, CPC degree days, GWDD, and balance terms."""
    if refresh:
        storage = ingest_eia()
        cpc_raw = ingest_cpc()
        try:
            balance = ingest_balance_terms()
            save_balance_snapshot(balance)
        except Exception as exc:
            balance = pd.DataFrame(columns=["week_ending", "production_bcf_d", "net_imports_bcf_d"])
            print(f"Warning: balance terms skipped ({exc})")
    else:
        storage = load_eia()
        cpc_raw = load_cpc()
        try:
            from natgas.ingest.balance import monthly_to_weekly, fetch_dry_production_monthly

            balance = monthly_to_weekly(fetch_dry_production_monthly(), "production_bcf_d")
        except Exception:
            balance = pd.DataFrame(columns=["week_ending", "production_bcf_d"])

    cpc_nat = (
        cpc_raw[cpc_raw["region"] == "NATIONAL"]
        if "NATIONAL" in cpc_raw["region"].values
        else None
    )
    if cpc_nat is not None and not cpc_nat.empty:
        cpc_weekly = cpc_nat.rename(columns={"hdd": "cpc_hdd", "cdd": "cpc_cdd"})[
            ["week_ending", "cpc_hdd", "cpc_cdd"]
        ]
    else:
        div = division_weekly(cpc_raw)
        cpc_weekly = (
            div.groupby("week_ending", as_index=False)[["hdd", "cdd"]]
            .sum()
            .rename(columns={"hdd": "cpc_hdd", "cdd": "cpc_cdd"})
        )

    div = division_weekly(cpc_raw)
    emp_weights = calibrate_weights(div, storage.set_index("week_ending")["storage_change_bcf"])
    gwdd = apply_gwdd(div, emp_weights)

    panel = storage[["week_ending", "total", "storage_change_bcf"]].rename(columns={"total": "storage_level_bcf"})
    panel = panel.merge(cpc_weekly, on="week_ending", how="inner")
    panel = panel.merge(gwdd, on="week_ending", how="inner")
    if not balance.empty:
        panel = panel.merge(balance, on="week_ending", how="left")

    panel = panel.dropna(subset=["storage_change_bcf", "cpc_hdd", "gw_hdd"])
    panel["vintage_date"] = datetime.now(UTC).date()

    conn = connect()
    init_schema(conn)
    conn.execute("DELETE FROM weekly_panel")
    conn.register("panel_df", panel)
    conn.execute(
        """
        INSERT INTO weekly_panel (
            week_ending, storage_level_bcf, storage_change_bcf,
            cpc_hdd, cpc_cdd, gw_hdd, gw_cdd,
            production_bcf_d, vintage_date
        )
        SELECT
            week_ending, storage_level_bcf, storage_change_bcf,
            cpc_hdd, cpc_cdd, gw_hdd, gw_cdd,
            production_bcf_d, vintage_date
        FROM panel_df
        """
    )
    conn.close()
    return panel


def load_panel() -> pd.DataFrame:
    conn = connect()
    init_schema(conn)
    df = conn.execute("SELECT * FROM weekly_panel ORDER BY week_ending").df()
    conn.close()
    if df.empty:
        raise FileNotFoundError("weekly_panel is empty. Run `natgas build-panel`.")
    return df
