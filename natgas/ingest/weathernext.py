"""WeatherNext 3 via the Maps Platform Weather API (hourly T2m → division HDD/CDD).

Maps hourly forecasts cap at 240 hours (10 days). WeatherNext 3 synoptic cycles
on BigQuery/GCS go to 360 hours (15 days); this module is the no-allowlist A/B
path. Same output schema as CPC NDFD: issued, date, region, hdd, cdd, lead_days.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from natgas.config import GOOGLE_MAPS_API_KEY, INTERIM_DIR, ensure_dirs
from natgas.ingest.nwp import (
    DIVISION_POINTS,
    attach_national,
    celsius_to_fahrenheit,
    temps_to_daily_dd,
)

MAPS_HOURLY_URL = "https://weather.googleapis.com/v1/forecast/hours:lookup"
MAPS_MAX_HOURS = 240  # published Maps Weather API cap
WN3_SYNOPTIC_HOURS = 360  # 00/06/12/18z WeatherNext 3 product
NWP_SOURCE = "weathernext3"


class WeatherNextConfigError(RuntimeError):
    """Missing API key or empty forecast payload."""


def _api_key() -> str:
    if not GOOGLE_MAPS_API_KEY:
        raise WeatherNextConfigError(
            "Set GOOGLE_MAPS_API_KEY (Maps Platform Weather API) in the environment."
        )
    return GOOGLE_MAPS_API_KEY


def _to_fahrenheit(degrees: float, unit: str | None) -> float:
    if (unit or "CELSIUS").upper() == "FAHRENHEIT":
        return float(degrees)
    return float(celsius_to_fahrenheit([degrees])[0])


def hour_local_date(item: dict[str, Any]) -> date:
    """Civil date in the location's timezone when Google provides it."""
    disp = item.get("displayDateTime") or {}
    if {"year", "month", "day"} <= set(disp):
        return date(int(disp["year"]), int(disp["month"]), int(disp["day"]))
    start = item["interval"]["startTime"]
    return datetime.fromisoformat(start.replace("Z", "+00:00")).date()


def hour_temp_f(item: dict[str, Any]) -> float:
    temp = item.get("temperature") or {}
    return _to_fahrenheit(float(temp["degrees"]), temp.get("unit"))


def hours_to_daily_dd(
    hours: list[dict[str, Any]],
    *,
    issued: date,
    region: str,
) -> pd.DataFrame:
    """Convert Maps hourly forecast records to daily HDD/CDD for one region."""
    if not hours:
        return pd.DataFrame(columns=["issued", "date", "region", "hdd", "cdd", "lead_days"])
    times = []
    temps = []
    for item in hours:
        local = hour_local_date(item)
        times.append(pd.Timestamp(local))
        temps.append(hour_temp_f(item))
    daily = temps_to_daily_dd(pd.DatetimeIndex(times), temps)
    daily["issued"] = issued
    daily["region"] = region
    daily["lead_days"] = (pd.to_datetime(daily["date"]) - pd.Timestamp(issued)).dt.days
    daily["nwp_source"] = NWP_SOURCE
    return daily[["issued", "date", "region", "hdd", "cdd", "lead_days", "nwp_source"]]


def fetch_hourly_point(
    lat: float,
    lon: float,
    *,
    hours: int = MAPS_MAX_HOURS,
    api_key: str | None = None,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Paginated hourly forecast for one lat/lon."""
    key = api_key if api_key is not None else _api_key()
    http = session or requests
    base_params: dict[str, Any] = {
        "key": key,
        "location.latitude": lat,
        "location.longitude": lon,
        "hours": min(int(hours), MAPS_MAX_HOURS),
        "pageSize": min(24, min(int(hours), MAPS_MAX_HOURS)),
    }
    rows: list[dict[str, Any]] = []
    url = MAPS_HOURLY_URL
    params = base_params
    for _ in range(16):
        resp = http.get(url, params=params, timeout=60)
        if not resp.ok:
            detail = resp.text[:300]
            raise requests.HTTPError(
                f"{resp.status_code} {resp.reason}: {detail}",
                response=resp,
            )
        payload = resp.json()
        rows.extend(payload.get("forecastHours") or [])
        token = payload.get("nextPageToken")
        if not token or len(rows) >= min(int(hours), MAPS_MAX_HOURS):
            break
        params = {**base_params, "pageToken": token}
    return rows[: min(int(hours), MAPS_MAX_HOURS)]


def fetch_division_dd(
    *,
    hours: int = MAPS_MAX_HOURS,
    issued: date | None = None,
    api_key: str | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """HDD/CDD by census division from Maps hourly T2m at division centroids."""
    issued = issued or datetime.now(UTC).date()
    frames = []
    for name, (lon, lat) in DIVISION_POINTS.items():
        hours_payload = fetch_hourly_point(
            lat, lon, hours=hours, api_key=api_key, session=session
        )
        daily = hours_to_daily_dd(hours_payload, issued=issued, region=name)
        if not daily.empty:
            frames.append(daily)
    if not frames:
        return pd.DataFrame(
            columns=["issued", "date", "region", "hdd", "cdd", "lead_days", "nwp_source"]
        )
    out = pd.concat(frames, ignore_index=True)
    return attach_national(out)


def save_snapshot(df: pd.DataFrame) -> Path:
    ensure_dirs()
    path = INTERIM_DIR / "weathernext_latest.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def load_latest_snapshot() -> pd.DataFrame:
    path = INTERIM_DIR / "weathernext_latest.parquet"
    if not path.exists():
        raise FileNotFoundError("No WeatherNext snapshot. Run `natgas ingest weathernext`.")
    return pd.read_parquet(path)


def ingest(*, hours: int = MAPS_MAX_HOURS) -> pd.DataFrame:
    df = fetch_division_dd(hours=hours)
    if df.empty:
        raise WeatherNextConfigError("WeatherNext hourly fetch returned no rows.")
    save_snapshot(df)
    return df


def forecast_horizon_days(issued: date, forecasts: pd.DataFrame) -> int:
    """Inclusive civil-day span of a forecast frame issued on `issued`."""
    if forecasts.empty:
        return 0
    dates = pd.to_datetime(forecasts["date"]).dt.date
    return int((dates.max() - issued).days) + 1


def days_needed_for_week(week_ending: date, as_of: date) -> int:
    """Forecast days required so `week_ending` is fully in the future from `as_of`."""
    last_day = week_ending
    return (last_day - as_of).days + 1


def maps_api_covers_week(week_ending: date, as_of: date, *, max_hours: int = MAPS_MAX_HOURS) -> bool:
    """Whether the Maps 240h cap can cover this storage week from `as_of`."""
    return days_needed_for_week(week_ending, as_of) * 24 <= max_hours
