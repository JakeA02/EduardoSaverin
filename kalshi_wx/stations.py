"""Settlement stations for liquid Kalshi HIGH / RAIN cities."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    city: str
    name: str
    high_series: str
    rain_code: str
    cli_id: str
    lat: float
    lon: float
    lst_offset_hours: int
    icao: str


# Official climate sites Kalshi names in rules (CLINYC = Central Park, etc.).
# Coordinates are the observing point, not a metro centroid.
STATIONS: tuple[Station, ...] = (
    Station("NYC", "New York City", "KXHIGHNY", "NYC", "CLINYC", 40.7789, -73.9692, -5, "KNYC"),
    Station("CHI", "Chicago", "KXHIGHCHI", "CHI", "CLICHI", 41.7868, -87.7522, -6, "KMDW"),
    Station("LAX", "Los Angeles", "KXHIGHLAX", "LAX", "CLILAX", 33.9425, -118.4081, -8, "KLAX"),
    Station("DAL", "Dallas", "KXHIGHTDAL", "DAL", "CLIDAL", 32.8974, -97.0377, -6, "KDFW"),
    Station("AUS", "Austin", "KXHIGHAUS", "AUS", "CLIAUS", 30.1945, -97.6699, -6, "KAUS"),
    Station("HOU", "Houston", "KXHIGHTHOU", "HOU", "CLIHOU", 29.9844, -95.3414, -6, "KIAH"),
    Station("PHIL", "Philadelphia", "KXHIGHPHIL", "PHIL", "CLIPHL", 39.8719, -75.2411, -5, "KPHL"),
    Station("BOS", "Boston", "KXHIGHTBOS", "BOS", "CLIBOS", 42.3656, -71.0096, -5, "KBOS"),
    Station("ATL", "Atlanta", "KXHIGHTATL", "ATL", "CLIATL", 33.6407, -84.4277, -5, "KATL"),
    Station("SFO", "San Francisco", "KXHIGHTSFO", "SFO", "CLISFO", 37.6213, -122.3790, -8, "KSFO"),
    Station("MIN", "Minneapolis", "KXHIGHTMIN", "MIN", "CLIMIN", 44.8820, -93.2218, -6, "KMSP"),
    Station("PHX", "Phoenix", "KXHIGHTPHX", "PHX", "CLIPHX", 33.4343, -112.0116, -7, "KPHX"),
    Station("OKC", "Oklahoma City", "KXHIGHTOKC", "OKC", "CLIOKC", 35.3931, -97.6007, -6, "KOKC"),
    Station("SEA", "Seattle", "KXHIGHTSEA", "SEA", "CLISEA", 47.4502, -122.3088, -8, "KSEA"),
    Station("DEN", "Denver", "KXHIGHDEN", "DEN", "CLIDEN", 39.8561, -104.6737, -7, "KDEN"),
    Station("NOLA", "New Orleans", "KXHIGHTNOLA", "NOLA", "CLIMSY", 29.9934, -90.2580, -6, "KMSY"),
    Station("DC", "Washington DC", "KXHIGHTDC", "DC", "CLIDCA", 38.8521, -77.0377, -5, "KDCA"),
    Station("MIA", "Miami", "KXHIGHMIA", "MIA", "CLIMIA", 25.7954, -80.2901, -5, "KMIA"),
    Station("EWR", "Newark", "", "EWR", "CLIEWR", 40.6895, -74.1745, -5, "KEWR"),
    Station("TTN", "Trenton", "", "TTN", "CLITTN", 40.2767, -74.8135, -5, "KTTN"),
    Station("SAT", "San Antonio", "", "SAT", "CLISAT", 29.5337, -98.4698, -6, "KSAT"),
    Station("LAS", "Las Vegas", "", "LAS", "CLILAS", 36.0801, -115.1522, -8, "KLAS"),
)

BY_HIGH_SERIES = {s.high_series: s for s in STATIONS if s.high_series}
BY_RAIN_CODE = {s.rain_code: s for s in STATIONS}
BY_CLI = {s.cli_id: s for s in STATIONS}
BY_CITY = {s.city: s for s in STATIONS}

HIGH_SERIES = tuple(s.high_series for s in STATIONS if s.high_series)
RAIN_SERIES = "KXRAIN"
RAIN_WEEKEND_SERIES = "KXRAINWKND"

# Weekly "days above 90°F" series → city code.
WEEKLY_HOT_SERIES: dict[str, str] = {
    "KXAVGTATL": "ATL",
    "KXAVGTKAUS": "AUS",
    "KXAVGTKDEN": "DEN",
    "KXAVGTKDFW": "DAL",
    "KXAVGTKIAH": "HOU",
    "KXAVGTKLAS": "LAS",
    "KXAVGTKMSY": "NOLA",
    "KXAVGTKOKC": "OKC",
    "KXAVGTKPHX": "PHX",
    "KXAVGTKSAT": "SAT",
}

_CLI_RE = re.compile(r"\b(CLI[A-Z]{2,6})\b")


def station_from_rules(rules: str, *, series: str | None = None, rain_code: str | None = None) -> Station | None:
    """Prefer an explicit CLI id in the contract rules, else series / rain suffix."""
    match = _CLI_RE.search(rules or "")
    if match and match.group(1) in BY_CLI:
        return BY_CLI[match.group(1)]
    if series and series in BY_HIGH_SERIES:
        return BY_HIGH_SERIES[series]
    if rain_code and rain_code in BY_RAIN_CODE:
        return BY_RAIN_CODE[rain_code]
    return None
