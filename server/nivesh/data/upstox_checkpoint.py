"""Upstox candle fetch checkpoint — tracks per-symbol per-date fetch state.

Stored as a JSONL file so interrupted jobs resume safely without
re-downloading already fetched data.

Each line is a JSON object:
  {symbol, instrument_key, trade_date, status, fetched_at, candle_count, error}

Statuses mirror SessionStatus from upstox_calendar plus fetch outcomes:
  "ok"           — candles fetched and written to Parquet
  "empty"        — API returned 0 candles (treat as data_unavailable)
  "holiday"      — calendar says exchange was closed
  "skipped"      — symbol had no Upstox mapping
  "failed"       — HTTP error after all retries
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nivesh.config import settings

logger = logging.getLogger(__name__)

_CHECKPOINT_FILE = "upstox_fetch_checkpoint.jsonl"


class FetchCheckpoint:
    """Thread-safe checkpoint store backed by a JSONL file.

    On load, reads all existing records into an in-memory set.
    On mark(), appends one line and updates the set atomically.
    """

    def __init__(self, checkpoint_dir: Path | None = None) -> None:
        self._dir = checkpoint_dir or settings.logs_dir
        self._file = self._dir / _CHECKPOINT_FILE
        self._lock = threading.Lock()
        # Key: (symbol, trade_date_iso)  →  status string
        self._done: dict[tuple[str, str], str] = {}
        self._load()

    def _load(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        if not self._file.exists():
            return
        with open(self._file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    key = (rec["symbol"], rec["trade_date"])
                    self._done[key] = rec["status"]
                except (json.JSONDecodeError, KeyError):
                    logger.warning("Checkpoint: skipping malformed line: %s", line[:80])
        logger.info("Checkpoint: loaded %d existing records from %s", len(self._done), self._file)

    def is_done(self, symbol: str, trade_date: date) -> bool:
        """True if this symbol+date was already successfully fetched."""
        with self._lock:
            status = self._done.get((symbol, str(trade_date)))
        return status == "ok"

    def is_recorded(self, symbol: str, trade_date: date) -> bool:
        """True if any record (including failures) exists for this symbol+date."""
        with self._lock:
            return (symbol, str(trade_date)) in self._done

    def mark(self, record: dict[str, Any]) -> None:
        """Append one record to the checkpoint file."""
        symbol = record["symbol"]
        trade_date = record["trade_date"]
        status = record["status"]
        entry = {
            "symbol": symbol,
            "instrument_key": record.get("instrument_key", ""),
            "trade_date": str(trade_date),
            "status": status,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "candle_count": record.get("candle_count", 0),
            "error": record.get("error", ""),
        }
        with self._lock:
            self._done[(symbol, str(trade_date))] = status
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def summary(self) -> dict[str, int]:
        """Return count of records by status."""
        counts: dict[str, int] = {}
        with self._lock:
            for status in self._done.values():
                counts[status] = counts.get(status, 0) + 1
        return counts

    def pending_count(self, symbols: list[str], dates: list[date]) -> int:
        """Count how many (symbol, date) pairs have not yet been successfully fetched."""
        with self._lock:
            return sum(
                1 for sym in symbols for d in dates
                if self._done.get((sym, str(d))) != "ok"
            )
