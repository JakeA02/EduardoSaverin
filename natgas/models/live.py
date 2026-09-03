"""Live storage predictions from blended observed + forecast weather."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from natgas.calendar import horizon_weeks, target_weeks
from natgas.features.blend import (
    blend_coverage_days,
    blend_is_complete,
    blend_week_daily,
    week_totals,
)
from natgas.predictions import log_prediction

PRODUCTION_NWP = "cpc-ndfd"


@dataclass
class LivePrediction:
    target_week_ending: date
    horizon_weeks: int
    predicted_change_bcf: float
    interval_lower: float
    interval_upper: float
    nwp_source: str
    nwp_cycle: str | None
    model_version: str
    coverage_days: int
    complete: bool
    skipped_reason: str | None = None
    path: Path | None = None


def _empty_forecasts() -> pd.DataFrame:
    return pd.DataFrame(columns=["issued", "date", "region", "hdd", "cdd", "lead_days"])


def load_weather(source: str) -> tuple[pd.DataFrame, pd.DataFrame, str, str | None]:
    """Observed CPC always; forecasts from NDFD, WeatherNext, or none."""
    from natgas.ingest.cpc import load_latest_daily

    observed = load_latest_daily()
    if source == "observed":
        return observed, _empty_forecasts(), "observed_weather", None
    if source == "cpc-forecast":
        from natgas.ingest.cpc_forecast import fetch_latest, load_latest_vintage

        try:
            forecasts = load_latest_vintage()
        except FileNotFoundError:
            forecasts = fetch_latest()
        issued = pd.to_datetime(forecasts["issued"]).max().date()
        return observed, forecasts, PRODUCTION_NWP, issued.isoformat()
    if source == "weathernext":
        from natgas.ingest.weathernext import (
            NWP_SOURCE,
            WeatherNextConfigError,
            fetch_division_dd,
            load_latest_snapshot,
            save_snapshot,
        )

        try:
            forecasts = fetch_division_dd()
            save_snapshot(forecasts)
        except WeatherNextConfigError:
            raise
        except (OSError, ValueError, requests.RequestException):
            try:
                forecasts = load_latest_snapshot()
            except FileNotFoundError as exc:
                raise WeatherNextConfigError(
                    "WeatherNext fetch failed and no snapshot exists."
                ) from exc
        if forecasts.empty:
            raise WeatherNextConfigError("WeatherNext forecast frame is empty.")
        issued = pd.to_datetime(forecasts["issued"]).max().date()
        return observed, forecasts, NWP_SOURCE, issued.isoformat()
    raise ValueError(f"Unknown weather source: {source}")


def context_row(panel: pd.DataFrame, target: date, totals: dict[str, float]) -> pd.DataFrame:
    """Copy the latest panel row, set week_ending to `target`, overlay GWDD."""
    data = panel.copy()
    data["week_ending"] = pd.to_datetime(data["week_ending"]).dt.date
    latest = data[data["week_ending"] <= target].tail(1).copy()
    if latest.empty:
        raise ValueError("No panel data for target week.")
    latest["week_ending"] = target
    for key, value in totals.items():
        latest[key] = value
    return latest


def score_model(model: Any, row: pd.DataFrame) -> tuple[str, float, float, float]:
    from natgas.models.baseline import predict as predict_ols
    from natgas.models.ensemble import predict as predict_lgbm

    if hasattr(model, "pipeline"):
        y_hat = float(predict_ols(model, row)[0])
        name = model.name
    else:
        y_hat = float(predict_lgbm(model, row)[0])
        name = model.name
    lo = y_hat + float(getattr(model, "residual_q05", -20.0))
    hi = y_hat + float(getattr(model, "residual_q95", 20.0))
    return name, y_hat, lo, hi


def predict_week(
    *,
    target: date,
    as_of: date,
    model: Any,
    panel: pd.DataFrame,
    observed: pd.DataFrame,
    forecasts: pd.DataFrame,
    nwp_source: str,
    nwp_cycle: str | None,
    now: Any = None,
    write: bool = True,
) -> LivePrediction:
    """Blend weather for one storage week and optionally append a prediction log."""
    daily = blend_week_daily(target, as_of, observed, forecasts)
    coverage = blend_coverage_days(daily)
    complete = blend_is_complete(daily)
    horizon = horizon_weeks(target, now)
    if not complete:
        reason = (
            f"weather covers {coverage}/7 days of week ending {target.isoformat()}; "
            "need a complete Saturday–Friday blend"
        )
        if nwp_source == "weathernext3" and coverage < 7:
            reason += (
                ". Maps Weather API caps at 240 hours (10 days); "
                "full WeatherNext 3 synoptic cycles cover 15 days"
            )
        return LivePrediction(
            target_week_ending=target,
            horizon_weeks=horizon,
            predicted_change_bcf=float("nan"),
            interval_lower=float("nan"),
            interval_upper=float("nan"),
            nwp_source=nwp_source,
            nwp_cycle=nwp_cycle,
            model_version="",
            coverage_days=coverage,
            complete=False,
            skipped_reason=reason,
        )
    totals = week_totals(daily)
    if any(pd.isna(v) for v in totals.values()):
        return LivePrediction(
            target_week_ending=target,
            horizon_weeks=horizon,
            predicted_change_bcf=float("nan"),
            interval_lower=float("nan"),
            interval_upper=float("nan"),
            nwp_source=nwp_source,
            nwp_cycle=nwp_cycle,
            model_version="",
            coverage_days=coverage,
            complete=False,
            skipped_reason="blend produced NaN degree days (missing NATIONAL or divisions)",
        )
    row = context_row(panel, target, totals)
    name, y_hat, lo, hi = score_model(model, row)
    path = None
    if write:
        path = log_prediction(
            target_week_ending=target.isoformat(),
            model_version=name,
            predicted_change_bcf=y_hat,
            nwp_cycle=nwp_cycle,
            nwp_source=nwp_source,
            prediction_interval_90=(lo, hi),
            horizon_weeks=horizon,
        )
    return LivePrediction(
        target_week_ending=target,
        horizon_weeks=horizon,
        predicted_change_bcf=y_hat,
        interval_lower=lo,
        interval_upper=hi,
        nwp_source=nwp_source,
        nwp_cycle=nwp_cycle,
        model_version=name,
        coverage_days=coverage,
        complete=True,
        path=path,
    )


def run_live(
    *,
    source: str,
    weeks: int,
    model: Any,
    panel: pd.DataFrame,
    as_of: date | None = None,
    now: Any = None,
    write: bool = True,
    targets: list[date] | None = None,
) -> list[LivePrediction]:
    as_of = as_of or datetime.now(UTC).date()
    observed, forecasts, nwp_source, nwp_cycle = load_weather(source)
    week_list = targets if targets is not None else target_weeks(weeks, now)
    results = []
    for target in week_list:
        results.append(
            predict_week(
                target=target,
                as_of=as_of,
                model=model,
                panel=panel,
                observed=observed,
                forecasts=forecasts,
                nwp_source=nwp_source,
                nwp_cycle=nwp_cycle,
                now=now,
                write=write,
            )
        )
    return results


def default_weeks_for_source(source: str, weeks: int | None) -> int:
    if weeks is not None and weeks > 0:
        return weeks
    return 2 if source in {"weathernext", "all"} else 1
