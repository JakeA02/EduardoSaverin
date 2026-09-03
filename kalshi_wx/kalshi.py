"""Public Kalshi market data (no auth). Climate and Weather books only."""

from __future__ import annotations

import time
from typing import Any

import requests

from kalshi_wx.config import KALSHI_PROD_BASE_URL

_TIMEOUT = 30
_MAX_ATTEMPTS = 5


class KalshiError(RuntimeError):
    pass


def kalshi_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{KALSHI_PROD_BASE_URL.rstrip('/')}{path if path.startswith('/') else '/' + path}"
    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = requests.get(url, params=params, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            last = exc
            time.sleep(0.4 * (attempt + 1))
            continue
        if resp.status_code == 429:
            time.sleep(0.8 * (attempt + 1))
            last = KalshiError(f"429 {path}")
            continue
        if resp.status_code >= 500:
            last = KalshiError(f"{resp.status_code} {path}: {resp.text[:200]}")
            time.sleep(0.4 * (attempt + 1))
            continue
        if not resp.ok:
            raise KalshiError(f"{resp.status_code} {path}: {resp.text[:300]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise KalshiError(f"Unexpected payload for {path}")
        return data
    raise last if last else KalshiError(f"request failed: {path}")


def list_markets(*, series_ticker: str, status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(20):
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": status,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        data = kalshi_get("/markets", params)
        batch = data.get("markets") or []
        out.extend(batch)
        cursor = data.get("cursor") or None
        if not cursor or not batch:
            break
        time.sleep(0.12)
    return out


def get_market(ticker: str) -> dict[str, Any]:
    data = kalshi_get(f"/markets/{ticker}")
    market = data.get("market")
    if not isinstance(market, dict):
        raise KalshiError(f"No market payload for {ticker}")
    return market
