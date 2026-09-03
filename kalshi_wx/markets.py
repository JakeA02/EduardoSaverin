"""Parse Kalshi HIGH / RAIN tickers into settlement questions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_DATE_RE = re.compile(r"(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})")
_BRACKET_RE = re.compile(r"-B(?P<center>\d+(?:\.\d+)?)$")
_THRESH_RE = re.compile(r"-T(?P<strike>\d+(?:\.\d+)?)$")
_TITLE_LT = re.compile(r"be\s*<\s*(\d+)")
_TITLE_GT = re.compile(r"be\s*>\s*(\d+)")
_TITLE_OR_BELOW = re.compile(r"(\d+)\s*°?\s*or below", re.I)
_TITLE_OR_ABOVE = re.compile(r"(\d+)\s*°?\s*or above", re.I)
_TITLE_RANGE = re.compile(r"(\d+)\s*[–\-to]+\s*(\d+)")


def parse_ticker_date(text: str) -> date | None:
    match = _DATE_RE.search(text.upper())
    if not match:
        return None
    mon = _MONTHS.get(match.group("mon"))
    if mon is None:
        return None
    yy = int(match.group("yy"))
    year = 2000 + yy if yy < 80 else 1900 + yy
    try:
        return date(year, mon, int(match.group("dd")))
    except ValueError:
        return None


def dollars_to_prob(raw: str | float | None) -> float | None:
    if raw is None or raw == "":
        return None
    n = float(raw)
    if not math_isfinite(n) or n <= 0:
        return None
    if n > 1:
        n = n / 100.0
    if n > 1:
        return None
    return n


def math_isfinite(n: float) -> bool:
    return n == n and n not in (float("inf"), float("-inf"))


@dataclass(frozen=True)
class HighContract:
    ticker: str
    series: str
    target_date: date
    kind: str  # bracket | less | greater
    lo: int | None
    hi: int | None
    strike: int | None
    center: float | None


@dataclass(frozen=True)
class RainContract:
    ticker: str
    series: str
    target_date: date
    rain_code: str


def parse_high_contract(ticker: str, title: str = "") -> HighContract | None:
    target = parse_ticker_date(ticker)
    if target is None:
        return None
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    series = parts[0]
    hay = f"{ticker} {title}"

    br = _BRACKET_RE.search(ticker)
    if br:
        center = float(br.group("center"))
        lo = int(math_floor(center))
        hi = lo + 1
        return HighContract(ticker, series, target, "bracket", lo, hi, None, center)

    th = _THRESH_RE.search(ticker)
    if th:
        strike = int(float(th.group("strike")))
        if _TITLE_GT.search(hay) or _TITLE_OR_ABOVE.search(hay):
            return HighContract(ticker, series, target, "greater", None, None, strike, None)
        if _TITLE_LT.search(hay) or _TITLE_OR_BELOW.search(hay):
            return HighContract(ticker, series, target, "less", None, None, strike, None)
        # Bare threshold with no title: Kalshi T-strikes on HIGH are usually tails.
        # Prefer greater for high strikes, less for low — caller should pass title.
        return HighContract(ticker, series, target, "less", None, None, strike, None)

    rng = _TITLE_RANGE.search(title)
    if rng:
        lo, hi = int(rng.group(1)), int(rng.group(2))
        return HighContract(ticker, series, target, "bracket", lo, hi, None, (lo + hi) / 2)
    return None


def math_floor(n: float) -> int:
    return int(n) if n >= 0 else int(n) - (0 if n == int(n) else 1)


@dataclass(frozen=True)
class WeekendRainContract:
    ticker: str
    series: str
    start_date: date
    end_date: date
    rain_code: str


@dataclass(frozen=True)
class WeeklyHotContract:
    ticker: str
    series: str
    week_ending: date
    n_days: int


def parse_weekend_rain(ticker: str, title: str = "") -> WeekendRainContract | None:
    if not ticker.upper().startswith("KXRAINWKND"):
        return None
    base = parse_rain_contract(ticker)
    if base is None:
        return None
    span = 1
    match = re.search(
        r"between\s+[A-Za-z]+\s+(\d+).+?and\s+[A-Za-z]+\s+(\d+)",
        title,
        re.I,
    )
    if match:
        span = max(1, int(match.group(2)) - int(match.group(1)))
    return WeekendRainContract(
        ticker, base.series, base.target_date, base.target_date + timedelta(days=span), base.rain_code
    )


def parse_weekly_hot(ticker: str) -> WeeklyHotContract | None:
    from kalshi_wx.stations import WEEKLY_HOT_SERIES

    parts = ticker.split("-")
    if len(parts) < 3 or parts[0] not in WEEKLY_HOT_SERIES:
        return None
    target = parse_ticker_date(parts[1])
    if target is None:
        return None
    try:
        n_days = int(parts[2])
    except ValueError:
        return None
    if n_days < 1:
        return None
    return WeeklyHotContract(ticker, parts[0], target, n_days)


def parse_rain_contract(ticker: str) -> RainContract | None:
    parts = ticker.split("-")
    if len(parts) < 3 or not parts[0].upper().startswith("KXRAIN"):
        return None
    target = parse_ticker_date(parts[1] if len(parts) > 1 else ticker)
    if target is None:
        return None
    return RainContract(ticker, parts[0], target, parts[-1].upper())


def implied_tmax(contracts: list[tuple[HighContract, float]]) -> float | None:
    """Probability-weighted integer center from a HIGH event's mids."""
    weights = []
    centers = []
    for spec, p in contracts:
        if p is None or p <= 0:
            continue
        if spec.kind == "bracket" and spec.lo is not None and spec.hi is not None:
            centers.append(0.5 * (spec.lo + spec.hi))
        elif spec.kind == "less" and spec.strike is not None:
            centers.append(float(spec.strike - 2))
        elif spec.kind == "greater" and spec.strike is not None:
            centers.append(float(spec.strike + 2))
        else:
            continue
        weights.append(p)
    if not weights:
        return None
    total = sum(weights)
    if total <= 0:
        return None
    return sum(c * w for c, w in zip(centers, weights, strict=True)) / total
