"""Upstox instrument master resolver.

Downloads and parses the official Upstox instrument master file to map
NSE equity symbols and ISINs to Upstox instrument keys.
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from nivesh.config import settings

logger = logging.getLogger(__name__)

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"


class UpstoxResolver:
    """Resolves NSE equity symbols and ISINs to Upstox instrument keys."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or (settings.raw_dir / "upstox")
        self.cache_file = self.cache_dir / "instruments.json.gz"
        self._symbol_to_key: dict[str, str] = {}
        self._isin_to_key: dict[str, str] = {}
        self._key_to_details: dict[str, dict[str, Any]] = {}
        self._loaded: bool = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def download_instruments_file(self, force: bool = False) -> Path:
        """Download complete.json.gz from Upstox CDN if not cached or force=True."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.cache_file.exists() and not force:
            logger.info("Using cached Upstox instruments file at %s", self.cache_file)
            return self.cache_file

        logger.info("Downloading Upstox instruments file from %s", INSTRUMENTS_URL)
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(INSTRUMENTS_URL)
            resp.raise_for_status()
            self.cache_file.write_bytes(resp.content)

        logger.info("Saved Upstox instruments file to %s (%d bytes)", self.cache_file, self.cache_file.stat().st_size)
        return self.cache_file

    def load_from_records(self, records: list[dict[str, Any]]) -> None:
        """Index in-memory records filtering for NSE equities."""
        self._symbol_to_key.clear()
        self._isin_to_key.clear()
        self._key_to_details.clear()

        for item in records:
            segment = item.get("segment")
            itype = item.get("instrument_type")
            key = item.get("instrument_key")
            symbol = item.get("trading_symbol")
            isin = item.get("isin")

            if not key or not symbol:
                continue

            # We focus on NSE equities and indices
            if segment == "NSE_EQ" and itype == "EQ":
                self._symbol_to_key[symbol.upper()] = key
                if isin:
                    self._isin_to_key[isin.upper()] = key
                self._key_to_details[key] = {
                    "symbol": symbol.upper(),
                    "name": item.get("name", ""),
                    "isin": isin or "",
                    "lot_size": item.get("lot_size", 1),
                    "tick_size": item.get("tick_size", 0.05),
                    "instrument_key": key,
                }
            elif segment == "NSE_INDEX":
                self._symbol_to_key[symbol.upper()] = key
                self._key_to_details[key] = {
                    "symbol": symbol.upper(),
                    "name": item.get("name", ""),
                    "isin": "",
                    "lot_size": item.get("lot_size", 1),
                    "tick_size": item.get("tick_size", 0.05),
                    "instrument_key": key,
                }

        self._loaded = True
        logger.info("Loaded %d NSE symbols and %d ISINs", len(self._symbol_to_key), len(self._isin_to_key))

    def load(self, file_path: Path | None = None) -> None:
        """Load and index instruments from a gzipped or plain json file."""
        path = file_path or self.cache_file
        if not path.exists():
            path = self.download_instruments_file()

        logger.info("Parsing instruments file: %s", path)
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        else:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

        records = data if isinstance(data, list) else data.get("data", [])
        self.load_from_records(records)

    def resolve_symbol(self, symbol: str) -> str | None:
        """Resolve a trading symbol (e.g. 'RELIANCE') to an instrument key."""
        return self._symbol_to_key.get(symbol.strip().upper())

    def resolve_isin(self, isin: str) -> str | None:
        """Resolve an ISIN (e.g. 'INE002A01018') to an instrument key."""
        return self._isin_to_key.get(isin.strip().upper())

    def get_details(self, instrument_key: str) -> dict[str, Any] | None:
        """Get stored details for a given instrument key."""
        return self._key_to_details.get(instrument_key)

    def all_symbols(self) -> list[str]:
        """Return list of all indexed trading symbols."""
        return sorted(self._symbol_to_key.keys())
