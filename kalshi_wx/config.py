"""Paths and scan parameters for Kalshi weather paper trading."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PAPER_DIR = DATA_DIR / "paper"
INTERIM_DIR = DATA_DIR / "interim"
LOGS_DIR = ROOT / "logs" / "kalshi"
DASHBOARD_DIR = ROOT / "dashboard" / "kalshi"

KALSHI_PROD_BASE_URL = os.getenv(
    "KALSHI_PROD_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
)
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "") or os.getenv(
    "GOOGLE_WEATHER_API_KEY", ""
)

MODEL_VERSION = "wn3-maps-v0.3"
# Maps Weather API cap is 240h (~10 days). Full WN3 synoptic is 360h.
FORECAST_HOURS = 240
HISTORY_HOURS = 24
TAKER_FEE_COEFF = 0.07
MIN_EV = 0.05
MIN_VOLUME = 50.0
FAR_MIN_VOLUME = 10.0
MIN_MID = 0.05
MAX_MID = 0.95
N_CONTRACTS = 1
MAX_PAPER_PER_SCAN = 8
NEAR_LEAD_DAYS = 1
FAR_MIN_LEAD_DAYS = 2
FAR_MAX_LEAD_DAYS = 10
STRATEGY_NEAR = "near"
STRATEGY_FAR = "far"
STRATEGY = STRATEGY_NEAR  # legacy alias used by older ledger rows via HorizonPolicy.legacy_ids

# Overnight D+1 fills stay live until 4:00 America/New_York on target+1.
# That is 1am Pacific year-round, after local midnight for every CONUS station.
PAPER_TZ = "America/New_York"
PAPER_CLOSE_HOUR_ET = 4

# σ for integer °F high: day-of ~2°F MAE, grows with lead.
TMAX_SIGMA_BASE_F = 1.8
TMAX_SIGMA_PER_LEAD_F = 0.45
TMAX_SIGMA_MIN_F = 1.5
TMAX_SIGMA_MAX_F = 5.5

# QPF uncertainty (inches) for P(precip > 0).
QPF_SIGMA_BASE_IN = 0.04
QPF_SIGMA_PER_LEAD_IN = 0.02


def ensure_dirs() -> None:
    for path in (DATA_DIR, PAPER_DIR, INTERIM_DIR, LOGS_DIR, DASHBOARD_DIR):
        path.mkdir(parents=True, exist_ok=True)
