"""Join WeatherNext station forecasts to open Kalshi HIGH / RAIN books."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from natgas.ingest.weathernext import WeatherNextConfigError

from kalshi_wx.config import FORECAST_HOURS, LOGS_DIR, MIN_EV, MODEL_VERSION, ensure_dirs
from kalshi_wx.forecast import DailyWx, fetch_all_stations
from kalshi_wx.horizons import FAR, NEAR, eligible_for, tag_candidate
from kalshi_wx.kalshi import list_markets
from kalshi_wx.markets import (
    dollars_to_prob,
    implied_tmax,
    parse_high_contract,
    parse_rain_contract,
    parse_weekend_rain,
    parse_weekly_hot,
)
from kalshi_wx.probability import (
    contract_edge,
    p_consecutive_ge,
    p_day_above,
    p_rain,
    p_rain_range,
    p_tmax_greater_than,
    p_tmax_less_than,
    p_integer_tmax,
    tmax_sigma_f,
)
from kalshi_wx.stations import (
    BY_CITY,
    BY_HIGH_SERIES,
    BY_RAIN_CODE,
    HIGH_SERIES,
    RAIN_SERIES,
    RAIN_WEEKEND_SERIES,
    STATIONS,
    WEEKLY_HOT_SERIES,
    Station,
    station_from_rules,
)


def _quote(raw: dict[str, Any]) -> tuple[float | None, float | None, float, float]:
    bid = dollars_to_prob(raw.get("yes_bid_dollars"))
    ask = dollars_to_prob(raw.get("yes_ask_dollars"))
    try:
        vol = float(raw.get("volume_fp") or raw.get("volume") or 0)
    except (TypeError, ValueError):
        vol = 0.0
    try:
        oi = float(raw.get("open_interest_fp") or raw.get("open_interest") or 0)
    except (TypeError, ValueError):
        oi = 0.0
    return bid, ask, vol, oi


def _wx_for(station: Station, target: date, by_city: dict[str, list[DailyWx]]) -> DailyWx | None:
    rows = by_city.get(station.city) or []
    for row in rows:
        if row.target_date == target:
            return row
    return None


def _high_model_p(spec, wx: DailyWx) -> float:
    sigma = tmax_sigma_f(wx.lead_days)
    if spec.kind == "bracket" and spec.lo is not None and spec.hi is not None:
        return p_integer_tmax(wx.tmax_f, sigma, spec.lo, spec.hi)
    if spec.kind == "less" and spec.strike is not None:
        return p_tmax_less_than(wx.tmax_f, sigma, spec.strike)
    if spec.kind == "greater" and spec.strike is not None:
        return p_tmax_greater_than(wx.tmax_f, sigma, spec.strike)
    raise ValueError(f"unhandled high spec {spec}")


def _candidate(
    *,
    kind: str,
    ticker: str,
    event_ticker: str,
    title: str,
    city: str,
    station: Station,
    target_date: date,
    model_p: float,
    wx: DailyWx,
    raw: dict[str, Any],
    extra: dict[str, Any],
) -> dict[str, Any]:
    bid, ask, vol, oi = _quote(raw)
    edge = contract_edge(model_p, bid, ask, min_ev=MIN_EV)
    mid = edge.mid
    return {
        "kind": kind,
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": title,
        "city": city,
        "city_name": station.name,
        "cli_id": station.cli_id,
        "icao": station.icao,
        "target_date": target_date.isoformat(),
        "model_p": round(model_p, 4),
        "yes_bid": None if bid is None else round(bid, 4),
        "yes_ask": None if ask is None else round(ask, 4),
        "mid": None if mid is None else round(mid, 4),
        "side": edge.side,
        "entry": round(edge.entry, 4) if edge.side != "skip" else None,
        "ev": round(edge.ev, 4) if edge.side != "skip" else 0.0,
        "fee": round(edge.fee, 4) if edge.side != "skip" else 0.0,
        "volume": vol,
        "open_interest": oi,
        "tmax_f": round(wx.tmax_f, 2),
        "tmin_f": round(wx.tmin_f, 2),
        "qpf_in": round(wx.qpf_in, 4),
        "lead_days": round(wx.lead_days, 2),
        "n_hours": wx.n_hours,
        "sigma_f": round(tmax_sigma_f(wx.lead_days), 2),
        "close_time": raw.get("close_time"),
        "rules_primary": (raw.get("rules_primary") or "")[:400],
        **extra,
    }


def scan_highs(by_city: dict[str, list[DailyWx]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped: dict[str, list[tuple[Any, dict[str, Any], DailyWx, Station]]] = {}
    for series in HIGH_SERIES:
        station = BY_HIGH_SERIES[series]
        for raw in list_markets(series_ticker=series):
            ticker = raw.get("ticker") or ""
            title = raw.get("title") or raw.get("subtitle") or ""
            spec = parse_high_contract(ticker, title)
            if spec is None:
                continue
            wx = _wx_for(station, spec.target_date, by_city)
            if wx is None:
                continue
            event = raw.get("event_ticker") or ticker
            grouped.setdefault(event, []).append((spec, raw, wx, station))
            model_p = _high_model_p(spec, wx)
            rows.append(
                _candidate(
                    kind="high",
                    ticker=ticker,
                    event_ticker=event,
                    title=title,
                    city=station.city,
                    station=station,
                    target_date=spec.target_date,
                    model_p=model_p,
                    wx=wx,
                    raw=raw,
                    extra={
                        "contract_kind": spec.kind,
                        "lo": spec.lo,
                        "hi": spec.hi,
                        "strike": spec.strike,
                    },
                )
            )
    implied = {}
    for event, items in grouped.items():
        pairs = []
        for spec, raw, _wx, _st in items:
            bid, ask, _vol, _oi = _quote(raw)
            mid = None
            if bid is not None and ask is not None:
                mid = 0.5 * (bid + ask)
            elif ask is not None:
                mid = ask
            if mid is not None:
                pairs.append((spec, mid))
        implied[event] = implied_tmax(pairs)
    for row in rows:
        row["implied_tmax"] = (
            None if implied.get(row["event_ticker"]) is None else round(implied[row["event_ticker"]], 2)
        )
    return rows


def scan_rain(by_city: dict[str, list[DailyWx]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in list_markets(series_ticker=RAIN_SERIES):
        ticker = raw.get("ticker") or ""
        spec = parse_rain_contract(ticker)
        if spec is None:
            continue
        rules = f"{raw.get('rules_primary') or ''} {raw.get('rules_secondary') or ''}"
        station = station_from_rules(rules, rain_code=spec.rain_code) or BY_RAIN_CODE.get(spec.rain_code)
        if station is None:
            continue
        wx = _wx_for(station, spec.target_date, by_city)
        if wx is None:
            continue
        model_p = p_rain(wx.qpf_in, wx.hourly_pops, wx.lead_days)
        title = raw.get("title") or f"Rain in {station.name}"
        rows.append(
            _candidate(
                kind="rain",
                ticker=ticker,
                event_ticker=raw.get("event_ticker") or ticker,
                title=title,
                city=station.city,
                station=station,
                target_date=spec.target_date,
                model_p=model_p,
                wx=wx,
                raw=raw,
                extra={"rain_code": spec.rain_code, "contract_kind": "rain"},
            )
        )
    return rows


def _dates_inclusive(start: date, end: date) -> list[date]:
    out: list[date] = []
    cursor = start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _complete_days(
    station: Station, start: date, end: date, by_city: dict[str, list[DailyWx]]
) -> list[DailyWx] | None:
    need = _dates_inclusive(start, end)
    have = {row.target_date: row for row in (by_city.get(station.city) or [])}
    if any(day not in have for day in need):
        return None
    return [have[day] for day in need]


def scan_weekend_rain(by_city: dict[str, list[DailyWx]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in list_markets(series_ticker=RAIN_WEEKEND_SERIES):
        ticker = raw.get("ticker") or ""
        title = raw.get("title") or raw.get("subtitle") or ""
        spec = parse_weekend_rain(ticker, title)
        if spec is None:
            continue
        rules = f"{raw.get('rules_primary') or ''} {raw.get('rules_secondary') or ''}"
        station = station_from_rules(rules, rain_code=spec.rain_code) or BY_RAIN_CODE.get(spec.rain_code)
        if station is None:
            continue
        days = _complete_days(station, spec.start_date, spec.end_date, by_city)
        if not days:
            continue
        model_p = p_rain_range([(d.qpf_in, d.hourly_pops, d.lead_days) for d in days])
        wx = max(days, key=lambda d: d.qpf_in)
        rows.append(
            _candidate(
                kind="rain_weekend",
                ticker=ticker,
                event_ticker=raw.get("event_ticker") or ticker,
                title=title or f"Weekend rain in {station.name}",
                city=station.city,
                station=station,
                target_date=spec.start_date,
                model_p=model_p,
                wx=wx,
                raw=raw,
                extra={
                    "rain_code": spec.rain_code,
                    "contract_kind": "rain_weekend",
                    "window_start": spec.start_date.isoformat(),
                    "window_end": spec.end_date.isoformat(),
                    "n_days": len(days),
                },
            )
        )
    return rows


def scan_weekly_hot(by_city: dict[str, list[DailyWx]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for series, city in WEEKLY_HOT_SERIES.items():
        station = BY_CITY.get(city)
        if station is None:
            continue
        for raw in list_markets(series_ticker=series):
            ticker = raw.get("ticker") or ""
            spec = parse_weekly_hot(ticker)
            if spec is None:
                continue
            week_start = spec.week_ending - timedelta(days=6)
            days = _complete_days(station, week_start, spec.week_ending, by_city)
            if not days:
                continue
            day_probs = [
                p_day_above(0.5 * (d.tmax_f + d.tmin_f), tmax_sigma_f(d.lead_days), 90.0) for d in days
            ]
            model_p = p_consecutive_ge(day_probs, spec.n_days)
            wx = max(days, key=lambda d: 0.5 * (d.tmax_f + d.tmin_f))
            title = raw.get("title") or raw.get("subtitle") or f"{spec.n_days} hot days in {station.name}"
            rows.append(
                _candidate(
                    kind="weekly_hot",
                    ticker=ticker,
                    event_ticker=raw.get("event_ticker") or ticker,
                    title=title,
                    city=station.city,
                    station=station,
                    target_date=spec.week_ending,
                    model_p=model_p,
                    wx=wx,
                    raw=raw,
                    extra={
                        "contract_kind": "weekly_hot",
                        "n_hot_days": spec.n_days,
                        "week_start": week_start.isoformat(),
                        "week_ending": spec.week_ending.isoformat(),
                        "day_probs": [round(p, 4) for p in day_probs],
                    },
                )
            )
    return rows


def paper_eligible(row: dict[str, Any], *, today: date | None = None) -> bool:
    """Near (D+1) book only — kept as the backward-compatible flag."""
    today = today or datetime.now(UTC).date()
    return eligible_for(row, NEAR, today=today)


def unique_stations_for_scan() -> list[Station]:
    seen: dict[str, Station] = {}
    for s in STATIONS:
        seen[s.city] = s
    return list(seen.values())


def run_scan(*, hours: int = FORECAST_HOURS) -> dict[str, Any]:
    ensure_dirs()
    stations = unique_stations_for_scan()
    by_city = fetch_all_stations(stations, hours=hours)
    highs = scan_highs(by_city)
    rain = scan_rain(by_city)
    weekend = scan_weekend_rain(by_city)
    weekly = scan_weekly_hot(by_city)
    candidates = highs + rain + weekend + weekly
    as_of = datetime.now(UTC).date()
    for row in candidates:
        tag_candidate(row, today=as_of)
    tradeable_near = [c for c in candidates if c.get("eligible", {}).get(NEAR.id)]
    tradeable_far = [c for c in candidates if c.get("eligible", {}).get(FAR.id)]
    forecasts = []
    for city, days in sorted(by_city.items()):
        for d in days:
            forecasts.append(
                {
                    "city": city,
                    "name": d.station.name,
                    "target_date": d.target_date.isoformat(),
                    "tmax_f": round(d.tmax_f, 2),
                    "tmin_f": round(d.tmin_f, 2),
                    "qpf_in": round(d.qpf_in, 4),
                    "n_hours": d.n_hours,
                    "lead_days": round(d.lead_days, 2),
                }
            )
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "ts_utc": ts,
        "model_version": MODEL_VERSION,
        "n_high": len(highs),
        "n_rain": len(rain),
        "n_weekend": len(weekend),
        "n_weekly": len(weekly),
        "n_markets": len(candidates),
        "n_tradeable": len(tradeable_near),
        "n_tradeable_near": len(tradeable_near),
        "n_tradeable_far": len(tradeable_far),
        "forecasts": forecasts,
        "candidates": candidates,
    }
    path = LOGS_DIR / f"{ts}.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    record["path"] = str(path)
    return record


def load_latest_scan() -> dict[str, Any] | None:
    ensure_dirs()
    files = sorted(LOGS_DIR.glob("*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())


def load_scan_history(limit: int = 12) -> list[dict[str, Any]]:
    ensure_dirs()
    files = sorted(LOGS_DIR.glob("*.json"))[-limit:]
    out = []
    for path in files:
        try:
            out.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def scan_or_raise(*, hours: int = FORECAST_HOURS) -> dict[str, Any]:
    try:
        return run_scan(hours=hours)
    except WeatherNextConfigError:
        raise
