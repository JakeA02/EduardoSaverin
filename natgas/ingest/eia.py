"""EIA Weekly Natural Gas Storage Report ingestion."""

from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path

import pandas as pd
import requests

from natgas.config import EIA_API_KEY, EIA_BASE_URL, RAW_DIR, ensure_dirs

# EIA API v2 series codes for working gas in Bcf (route: natural-gas/stor/wkly).
STORAGE_SERIES = {
    "total": "NW2_EPG0_SWO_R48_BCF",
    "east": "NW2_EPG0_SAE_R1_BCF",
    "midwest": "NW2_EPG0_SAE_R2_BCF",
    "mountain": "NW2_EPG0_SAE_R3_BCF",
    "pacific": "NW2_EPG0_SAE_R4_BCF",
    "south_central": "NW2_EPG0_SAE_R5_BCF",
    "south_central_salt": "NW2_EPG0_SAS_R5_BCF",
    "south_central_nonsalt": "NW2_EPG0_SAE_R5N_BCF",
}


def _fetch_series(series_code: str, start: str | None = None) -> pd.DataFrame:
    if not EIA_API_KEY:
        raise ValueError("Set EIA_API_KEY in .env (free at https://www.eia.gov/opendata/register.php)")

    rows: list[dict] = []
    offset = 0
    page_size = 5000

    while True:
        params: dict[str, str | int] = {
            "api_key": EIA_API_KEY,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": series_code,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": offset,
            "length": page_size,
        }
        if start:
            params["start"] = start

        url = f"{EIA_BASE_URL}/natural-gas/stor/wkly/data/"
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()

        batch = payload.get("response", {}).get("data", [])
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    if not rows:
        return pd.DataFrame(columns=["period", "value", "series"])

    df = pd.DataFrame(rows)
    df["series"] = series_code
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["period"] = pd.to_datetime(df["period"])
    return df


def fetch_storage(start: str = "2010-01-01") -> pd.DataFrame:
    """Fetch all storage series and return wide-format levels in Bcf."""
    frames = []
    for name, code in STORAGE_SERIES.items():
        df = _fetch_series(code, start=start)
        if df.empty:
            continue
        df = df.rename(columns={"value": name})[["period", name]]
        frames.append(df)

    if not frames:
        raise RuntimeError("No EIA storage data returned; check API key and series codes.")

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="period", how="outer")

    merged = merged.sort_values("period").reset_index(drop=True)
    merged["week_ending"] = merged["period"].dt.date
    merged["storage_change_bcf"] = merged["total"].diff()
    return merged


def save_vintage_snapshot(df: pd.DataFrame) -> Path:
    """Append-only Parquet snapshot keyed by pull date."""
    ensure_dirs()
    vintage = datetime.now(UTC).date().isoformat()
    out_dir = RAW_DIR / "eia" / f"vintage={vintage}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "storage.parquet"
    df.to_parquet(path, index=False)
    return path


def ingest(start: str = "2010-01-01") -> pd.DataFrame:
    df = fetch_storage(start=start)
    save_vintage_snapshot(df)
    return df


def load_latest_vintage() -> pd.DataFrame:
    """Load most recent EIA snapshot from disk."""
    base = RAW_DIR / "eia"
    if not base.exists():
        raise FileNotFoundError("No EIA snapshots found. Run `natgas ingest eia` first.")
    vintages = sorted(base.glob("vintage=*/storage.parquet"))
    if not vintages:
        raise FileNotFoundError("No EIA parquet files found.")
    return pd.read_parquet(vintages[-1])
