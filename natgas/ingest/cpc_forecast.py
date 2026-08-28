"""CPC 7-day NDFD degree-day forecasts (historical + latest)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, UTC
from pathlib import Path

import pandas as pd
import requests

from natgas.config import RAW_DIR, ensure_dirs
from natgas.ingest.cpc import _parse_pipe_file

CPC_FCST_BASE = "https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_forecasts_7day"


def _issued_urls(issued: date) -> tuple[str, str]:
    y, m, d = issued.strftime("%Y"), issued.strftime("%m"), issued.strftime("%d")
    base = f"{CPC_FCST_BASE}/{y}/{m}/{d}"
    return f"{base}/Population.Heating.txt", f"{base}/Population.Cooling.txt"


def _latest_urls() -> tuple[str, str]:
    base = f"{CPC_FCST_BASE}/latest"
    return f"{base}/Population.Heating.txt", f"{base}/Population.Cooling.txt"


def _fetch(url: str) -> str | None:
    resp = requests.get(url, timeout=60)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text


def _parse_issued(hdd_text: str, cdd_text: str, issued: date) -> pd.DataFrame:
    hdd = _parse_pipe_file(hdd_text, "hdd")
    cdd = _parse_pipe_file(cdd_text, "cdd")
    if hdd.empty and cdd.empty:
        return pd.DataFrame(columns=["issued", "date", "region", "hdd", "cdd", "lead_days"])
    daily = hdd.merge(cdd, on=["date", "region"], how="outer")
    daily["issued"] = issued
    daily["lead_days"] = (pd.to_datetime(daily["date"]) - pd.Timestamp(issued)).dt.days
    return daily[["issued", "date", "region", "hdd", "cdd", "lead_days"]]


def fetch_issued(issued: date) -> pd.DataFrame:
    hdd_url, cdd_url = _issued_urls(issued)
    hdd_text, cdd_text = _fetch(hdd_url), _fetch(cdd_url)
    if not hdd_text or not cdd_text:
        return pd.DataFrame(columns=["issued", "date", "region", "hdd", "cdd", "lead_days"])
    return _parse_issued(hdd_text, cdd_text, issued)


def fetch_latest() -> pd.DataFrame:
    hdd_url, cdd_url = _latest_urls()
    hdd_text, cdd_text = _fetch(hdd_url), _fetch(cdd_url)
    if not hdd_text or not cdd_text:
        raise RuntimeError("CPC latest 7-day forecast files missing.")
    issued = datetime.now(UTC).date()
    for line in hdd_text.splitlines():
        if "Issued" in line and "UTC" in line:
            token = line.split("UTC")[-1].strip().split()[0]
            if token.isdigit() and len(token) == 8:
                issued = datetime.strptime(token, "%Y%m%d").date()
            break
    return _parse_issued(hdd_text, cdd_text, issued)


def _daterange(start: date, end: date, weekdays: tuple[int, ...]) -> list[date]:
    days = []
    cur = start
    while cur <= end:
        if cur.weekday() in weekdays:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def fetch_archive(
    start: date,
    end: date,
    weekdays: tuple[int, ...] = (5, 2),
    max_workers: int = 16,
) -> pd.DataFrame:
    """Pull issued forecasts on selected weekdays (default Sat + Wed)."""
    issued_dates = _daterange(start, end, weekdays)
    frames: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(fetch_issued, d): d for d in issued_dates}
        for fut in as_completed(futs):
            df = fut.result()
            if not df.empty:
                frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["issued", "date", "region", "hdd", "cdd", "lead_days"])
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(subset=["issued", "date", "region"], keep="last").sort_values(
        ["issued", "date", "region"]
    )


def save_snapshot(df: pd.DataFrame) -> Path:
    ensure_dirs()
    vintage = datetime.now(UTC).date().isoformat()
    out_dir = RAW_DIR / "cpc_forecast" / f"vintage={vintage}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "forecasts.parquet"
    df.to_parquet(path, index=False)
    return path


def ingest(start_year: int = 2020) -> pd.DataFrame:
    start = date(start_year, 1, 1)
    end = datetime.now(UTC).date()
    hist = fetch_archive(start, end)
    latest = fetch_latest()
    df = pd.concat([hist, latest], ignore_index=True)
    df = df.drop_duplicates(subset=["issued", "date", "region"], keep="last")
    save_snapshot(df)
    return df


def load_latest_vintage() -> pd.DataFrame:
    base = RAW_DIR / "cpc_forecast"
    vintages = sorted(base.glob("vintage=*/forecasts.parquet")) if base.exists() else []
    if not vintages:
        raise FileNotFoundError("No CPC forecast snapshots. Run `natgas ingest cpc-forecast`.")
    df = pd.read_parquet(vintages[-1])
    df = df[(df["hdd"] >= 0) & (df["cdd"] >= 0)]
    return df
