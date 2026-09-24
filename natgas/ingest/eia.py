"""EIA Weekly Natural Gas Storage Report ingestion."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import requests

from natgas.config import (
    EIA_ACTUALS_CSV,
    EIA_API_KEY,
    EIA_BASE_URL,
    RAW_DIR,
    ensure_dirs,
)

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

# Official WNGSR IR JSON is the Thursday 10:30 ET print. API v2 often lags it.
WNGSR_JSON_URL = "https://ir.eia.gov/ngs/wngsr.json"
WNGSR_SERIES = {
    "total lower 48 states": "total",
    "east region": "east",
    "midwest region": "midwest",
    "mountain region": "mountain",
    "pacific region": "pacific",
    "south central region": "south_central",
    "south central salt region": "south_central_salt",
    "south central nonsalt region": "south_central_nonsalt",
}
ACTUALS_COLUMNS = ["week_ending", "storage_change_bcf", "total_bcf", "source"]


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


def parse_wngsr(payload: dict) -> pd.DataFrame:
    """Parse the official IR WNGSR JSON into a 1–2 row storage frame."""
    current = date.fromisoformat(str(payload["current_week"]))
    week_ago_raw = payload.get("week_ago")
    try:
        week_ago = date.fromisoformat(str(week_ago_raw)) if week_ago_raw else None
    except ValueError:
        week_ago = None
    keep = {current} if week_ago is None else {current, week_ago}
    by_week: dict[date, dict] = {}

    def row_for(week: date) -> dict:
        return by_week.setdefault(week, {"week_ending": week, "period": pd.Timestamp(week)})

    for series in payload.get("series", []):
        col = WNGSR_SERIES.get(str(series.get("name", "")).lower())
        if not col:
            continue
        for item in series.get("data", []):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                week = date.fromisoformat(str(item[0]))
            except ValueError:
                continue
            if week not in keep:
                continue
            row_for(week)[col] = float(item[1])
        if col == "total":
            net = series.get("calculated", {}).get("net_change")
            if net is not None:
                row_for(current)["storage_change_bcf"] = float(net)

    if not by_week:
        return pd.DataFrame(columns=["period", "week_ending", "total", "storage_change_bcf"])
    df = pd.DataFrame(by_week.values()).sort_values("week_ending").reset_index(drop=True)
    if "storage_change_bcf" not in df.columns and "total" in df.columns:
        df["storage_change_bcf"] = df["total"].diff()
    return df


def fetch_wngsr() -> pd.DataFrame:
    """Official Thursday print. Empty frame if the IR feed is unreachable."""
    try:
        resp = requests.get(WNGSR_JSON_URL, timeout=30)
        resp.raise_for_status()
        return parse_wngsr(resp.json())
    except (OSError, TypeError, ValueError, requests.RequestException):
        return pd.DataFrame()


def merge_wngsr(storage: pd.DataFrame, wngsr: pd.DataFrame) -> pd.DataFrame:
    """Overlay the official WNGSR print onto an API (or empty) storage frame."""
    if wngsr is None or wngsr.empty:
        return storage
    add = wngsr.copy()
    add["week_ending"] = pd.to_datetime(add["week_ending"]).dt.date
    official_change = {
        row["week_ending"]: row["storage_change_bcf"]
        for _, row in add.iterrows()
        if pd.notna(row.get("storage_change_bcf"))
    }
    if storage is None or storage.empty:
        out = add
    else:
        out = storage.copy()
        out["week_ending"] = pd.to_datetime(out["week_ending"]).dt.date
        out = out[~out["week_ending"].isin(set(add["week_ending"]))]
        cols = list(dict.fromkeys([*out.columns, *add.columns]))
        out = pd.concat([out.reindex(columns=cols), add.reindex(columns=cols)], ignore_index=True)
    out = out.sort_values("week_ending").reset_index(drop=True)
    if "period" not in out.columns:
        out["period"] = pd.to_datetime(out["week_ending"])
    if "total" in out.columns:
        computed = out["total"].diff()
        if "storage_change_bcf" not in out.columns:
            out["storage_change_bcf"] = computed
        else:
            out["storage_change_bcf"] = out["storage_change_bcf"].fillna(computed)
    for week, change in official_change.items():
        out.loc[out["week_ending"] == week, "storage_change_bcf"] = change
    return out


def save_vintage_snapshot(df: pd.DataFrame) -> Path:
    """Append-only Parquet snapshot keyed by pull date."""
    ensure_dirs()
    vintage = datetime.now(UTC).date().isoformat()
    out_dir = RAW_DIR / "eia" / f"vintage={vintage}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "storage.parquet"
    df.to_parquet(path, index=False)
    return path


def load_tracked_actuals() -> pd.DataFrame:
    ensure_dirs()
    if not EIA_ACTUALS_CSV.exists():
        return pd.DataFrame(columns=ACTUALS_COLUMNS)
    df = pd.read_csv(EIA_ACTUALS_CSV)
    if df.empty:
        return pd.DataFrame(columns=ACTUALS_COLUMNS)
    df["week_ending"] = pd.to_datetime(df["week_ending"]).dt.date
    df["storage_change_bcf"] = pd.to_numeric(df["storage_change_bcf"], errors="coerce")
    if "total_bcf" in df.columns:
        df["total_bcf"] = pd.to_numeric(df["total_bcf"], errors="coerce")
    else:
        df["total_bcf"] = pd.NA
    if "source" not in df.columns:
        df["source"] = "eia"
    return df.dropna(subset=["week_ending", "storage_change_bcf"])


def upsert_tracked_actuals(storage: pd.DataFrame, *, source: str = "eia") -> Path:
    """Persist weekly actuals in git so the dashboard does not depend on gitignored parquet."""
    ensure_dirs()
    incoming = storage.copy()
    incoming["week_ending"] = pd.to_datetime(incoming["week_ending"]).dt.date
    if "total" in incoming.columns and "total_bcf" not in incoming.columns:
        incoming["total_bcf"] = incoming["total"]
    if "total_bcf" not in incoming.columns:
        incoming["total_bcf"] = pd.NA
    incoming["storage_change_bcf"] = pd.to_numeric(incoming["storage_change_bcf"], errors="coerce")
    incoming = incoming.dropna(subset=["week_ending", "storage_change_bcf"])
    if "source" not in incoming.columns:
        incoming["source"] = source
    incoming = incoming[ACTUALS_COLUMNS]
    existing = load_tracked_actuals()
    merged = pd.concat([existing, incoming], ignore_index=True)
    merged = merged.dropna(subset=["week_ending", "storage_change_bcf"])
    merged = merged.drop_duplicates(subset=["week_ending"], keep="last")
    merged = merged.sort_values("week_ending")
    out = merged[ACTUALS_COLUMNS].copy()
    out["week_ending"] = pd.to_datetime(out["week_ending"]).dt.strftime("%Y-%m-%d")
    out.to_csv(EIA_ACTUALS_CSV, index=False)
    return EIA_ACTUALS_CSV


def _backfill_actuals(storage: pd.DataFrame) -> None:
    from natgas.db import init_schema
    from natgas.predictions import backfill_outcomes

    init_schema()
    rows = storage.dropna(subset=["week_ending", "storage_change_bcf"])
    for _, row in rows.iterrows():
        week = row["week_ending"]
        week_s = week.isoformat() if hasattr(week, "isoformat") else str(week)
        backfill_outcomes(week_s, actual=float(row["storage_change_bcf"]))


def ingest(start: str = "2010-01-01") -> pd.DataFrame:
    df = fetch_storage(start=start)
    wngsr = fetch_wngsr()
    df = merge_wngsr(df, wngsr)
    save_vintage_snapshot(df)
    upsert_tracked_actuals(df, source="wngsr" if not wngsr.empty else "eia-api")
    _backfill_actuals(df)
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


def load_actuals() -> pd.DataFrame:
    """Weekly storage change: vintage, tracked CSV, then live WNGSR (official print wins)."""
    frames: list[pd.DataFrame] = []

    try:
        vintage = load_latest_vintage()
        vintage["week_ending"] = pd.to_datetime(vintage["week_ending"]).dt.date
        frames.append(vintage[["week_ending", "storage_change_bcf"]].dropna())
    except (FileNotFoundError, OSError):
        pass

    try:
        from natgas.features.balance import load_panel

        panel = load_panel()
        panel["week_ending"] = pd.to_datetime(panel["week_ending"]).dt.date
        frames.append(panel[["week_ending", "storage_change_bcf"]].dropna())
    except (FileNotFoundError, OSError):
        pass

    tracked = load_tracked_actuals()
    if not tracked.empty:
        frames.append(tracked[["week_ending", "storage_change_bcf"]].dropna())

    wngsr = fetch_wngsr()
    if not wngsr.empty:
        frames.append(wngsr[["week_ending", "storage_change_bcf"]].dropna())

    if not frames:
        return pd.DataFrame(columns=["week_ending", "storage_change_bcf"])
    out = pd.concat(frames, ignore_index=True)
    out["week_ending"] = pd.to_datetime(out["week_ending"]).dt.date
    out["storage_change_bcf"] = pd.to_numeric(out["storage_change_bcf"], errors="coerce")
    out = out.dropna(subset=["week_ending", "storage_change_bcf"])
    return out.drop_duplicates(subset=["week_ending"], keep="last").sort_values("week_ending").reset_index(drop=True)
