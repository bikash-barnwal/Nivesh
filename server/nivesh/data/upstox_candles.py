"""Upstox 1-minute candle normalizer and Parquet writer.

Responsible for:
  - Parsing raw Upstox candle arrays into a typed Polars DataFrame
  - Validating continuity (no gaps > 60 s during market hours)
  - Writing one Parquet file per trading day per symbol (no merging, thread-safe)

Partition layout:
  stock_intraday_1m/symbol={SYMBOL}/{YYYY-MM-DD}.parquet
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nivesh.config import settings

logger = logging.getLogger(__name__)

# NSE trading hours in IST (UTC+5:30)
_MARKET_OPEN_H = 9
_MARKET_OPEN_M = 15
_MARKET_CLOSE_H = 15
_MARKET_CLOSE_M = 30

# Maximum allowed gap within market hours (seconds)
_MAX_GAP_SECONDS = 70  # 60s + 10s tolerance


def _parse_upstox_candles(raw: list[list[Any]], symbol: str, instrument_key: str) -> pl.DataFrame:
    """
    Parse raw Upstox candle list into a typed Polars DataFrame.

    Each Upstox candle is a 7-element list:
      [timestamp_str, open, high, low, close, volume, open_interest]

    Example timestamp: '2026-10-23T09:15:00+05:30'
    """
    if not raw:
        return _empty_candle_df()

    rows = []
    for candle in raw:
        if len(candle) < 6:
            logger.warning("Skipping malformed candle for %s: %s", symbol, candle)
            continue
        ts_str, open_, high, low, close, volume = candle[:6]
        oi = candle[6] if len(candle) > 6 else 0

        try:
            ts = datetime.fromisoformat(str(ts_str))
        except ValueError:
            logger.warning("Skipping candle with unparseable timestamp for %s: %s", symbol, ts_str)
            continue

        rows.append({
            "timestamp": ts,
            "symbol": symbol,
            "instrument_key": instrument_key,
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": int(volume),
            "open_interest": int(oi),
        })

    if not rows:
        return _empty_candle_df()

    return pl.DataFrame(rows).with_columns([
        pl.col("timestamp").dt.convert_time_zone("Asia/Kolkata"),
    ])


def _empty_candle_df() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "timestamp": pl.Datetime("us", "Asia/Kolkata"),
        "symbol": pl.Utf8,
        "instrument_key": pl.Utf8,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Int64,
        "open_interest": pl.Int64,
    })


def _validate_prices(df: pl.DataFrame, symbol: str) -> None:
    """Assert that OHLCV prices are positive and OHLC ordering is valid."""
    for col in ("open", "high", "low", "close"):
        bad = df.filter(pl.col(col) <= 0.0)
        if len(bad) > 0:
            raise ValueError(f"{symbol}: Non-positive price in column '{col}': {bad.head(1)}")

    invalid = df.filter((pl.col("high") < pl.col("open")) | (pl.col("high") < pl.col("close")))
    if len(invalid) > 0:
        raise ValueError(f"{symbol}: High < Open or High < Close in {len(invalid)} candle(s)")

    invalid_low = df.filter((pl.col("low") > pl.col("open")) | (pl.col("low") > pl.col("close")))
    if len(invalid_low) > 0:
        raise ValueError(f"{symbol}: Low > Open or Low > Close in {len(invalid_low)} candle(s)")


def _validate_continuity(df: pl.DataFrame, symbol: str) -> None:
    """Warn on gaps > 70 seconds between consecutive candles (allowing for halts)."""
    if len(df) < 2:
        return

    sorted_df = df.sort("timestamp")
    timestamps = sorted_df["timestamp"].to_list()

    for i in range(1, len(timestamps)):
        prev_ts = timestamps[i - 1]
        curr_ts = timestamps[i]
        gap = (curr_ts - prev_ts).total_seconds()
        if gap > _MAX_GAP_SECONDS:
            logger.warning(
                "%s: Gap of %.0fs between %s and %s (may be circuit hit or trading halt)",
                symbol, gap, prev_ts, curr_ts,
            )


def normalize_candles(
    raw_candles: list[list[Any]],
    symbol: str,
    instrument_key: str,
) -> pl.DataFrame:
    """
    Parse, validate, and sort raw Upstox candle data into a clean DataFrame.

    Args:
        raw_candles: List of 7-element arrays returned by Upstox historical API.
        symbol: NSE trading symbol (e.g. 'RELIANCE').
        instrument_key: Upstox instrument key (e.g. 'NSE_EQ|INE002A01018').

    Returns:
        Polars DataFrame sorted ascending by timestamp.
    """
    df = _parse_upstox_candles(raw_candles, symbol, instrument_key)
    if len(df) == 0:
        logger.warning("%s: No candles parsed from %d raw records", symbol, len(raw_candles))
        return df

    # Upstox returns candles in DESCENDING order; sort ascending
    df = df.sort("timestamp")

    _validate_prices(df, symbol)
    _validate_continuity(df, symbol)

    logger.info("%s: Normalized %d 1m candles (%s → %s)", symbol, len(df),
                df["timestamp"][0], df["timestamp"][-1])
    return df


def write_candles(df: pl.DataFrame, symbol: str, trade_date: date, parquet_root: Path | None = None) -> Path:
    """
    Write one trading day's candles to a dedicated Parquet file — one file per symbol per day.

    Output path:
        {parquet_root}/stock_intraday_1m/symbol={symbol}/{YYYY-MM-DD}.parquet

    One-file-per-day design avoids concurrent write collisions entirely:
    each fetch thread writes to a unique path, so no locking or merging is needed.
    If the file already exists (idempotent re-run), it is overwritten atomically via a .part temp file.
    """
    root = parquet_root or settings.parquet_dir
    partition_dir = root / "stock_intraday_1m" / f"symbol={symbol}"
    partition_dir.mkdir(parents=True, exist_ok=True)

    part_file = partition_dir / f"{trade_date}.parquet"
    tmp_file = partition_dir / f"{trade_date}.parquet.part"

    # Write to .part then atomically replace — protects against partial reads
    df.write_parquet(tmp_file)
    tmp_file.replace(part_file)

    logger.info("%s: Wrote %d candles to %s", symbol, len(df), part_file)
    return part_file
