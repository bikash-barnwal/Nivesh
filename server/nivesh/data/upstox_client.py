"""Upstox read-only HTTP REST client.

Wraps httpx for safe, token-redacting access to Upstox market-data APIs.
Never exposes authorization headers or token values in logs or errors.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from nivesh.config import settings

logger = logging.getLogger(__name__)

UPSTOX_BASE_URL = "https://api.upstox.com"
UPSTOX_API_VERSION = "v2"

# Order-write endpoints that must NEVER be called
_FORBIDDEN_PATHS = {
    "/v2/order/place",
    "/v2/order/modify",
    "/v2/order/cancel",
    "/v2/order/cancel-multi",
    "/v2/portfolio/convert-position",
}


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with Authorization redacted for logging."""
    return {k: "***REDACTED***" if k.lower() == "authorization" else v for k, v in headers.items()}


def _safe_error(url: str, status_code: int, body: str) -> str:
    """Build an error string without leaking any token or sensitive value."""
    # Strip any Bearer token from the URL (should never be there, but guard anyway)
    safe_url = url.split("?")[0]
    return f"Upstox API error {status_code} at {safe_url}: {body[:200]}"


class UpstoxClient:
    """Safe, read-only Upstox market-data client using httpx."""

    def __init__(self, token: str | None = None, base_url: str = UPSTOX_BASE_URL) -> None:
        resolved = token or settings.resolved_upstox_token
        if not resolved:
            raise ValueError(
                "Upstox token not configured. Set UPSTOX_API_KEY in server/.env"
            )
        self._headers = {
            "Authorization": f"Bearer {resolved}",
            "Accept": "application/json",
        }
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> UpstoxClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a safe GET request; never calls order-write endpoints."""
        if path in _FORBIDDEN_PATHS:
            raise PermissionError(f"Path '{path}' is an order-write endpoint and is forbidden.")

        url = f"{self.base_url}/{UPSTOX_API_VERSION}{path}"
        logger.debug("GET %s params=%s", url.split("?")[0], params)

        response = self._client.get(url, headers=self._headers, params=params)

        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                _safe_error(url, response.status_code, response.text),
                request=response.request,
                response=response,
            )

        return response.json()

    def get_quotes(self, instrument_keys: list[str]) -> dict[str, Any]:
        """
        Fetch full market quote for one or more instrument keys.
        Returns the raw 'data' dict from the Upstox response.

        Example instrument_key: 'NSE_EQ|INE002A01018'
        """
        if not instrument_keys:
            return {}
        params = {"instrument_key": ",".join(instrument_keys)}
        resp = self._get("/market-quote/quotes", params)
        return resp.get("data", {})

    def get_historical_1m(
        self,
        instrument_key: str,
        from_date: date,
        to_date: date,
    ) -> list[list[Any]]:
        """
        Fetch 1-minute historical OHLCV candles.

        Returns a list of candles, each in the format:
            [timestamp_str, open, high, low, close, volume, oi]
        """
        path = f"/historical-candle/{instrument_key}/1minute/{to_date}/{from_date}"
        resp = self._get(path)
        candles = resp.get("data", {}).get("candles", [])
        return candles

    def get_ltp(self, instrument_keys: list[str]) -> dict[str, Any]:
        """
        Fetch last-traded price for one or more instrument keys.
        Returns the raw 'data' dict from the Upstox response.
        """
        if not instrument_keys:
            return {}
        params = {"instrument_key": ",".join(instrument_keys)}
        resp = self._get("/market-quote/ltp", params)
        return resp.get("data", {})
