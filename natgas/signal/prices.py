"""Daily NG front-month closes for paper fills. Never a model feature."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from io import StringIO

import pandas as pd
import requests

from natgas.calendar import report_release_datetime
from natgas.config import EIA_API_KEY, EIA_BASE_URL, INTERIM_DIR, ensure_dirs

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
STOOQ_URL = "https://stooq.com/q/d/l/?s=ng.f&i=d"
YAHOO_URL = "https://query2.finance.yahoo.com/v8/finance/chart/NG=F"
CACHE_PATH = INTERIM_DIR / "ng_front_daily.parquet"


def _fetch_eia_series(series: str) -> pd.DataFrame:
    """Daily EIA NG price series (RNGWHHD spot or RNGC1 contract 1)."""
    if not EIA_API_KEY:
        return pd.DataFrame(columns=["date", "close"])
    rows: list[dict] = []
    offset = 0
    page_size = 5000
    while True:
        params: dict[str, str | int] = {
            "api_key": EIA_API_KEY,
            "frequency": "daily",
            "data[0]": "value",
            "facets[series][]": series,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": offset,
            "length": page_size,
            "start": "2015-01-01",
        }
        resp = requests.get(f"{EIA_BASE_URL}/natural-gas/pri/fut/data/", params=params, timeout=60)
        resp.raise_for_status()
        batch = resp.json().get("response", {}).get("data", [])
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    if not rows:
        return pd.DataFrame(columns=["date", "close"])
    df = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["period"], errors="coerce").dt.date,
            "close": pd.to_numeric(df["value"], errors="coerce"),
        }
    )
    return out.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)


def _newest(frames: list[pd.DataFrame]) -> pd.DataFrame:
    alive = [f for f in frames if not f.empty]
    if not alive:
        return pd.DataFrame(columns=["date", "close"])
    return max(alive, key=lambda f: f["date"].max())


def _merge_closes(*frames: pd.DataFrame) -> pd.DataFrame:
    """Combine daily closes; earlier frames win on duplicate dates (EIA preferred)."""
    alive = [f for f in frames if not f.empty]
    if not alive:
        return pd.DataFrame(columns=["date", "close"])
    combined = pd.concat(alive, ignore_index=True)
    return combined.drop_duplicates("date", keep="first").sort_values("date").reset_index(drop=True)


def _fetch_stooq() -> pd.DataFrame:
    try:
        resp = requests.get(STOOQ_URL, headers=HEADERS, timeout=45)
        if resp.status_code == 200 and "Date" in resp.text[:80]:
            df = _parse_stooq(resp.text)
            if len(df) > 50:
                return df
    except requests.RequestException:
        pass
    return pd.DataFrame(columns=["date", "close"])


def _fetch_yfinance() -> pd.DataFrame:
    try:
        import yfinance as yf

        hist = yf.Ticker("NG=F").history(start="2015-01-01", auto_adjust=False)
        if hist.empty:
            return pd.DataFrame(columns=["date", "close"])
        out = pd.DataFrame(
            {
                "date": pd.to_datetime(hist.index, utc=True).date,
                "close": pd.to_numeric(hist["Close"], errors="coerce"),
            }
        )
        return out.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    except (ImportError, ValueError, KeyError, OSError):
        return pd.DataFrame(columns=["date", "close"])


def _fetch_yahoo() -> pd.DataFrame:
    import time

    for attempt in range(3):
        try:
            resp = requests.get(
                YAHOO_URL,
                headers=HEADERS,
                params={"interval": "1d", "range": "5y"},
                timeout=45,
            )
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            df = _parse_yahoo(resp.json())
            if not df.empty:
                return df
        except (requests.RequestException, ValueError):
            if attempt < 2:
                time.sleep(1)
    return pd.DataFrame(columns=["date", "close"])


def _parse_stooq(text: str) -> pd.DataFrame:
    df = pd.read_csv(StringIO(text))
    cols = {c.lower(): c for c in df.columns}
    date_col = cols.get("date")
    close_col = cols.get("close")
    if date_col is None or close_col is None:
        return pd.DataFrame(columns=["date", "close"])
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df[date_col], errors="coerce").dt.date,
            "close": pd.to_numeric(df[close_col], errors="coerce"),
        }
    )
    return out.dropna().sort_values("date").reset_index(drop=True)


def _parse_yahoo(payload: dict) -> pd.DataFrame:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame(columns=["date", "close"])
    timestamps = result[0].get("timestamp") or []
    closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
    rows = []
    for ts, close in zip(timestamps, closes, strict=False):
        if close is None:
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(int(ts), tz=UTC).date(),
                "close": float(close),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["date", "close"])
    return pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def fetch_ng_closes() -> pd.DataFrame:
    eia_frames: list[pd.DataFrame] = []
    for series in ("RNGWHHD", "RNGC1"):
        try:
            eia_frames.append(_fetch_eia_series(series))
        except (requests.RequestException, ValueError, KeyError):
            pass
    eia = _newest(eia_frames)
    fallback = _newest([_fetch_yfinance(), _fetch_yahoo(), _fetch_stooq()])
    if eia.empty:
        return fallback
    if fallback.empty:
        return eia
    # EIA for history; Yahoo/Stooq fill gaps when spot lags (common around EIA week).
    extra = fallback[~fallback["date"].isin(set(eia["date"])) | (fallback["date"] > eia["date"].max())]
    return _merge_closes(eia, extra)


def load_or_fetch_prices(*, refresh: bool = True) -> pd.DataFrame:
    ensure_dirs()
    cached = pd.DataFrame(columns=["date", "close"])
    if CACHE_PATH.exists():
        cached = pd.read_parquet(CACHE_PATH)
        cached["date"] = pd.to_datetime(cached["date"]).dt.date
    if not refresh and not cached.empty:
        return cached
    fetched = fetch_ng_closes()
    if fetched.empty:
        return cached
    path = INTERIM_DIR / "ng_front_daily.parquet"
    fetched.to_parquet(path, index=False)
    return fetched


def close_on_or_before(prices: pd.DataFrame, day: date) -> tuple[date, float] | None:
    if prices.empty:
        return None
    eligible = prices[prices["date"] <= day]
    if eligible.empty:
        return None
    row = eligible.iloc[-1]
    return row["date"], float(row["close"])


def close_on_or_after(prices: pd.DataFrame, day: date) -> tuple[date, float] | None:
    if prices.empty:
        return None
    eligible = prices[prices["date"] >= day]
    if eligible.empty:
        return None
    row = eligible.iloc[0]
    return row["date"], float(row["close"])


def entry_exit_fills(prices: pd.DataFrame, week_ending: date) -> dict | None:
    """Wednesday-or-prior close into Thursday close around the EIA print."""
    release = report_release_datetime(week_ending).date()
    entry_day = release - timedelta(days=1)
    entry = close_on_or_before(prices, entry_day)
    exit_ = close_on_or_before(prices, release)
    if exit_ is None or exit_[0] < release:
        exit_ = close_on_or_after(prices, release)
    if entry is None or exit_ is None:
        return None
    # Don't settle until we have the print-day (or next session) close.
    if exit_[0] < release:
        return None
    if exit_[0] > release + timedelta(days=3):
        return None
    return {
        "entry_date": entry[0],
        "entry_price": entry[1],
        "exit_date": exit_[0],
        "exit_price": exit_[1],
    }
