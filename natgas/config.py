"""Project paths and configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
CONSENSUS_DIR = DATA_DIR / "consensus"
PAPER_DIR = DATA_DIR / "paper"
LOGS_DIR = ROOT / "logs" / "predictions"
DASHBOARD_DIR = ROOT / "dashboard"
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"

EIA_API_KEY = os.getenv("EIA_API_KEY", "")
EIA_BASE_URL = "https://api.eia.gov/v2"

# EIA storage week ends Friday; report released following Thursday 10:30 ET.
STORAGE_BASE_TEMP_F = 65.0

# Census divisions used by CPC degree-day products.
CPC_DIVISIONS = [
    "NEW ENGLAND",
    "EAST NORTH CENTRAL",
    "WEST NORTH CENTRAL",
    "SOUTH ATLANTIC",
    "EAST SOUTH CENTRAL",
    "WEST SOUTH CENTRAL",
    "MOUNTAIN",
    "PACIFIC",
]

# Simplified gas-heating share weights (ACS-inspired; refined via empirical calibration).
DEFAULT_GAS_HEATING_SHARE = {
    "NEW ENGLAND": 0.32,
    "EAST NORTH CENTRAL": 0.72,
    "WEST NORTH CENTRAL": 0.68,
    "SOUTH ATLANTIC": 0.45,
    "EAST SOUTH CENTRAL": 0.42,
    "WEST SOUTH CENTRAL": 0.52,
    "MOUNTAIN": 0.55,
    "PACIFIC": 0.48,
}


def ensure_dirs() -> None:
    for path in (RAW_DIR, INTERIM_DIR, CONSENSUS_DIR, PAPER_DIR, LOGS_DIR, DASHBOARD_DIR, DATA_DIR):
        path.mkdir(parents=True, exist_ok=True)
