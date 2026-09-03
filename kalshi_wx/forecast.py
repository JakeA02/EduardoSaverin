"""WeatherNext 3 via Maps hourly forecast at Kalshi settlement points."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests

from natgas.ingest.weathernext import (
    MAPS_MAX_HOURS,
    WeatherNextConfigError,
    fetch_hourly_point,
    hour_temp_f,
)

from kalshi_wx.config import FORECAST_HOURS, GOOGLE_MAPS_API_KEY, HISTORY_HOURS
from kalshi_wx.stations import Station

MAPS_HISTORY_URL = "https://weather.googleapis.com/v1/history/hours:lookup"


def hour_lst_date(item: dict[str, Any], lst_offset_hours: int) -> date:
    start = item["interval"]["startTime"]
    utc = datetime.fromisoformat(start.replace("Z", "+00:00"))
    local = utc + timedelta(hours=lst_offset_hours)
    return local.date()


def hour_qpf_in(item: dict[str, Any]) -> float:
    precip = item.get("precipitation") or {}
    qpf = precip.get("qpf") or {}
    qty = float(qpf.get("quantity") or 0.0)
    unit = (qpf.get("unit") or "MILLIMETERS").upper()
    if unit in {"INCH", "INCHES"}:
        return qty
    return qty / 25.4


def hour_pop(item: dict[str, Any]) -> float:
    precip = item.get("precipitation") or {}
    prob = precip.get("probability") or {}
    return float(prob.get("percent") or 0.0) / 100.0


@dataclass
class DailyWx:
    station: Station
    target_date: date
    tmax_f: float
    tmin_f: float
    qpf_in: float
    hourly_pops: list[float]
    n_hours: int
    lead_days: float


def fetch_hourly_history(
    lat: float,
    lon: float,
    *,
    hours: int = HISTORY_HOURS,
    api_key: str | None = None,
    session=None,
) -> list[dict[str, Any]]:
    """Paginated hourly history (Maps cap 24h) so today's Tmax includes hours already observed."""
    key = api_key if api_key is not None else _require_key()
    http = session or requests
    n = min(int(hours), 24)
    base_params: dict[str, Any] = {
        "key": key,
        "location.latitude": lat,
        "location.longitude": lon,
        "hours": n,
        "pageSize": min(24, n),
    }
    rows: list[dict[str, Any]] = []
    params = base_params
    for _ in range(4):
        resp = http.get(MAPS_HISTORY_URL, params=params, timeout=60)
        if not resp.ok:
            raise requests.HTTPError(
                f"{resp.status_code} {resp.reason}: {resp.text[:300]}",
                response=resp,
            )
        payload = resp.json()
        rows.extend(payload.get("historyHours") or [])
        token = payload.get("nextPageToken")
        if not token or len(rows) >= n:
            break
        params = {**base_params, "pageToken": token}
    return rows[:n]


def _dedupe_hours(hours: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for item in hours:
        start = ((item.get("interval") or {}).get("startTime")) or ""
        if start:
            seen[start] = item
    return [seen[k] for k in sorted(seen)]


def _require_key() -> str:
    if not GOOGLE_MAPS_API_KEY:
        raise WeatherNextConfigError(
            "Set GOOGLE_MAPS_API_KEY (Maps Platform Weather API) in the environment."
        )
    return GOOGLE_MAPS_API_KEY


def daily_from_hours(
    hours: list[dict[str, Any]],
    station: Station,
    *,
    issued: date | None = None,
) -> list[DailyWx]:
    issued = issued or datetime.now(UTC).date()
    buckets: dict[date, list[dict[str, Any]]] = {}
    for item in hours:
        day = hour_lst_date(item, station.lst_offset_hours)
        buckets.setdefault(day, []).append(item)
    out: list[DailyWx] = []
    for day, items in sorted(buckets.items()):
        temps = [hour_temp_f(it) for it in items]
        pops = [hour_pop(it) for it in items]
        qpf = sum(hour_qpf_in(it) for it in items)
        if not temps:
            continue
        lead = (day - issued).days + (0.5 if day == issued else 0.0)
        out.append(
            DailyWx(
                station=station,
                target_date=day,
                tmax_f=max(temps),
                tmin_f=min(temps),
                qpf_in=qpf,
                hourly_pops=pops,
                n_hours=len(items),
                lead_days=max(0.0, float(lead)),
            )
        )
    return out


def fetch_station_daily(
    station: Station,
    *,
    hours: int = FORECAST_HOURS,
    api_key: str | None = None,
    session=None,
) -> list[DailyWx]:
    key = api_key if api_key is not None else _require_key()
    forecast = fetch_hourly_point(
        station.lat,
        station.lon,
        hours=min(int(hours), MAPS_MAX_HOURS),
        api_key=key,
        session=session,
    )
    try:
        history = fetch_hourly_history(
            station.lat, station.lon, hours=HISTORY_HOURS, api_key=key, session=session
        )
    except Exception:
        history = []
    return daily_from_hours(_dedupe_hours(history + forecast), station)


def fetch_all_stations(
    stations: list[Station],
    *,
    hours: int = FORECAST_HOURS,
) -> dict[str, list[DailyWx]]:
    """city → daily rows. Sequential to stay inside Maps quota and rate limits."""
    import requests

    _require_key()
    out: dict[str, list[DailyWx]] = {}
    with requests.Session() as session:
        for station in stations:
            out[station.city] = fetch_station_daily(station, hours=hours, session=session)
    return out
