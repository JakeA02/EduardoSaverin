"""GFS / GEFS 2m temperature → division HDD/CDD via Herbie."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from natgas.config import INTERIM_DIR, STORAGE_BASE_TEMP_F, ensure_dirs
from natgas.features.degree_days import daily_degree_days

# Census-division centroids (lon, lat) for nearest-neighbor sampling of NWP grids.
DIVISION_POINTS = {
    "NEW ENGLAND": (-71.5, 42.5),
    "EAST NORTH CENTRAL": (-86.0, 41.5),
    "WEST NORTH CENTRAL": (-96.0, 43.0),
    "SOUTH ATLANTIC": (-81.0, 33.0),
    "EAST SOUTH CENTRAL": (-87.0, 35.0),
    "WEST SOUTH CENTRAL": (-97.0, 32.0),
    "MOUNTAIN": (-111.0, 40.0),
    "PACIFIC": (-121.0, 38.0),
}


def kelvin_to_fahrenheit(arr: np.ndarray) -> np.ndarray:
    return (np.asarray(arr) - 273.15) * 9 / 5 + 32


def celsius_to_fahrenheit(arr: np.ndarray) -> np.ndarray:
    return np.asarray(arr, dtype=float) * 9 / 5 + 32


def nearest_index(coord: np.ndarray, value: float) -> int:
    return int(np.abs(coord - value).argmin())


def sample_grid(lons: np.ndarray, lats: np.ndarray, field: np.ndarray, lon: float, lat: float) -> float:
    """Nearest-neighbor sample. `field` is 2d (lat, lon) or (y, x)."""
    lon_1d = lons[0, :] if lons.ndim == 2 else lons
    lat_1d = lats[:, 0] if lats.ndim == 2 else lats
    i = nearest_index(lat_1d, lat)
    j = nearest_index(lon_1d, lon if lon <= 180 else lon - 360)
    return float(field[i, j])


def temps_to_daily_dd(times: pd.DatetimeIndex, t2m_f: np.ndarray, base: float = STORAGE_BASE_TEMP_F) -> pd.DataFrame:
    """Convert a temperature time series (°F) to daily HDD/CDD."""
    df = pd.DataFrame({"valid": pd.to_datetime(times), "t": t2m_f})
    df["date"] = df["valid"].dt.date
    daily = df.groupby("date")["t"].agg(["max", "min"]).reset_index()
    dd = daily_degree_days(daily["max"], daily["min"], base=base)
    daily["hdd"] = dd["hdd"].to_numpy()
    daily["cdd"] = dd["cdd"].to_numpy()
    return daily[["date", "hdd", "cdd"]]


def compute_degree_days(tmax_f: float, tmin_f: float, base: float = STORAGE_BASE_TEMP_F) -> tuple[float, float]:
    tmean = (tmax_f + tmin_f) / 2
    return max(0.0, base - tmean), max(0.0, tmean - base)


def _herbie_t2m(cycle: datetime, fxx: int, model: str = "gfs", member: str | None = None):
    from herbie import Herbie

    kwargs: dict = {
        "date": cycle.strftime("%Y-%m-%d %H:%M"),
        "model": model,
        "fxx": fxx,
    }
    if model == "gfs":
        kwargs["product"] = "pgrb2.0p25"
    if member is not None:
        kwargs["member"] = member
    cache = INTERIM_DIR / "nwp" / model
    cache.mkdir(parents=True, exist_ok=True)
    kwargs["save_dir"] = cache
    H = Herbie(**kwargs)
    return H.xarray("TMP:2 m above ground", remove_grib=True)


def fetch_cycle_division_dd(
    cycle: datetime,
    hours: range | list[int] | None = None,
    model: str = "gfs",
    member: str | None = None,
) -> pd.DataFrame:
    """Retrieve 2m temp for one cycle and return daily HDD/CDD by census division."""
    ensure_dirs()
    hours = list(hours) if hours is not None else list(range(0, 169, 3))
    samples: dict[str, list[tuple[datetime, float]]] = {name: [] for name in DIVISION_POINTS}

    for fxx in hours:
        ds = _herbie_t2m(cycle, fxx, model=model, member=member)
        t2m = ds["t2m"].values if "t2m" in ds else ds["t"].values
        if t2m.ndim == 3:
            t2m = t2m[0]
        lats = ds["latitude"].values if "latitude" in ds.coords else ds["lat"].values
        lons = ds["longitude"].values if "longitude" in ds.coords else ds["lon"].values
        valid = pd.Timestamp(ds["time"].values) + pd.Timedelta(hours=fxx) if "step" not in ds.coords else pd.Timestamp(ds.time.values)
        if "valid_time" in ds.coords:
            valid = pd.Timestamp(ds["valid_time"].values)
        t_f = kelvin_to_fahrenheit(t2m)
        for name, (lon, lat) in DIVISION_POINTS.items():
            samples[name].append((valid.to_pydatetime(), sample_grid(lons, lats, t_f, lon, lat)))

    frames = []
    for name, series in samples.items():
        times = pd.DatetimeIndex([t for t, _ in series])
        vals = np.array([v for _, v in series])
        daily = temps_to_daily_dd(times, vals)
        daily["region"] = name
        daily["issued"] = cycle.date()
        daily["nwp_source"] = model if member is None else f"{model}:{member}"
        frames.append(daily)
    return pd.concat(frames, ignore_index=True)


def fetch_gefs_ensemble(cycle: datetime, members: list[str] | None = None, hours: range | None = None) -> pd.DataFrame:
    """Subset of GEFS members for uncertainty. Default: control + 4 perturbations."""
    members = members or ["c00", "p01", "p10", "p20", "p30"]
    frames = []
    for mem in members:
        try:
            df = fetch_cycle_division_dd(cycle, hours=hours, model="gefs", member=mem)
            df["member"] = mem
            frames.append(df)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def attach_national(daily: pd.DataFrame) -> pd.DataFrame:
    """Add NATIONAL rows as population-weighted HDD/CDD of the eight divisions."""
    if daily.empty or (daily["region"] == "NATIONAL").any():
        return daily
    from natgas.config import CPC_DIVISIONS
    from natgas.features.weights import population_weights

    keys = [c for c in ("issued", "nwp_source", "member") if c in daily.columns]
    div = daily[daily["region"].isin(CPC_DIVISIONS)].copy()
    if div.empty:
        return daily
    weights = population_weights()
    div["weight"] = div["region"].map(weights)
    grouped = []
    for _, grp in div.groupby(["date"] + keys, dropna=False):
        w = grp["weight"].fillna(0.0)
        if float(w.sum()) <= 0:
            continue
        w = w / w.sum()
        row: dict = {
            "date": grp["date"].iloc[0],
            "region": "NATIONAL",
            "hdd": float((grp["hdd"] * w).sum()),
            "cdd": float((grp["cdd"] * w).sum()),
        }
        for key in keys:
            row[key] = grp[key].iloc[0]
        if "lead_days" in grp.columns:
            row["lead_days"] = int(grp["lead_days"].iloc[0])
        grouped.append(row)
    if not grouped:
        return daily
    return pd.concat([daily, pd.DataFrame(grouped)], ignore_index=True)
