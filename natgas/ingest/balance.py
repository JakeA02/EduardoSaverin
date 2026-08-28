"""Ingest slow-moving EIA balance terms."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from pathlib import Path

import pandas as pd
import requests

from natgas.calendar import week_ending_friday
from natgas.config import EIA_API_KEY, EIA_BASE_URL, RAW_DIR, ensure_dirs


def _eia_monthly(route: str, facets: dict[str, str], start: str = "2010-01") -> pd.DataFrame:
    if not EIA_API_KEY:
        raise ValueError("Set EIA_API_KEY in .env")

    rows: list[dict] = []
    offset = 0
    page_size = 5000

    while True:
        params: dict[str, str | int] = {
            "api_key": EIA_API_KEY,
            "frequency": "monthly",
            "data[0]": "value",
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": offset,
            "length": page_size,
            "start": start,
        }
        for facet, value in facets.items():
            params[f"facets[{facet}][]"] = value

        url = f"{EIA_BASE_URL}/{route}"
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        batch = resp.json().get("response", {}).get("data", [])
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    if not rows:
        return pd.DataFrame(columns=["period", "value"])

    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["period"] = pd.to_datetime(df["period"])
    return df[["period", "value"]].dropna()


def _mmcf_month_to_bcf_daily(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Convert monthly total MMcf to average daily Bcf/d."""
    if df.empty:
        return pd.DataFrame(columns=["period", col])

    out = df.copy()
    out["days"] = out["period"].dt.days_in_month
    out[col] = (out["value"] / 1000.0) / out["days"]
    return out[["period", col]]


def fetch_dry_production_monthly() -> pd.DataFrame:
    """US marketed natural gas production as average daily Bcf/d."""
    monthly = _eia_monthly(
        "natural-gas/prod/sum/data/",
        {"process": "VGM", "duoarea": "NUS"},
    )
    return _mmcf_month_to_bcf_daily(monthly, "production_bcf_d")


def fetch_imports_monthly() -> pd.DataFrame:
    """US natural gas imports (monthly total MMcf)."""
    return _eia_monthly("natural-gas/move/impc/data/", {"process": "IM0"})


def fetch_exports_monthly() -> pd.DataFrame:
    """US natural gas exports (monthly total MMcf)."""
    return _eia_monthly("natural-gas/move/expc/data/", {"process": "EEX"})


def fetch_net_imports_monthly() -> pd.DataFrame:
    """Net imports (imports minus exports) as average daily Bcf/d."""
    imports = fetch_imports_monthly().rename(columns={"value": "imports_mmcf"})
    exports = fetch_exports_monthly().rename(columns={"value": "exports_mmcf"})
    if imports.empty and exports.empty:
        return pd.DataFrame(columns=["period", "net_imports_bcf_d"])

    merged = imports.merge(exports, on="period", how="outer").sort_values("period")
    merged["value"] = merged["imports_mmcf"].fillna(0) - merged["exports_mmcf"].fillna(0)
    return _mmcf_month_to_bcf_daily(merged[["period", "value"]], "net_imports_bcf_d")


def monthly_to_weekly(monthly: pd.DataFrame, col: str) -> pd.DataFrame:
    """Forward-fill monthly values to weekly Friday dates."""
    if monthly.empty:
        return pd.DataFrame(columns=["week_ending", col])

    monthly = monthly.sort_values("period")
    start = monthly["period"].min().date()
    end = datetime.now(UTC).date()
    weeks = []
    current = week_ending_friday(start)
    while current <= end:
        weeks.append(current)
        current += timedelta(days=7)

    weekly = pd.DataFrame({"week_ending": weeks})
    monthly = monthly.rename(columns={"period": "month"})
    monthly["month"] = monthly["month"].dt.to_period("M").dt.to_timestamp()
    weekly["month"] = pd.to_datetime(weekly["week_ending"]).dt.to_period("M").dt.to_timestamp()
    out = weekly.merge(monthly[["month", col]], on="month", how="left")
    out[col] = out[col].ffill()
    return out[["week_ending", col]]


def ingest_balance_terms() -> pd.DataFrame:
    """Fetch slow-moving balance terms and expand to weekly."""
    prod = monthly_to_weekly(fetch_dry_production_monthly(), "production_bcf_d")
    imports = monthly_to_weekly(fetch_net_imports_monthly(), "net_imports_bcf_d")
    return prod.merge(imports, on="week_ending", how="outer")


def save_balance_snapshot(df: pd.DataFrame) -> Path:
    ensure_dirs()
    vintage = datetime.now(UTC).date().isoformat()
    out_dir = RAW_DIR / "eia_balance" / f"vintage={vintage}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "balance.parquet"
    df.to_parquet(path, index=False)
    return path
