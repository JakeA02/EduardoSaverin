"""NOAA CPC population-weighted degree days ingestion."""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta, UTC
from pathlib import Path

import pandas as pd
import requests

from natgas.calendar import week_ending_friday
from natgas.config import CPC_DIVISIONS, RAW_DIR, ensure_dirs

CPC_FTP_BASE = "https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_data"

# Census division codes in CPC Population.*.txt files.
DIVISION_CODE_TO_NAME = {
    "1": "NEW ENGLAND",
    "2": "EAST NORTH CENTRAL",
    "3": "WEST NORTH CENTRAL",
    "4": "SOUTH ATLANTIC",
    "5": "EAST SOUTH CENTRAL",
    "6": "WEST SOUTH CENTRAL",
    "7": "MOUNTAIN",
    "8": "PACIFIC",
    "9": "OTHER",  # AK/HI/territories; excluded from lower-48 model
    "CONUS": "NATIONAL",
}


def _year_urls(year: int) -> tuple[str, str]:
    base = f"{CPC_FTP_BASE}/{year}"
    return (
        f"{base}/Population.Heating.txt",
        f"{base}/Population.Cooling.txt",
    )


def _latest_urls() -> tuple[str, str]:
    base = f"{CPC_FTP_BASE}/latest"
    return (
        f"{base}/Population.Heating.txt",
        f"{base}/Population.Cooling.txt",
    )


def _parse_pipe_file(text: str, value_name: str) -> pd.DataFrame:
    """Parse CPC pipe-delimited daily degree day file to long format."""
    header_line: str | None = None
    data_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Region|"):
            header_line = line
            continue
        if header_line and "|" in line:
            data_lines.append(line)

    if not header_line:
        return pd.DataFrame(columns=["date", "region", value_name])

    header = header_line.split("|")
    date_cols = [c for c in header[1:] if c.isdigit() and len(c) == 8]

    records: list[dict] = []
    for line in data_lines:
        parts = line.split("|")
        region_code = parts[0].strip()
        region = DIVISION_CODE_TO_NAME.get(region_code)
        if region is None:
            continue
        for dcol, val in zip(date_cols, parts[1 : len(date_cols) + 1]):
            try:
                value = float(val)
            except ValueError:
                continue
            if value < 0:
                # CPC missing sentinel is -9999; degree days cannot be negative.
                continue
            records.append(
                {
                    "date": datetime.strptime(dcol, "%Y%m%d").date(),
                    "region": region,
                    value_name: value,
                }
            )

    return pd.DataFrame(records)


def _fetch_url(url: str) -> str | None:
    resp = requests.get(url, timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text


def fetch_daily(start_year: int = 2010, end_year: int | None = None) -> pd.DataFrame:
    """Fetch daily population-weighted HDD/CDD for all census divisions."""
    end_year = end_year or datetime.now(UTC).year
    hdd_frames: list[pd.DataFrame] = []
    cdd_frames: list[pd.DataFrame] = []

    for year in range(start_year, end_year + 1):
        hdd_url, cdd_url = _year_urls(year)
        hdd_text = _fetch_url(hdd_url)
        cdd_text = _fetch_url(cdd_url)
        if hdd_text:
            hdd_frames.append(_parse_pipe_file(hdd_text, "hdd"))
        if cdd_text:
            cdd_frames.append(_parse_pipe_file(cdd_text, "cdd"))

    # Merge in latest partial year (may extend beyond archived year files).
    hdd_latest, cdd_latest = _latest_urls()
    for url, frames, col in [
        (hdd_latest, hdd_frames, "hdd"),
        (cdd_latest, cdd_frames, "cdd"),
    ]:
        text = _fetch_url(url)
        if text:
            frames.append(_parse_pipe_file(text, col))

    if not hdd_frames or not cdd_frames:
        raise RuntimeError("Failed to download CPC degree day files.")

    hdd = pd.concat(hdd_frames, ignore_index=True)
    cdd = pd.concat(cdd_frames, ignore_index=True)
    daily = hdd.merge(cdd, on=["date", "region"], how="outer")
    daily = daily.drop_duplicates(subset=["date", "region"], keep="last")
    return daily.sort_values(["date", "region"]).reset_index(drop=True)


def daily_to_storage_weeks(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily degree days to EIA storage weeks (Sat–Fri, ending Friday)."""
    if daily.empty:
        return pd.DataFrame(columns=["week_ending", "region", "hdd", "cdd"])

    daily = daily.copy()

    def _week_end(d: date) -> date:
        # EIA week: Saturday through Friday.
        if d.weekday() == 5:  # Saturday -> next Friday
            return d + timedelta(days=6)
        if d.weekday() == 6:  # Sunday -> next Friday
            return d + timedelta(days=5)
        return week_ending_friday(d)

    daily["week_ending"] = daily["date"].apply(_week_end)

    weekly = (
        daily.groupby(["week_ending", "region"], as_index=False)[["hdd", "cdd"]]
        .sum()
        .sort_values(["week_ending", "region"])
        .reset_index(drop=True)
    )
    return weekly


def fetch_historical(start_year: int = 2010, end_year: int | None = None) -> pd.DataFrame:
    daily = fetch_daily(start_year=start_year, end_year=end_year)
    return daily_to_storage_weeks(daily)


def national_weekly(df: pd.DataFrame | None = None) -> pd.DataFrame:
    if df is None:
        df = fetch_historical()
    nat = df[df["region"] == "NATIONAL"].copy()
    if nat.empty:
        div = df[df["region"].isin(CPC_DIVISIONS)]
        nat = div.groupby("week_ending", as_index=False)[["hdd", "cdd"]].sum()
    nat = nat.rename(columns={"hdd": "cpc_hdd", "cdd": "cpc_cdd"})
    return nat[["week_ending", "cpc_hdd", "cpc_cdd"]].sort_values("week_ending").reset_index(drop=True)


def division_weekly(df: pd.DataFrame | None = None) -> pd.DataFrame:
    if df is None:
        df = fetch_historical()
    return df[df["region"].isin(CPC_DIVISIONS)].copy()


def save_vintage_snapshot(df: pd.DataFrame) -> Path:
    ensure_dirs()
    vintage = datetime.now(UTC).date().isoformat()
    out_dir = RAW_DIR / "cpc" / f"vintage={vintage}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "degree_days.parquet"
    df.to_parquet(path, index=False)
    return path


def save_daily_snapshot(df: pd.DataFrame) -> Path:
    ensure_dirs()
    vintage = datetime.now(UTC).date().isoformat()
    out_dir = RAW_DIR / "cpc" / f"vintage={vintage}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "degree_days_daily.parquet"
    df.to_parquet(path, index=False)
    return path


def ingest(start_year: int = 2010) -> pd.DataFrame:
    daily = fetch_daily(start_year=start_year)
    save_daily_snapshot(daily)
    weekly = daily_to_storage_weeks(daily)
    save_vintage_snapshot(weekly)
    return weekly


def load_latest_daily() -> pd.DataFrame:
    base = RAW_DIR / "cpc"
    vintages = sorted(base.glob("vintage=*/degree_days_daily.parquet")) if base.exists() else []
    if not vintages:
        raise FileNotFoundError("No CPC daily snapshots. Run `natgas ingest cpc`.")
    return pd.read_parquet(vintages[-1])


def load_latest_vintage() -> pd.DataFrame:
    base = RAW_DIR / "cpc"
    if not base.exists():
        raise FileNotFoundError("No CPC snapshots found. Run `natgas ingest cpc` first.")
    vintages = sorted(base.glob("vintage=*/degree_days.parquet"))
    if not vintages:
        raise FileNotFoundError("No CPC parquet files found.")
    return pd.read_parquet(vintages[-1])
