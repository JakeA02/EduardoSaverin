"""Kalshi API client with RSA-PSS signature authentication."""
import base64
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from backend.config import REPO_ROOT, settings

logger = logging.getLogger("trading_bot")

BASE_URL = os.environ.get(
    "KALSHI_PROD_DATA_BASE_URL",
    "https://api.elections.kalshi.com/trade-api/v2",
).rstrip("/")


def _normalize_pem(raw: str) -> bytes:
    """Accept inline PEM or escaped newlines from env vars."""
    text = raw.strip().replace("\\n", "\n")
    if "BEGIN" not in text:
        raise ValueError("KALSHI_PRIVATE_KEY does not look like a PEM key")
    return text.encode("utf-8")


def _default_key_path() -> Path:
    return REPO_ROOT / "rsa.txt"


class KalshiClient:
    """Async Kalshi API client using RSA-PSS signature auth."""

    def __init__(self):
        self._private_key = None

    def _load_private_key(self):
        """Load RSA private key from env or file (lazy, cached)."""
        if self._private_key is not None:
            return self._private_key

        inline = (settings.KALSHI_PRIVATE_KEY or "").strip()
        if inline:
            pem_data = _normalize_pem(inline)
        else:
            key_path = settings.KALSHI_PRIVATE_KEY_PATH or str(_default_key_path())
            pem_path = Path(key_path).expanduser()
            if not pem_path.is_file():
                raise ValueError(
                    f"Kalshi private key not found at {pem_path}. "
                    "Set KALSHI_PRIVATE_KEY or KALSHI_PRIVATE_KEY_PATH."
                )
            pem_data = pem_path.read_bytes()

        self._private_key = serialization.load_pem_private_key(pem_data, password=None)
        return self._private_key

    def _sign_request(self, method: str, path: str) -> Dict[str, str]:
        """
        Generate auth headers for a Kalshi API request.

        Signature = RSA-PSS-sign(timestamp_ms + METHOD + path)
        where path = /trade-api/v2/... (no query params).
        """
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{path}"

        private_key = self._load_private_key()
        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": settings.KALSHI_API_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "Content-Type": "application/json",
        }

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> dict:
        """
        Authenticated GET request to Kalshi API.

        Args:
            path: API path after /trade-api/v2 (e.g., "/markets")
            params: Query parameters (not included in signature)
        """
        full_path = f"/trade-api/v2{path}"
        url = f"{BASE_URL}{path}"
        headers = self._sign_request("GET", full_path)

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()

    async def get_markets(self, params: Optional[Dict[str, Any]] = None) -> dict:
        """Fetch markets with optional filters."""
        return await self.get("/markets", params=params)

    async def get_market(self, ticker: str) -> dict:
        """Fetch a single market by ticker."""
        return await self.get(f"/markets/{ticker}")

    async def get_balance(self) -> dict:
        """Get portfolio balance (useful for auth test)."""
        return await self.get("/portfolio/balance")


def kalshi_credentials_present() -> bool:
    """Check if Kalshi API credentials are configured."""
    if not settings.KALSHI_API_KEY_ID:
        return False
    if (settings.KALSHI_PRIVATE_KEY or "").strip():
        return True
    key_path = settings.KALSHI_PRIVATE_KEY_PATH or str(_default_key_path())
    return Path(key_path).expanduser().is_file()
