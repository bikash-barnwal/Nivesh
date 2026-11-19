"""Upstox multi-symbol candle fetcher with rate limiting and checkpointing.

Fetches 1-minute OHLCV candles for a universe of NSE EQ symbols and
writes them to Parquet, resuming safely from interruptions.

Entry points:
  fetch_universe(symbols, from_date, to_date, ...)   — programmatic API
  build_symbol_universe()                             — loads all EQ from security_master
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import polars as pl

from nivesh.config import settings
from nivesh.data.upstox_calendar import SessionStatus, classify, trading_days
from nivesh.data.upstox_candles import normalize_candles, write_candles
from nivesh.data.upstox_checkpoint import FetchCheckpoint
from nivesh.data.upstox_client import UpstoxClient
from nivesh.data.upstox_limiter import UpstoxRateLimiter, get_limiter
from nivesh.data.upstox_resolver import UpstoxResolver

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
MAX_WORKERS = 4  # concurrent threads; keep low to avoid overwhelming single limiter


@dataclass
class FetchResult:
    symbol: str
    trade_date: date
    status: str           # "ok" | "empty" | "holiday" | "skipped" | "failed"
    candle_count: int = 0
    error: str = ""
    instrument_key: str = ""


def build_symbol_universe(parquet_dir: Path | None = None) -> list[dict[str, str]]:
    """Load all NSE EQ symbols from security_master.

    Returns list of dicts: {symbol, isin}
    Only 'EQ' series symbols are included. Suspended/unavailable are NOT
    silently dropped — they appear in the resolver miss log instead.
    """
    root = parquet_dir or settings.parquet_dir
    df = pl.read_parquet(root / "security_master")
    eq = df.filter(pl.col("series") == "EQ").select(["symbol", "isin"])
    return [{"symbol": r["symbol"], "isin": r["isin"]} for r in eq.iter_rows(named=True)]


def _fetch_one(
    symbol: str,
    instrument_key: str,
    trade_date: date,
    client: UpstoxClient,
    limiter: UpstoxRateLimiter,
    checkpoint: FetchCheckpoint,
    parquet_root: Path,
) -> FetchResult:
    """Fetch and persist one symbol for one trading date. Called from thread pool."""
    if checkpoint.is_done(symbol, trade_date):
        return FetchResult(symbol, trade_date, "ok", instrument_key=instrument_key)

    cal_status = classify(trade_date)
    if cal_status == SessionStatus.EXCHANGE_HOLIDAY:
        result = FetchResult(symbol, trade_date, "holiday", instrument_key=instrument_key)
        checkpoint.mark({"symbol": symbol, "instrument_key": instrument_key,
                         "trade_date": trade_date, "status": "holiday"})
        return result

    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        limiter.wait("historical")
        try:
            raw = client.get_historical_1m(instrument_key, from_date=trade_date, to_date=trade_date)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            # Check if it was a 429/5xx — the client raises HTTPStatusError
            status_code = getattr(getattr(exc, "response", None), "status_code", 0)
            retry_after = None
            if hasattr(exc, "response") and exc.response is not None:
                retry_after = exc.response.headers.get("Retry-After")
            limiter.record_response(status_code or 500, retry_after)
            logger.warning("%s %s attempt %d/%d failed: %s", symbol, trade_date, attempt, MAX_RETRIES, last_error)
            continue

        if not raw:
            rec = FetchResult(symbol, trade_date, "empty", instrument_key=instrument_key)
            checkpoint.mark({"symbol": symbol, "instrument_key": instrument_key,
                             "trade_date": trade_date, "status": "empty", "candle_count": 0})
            return rec

        df = normalize_candles(raw, symbol, instrument_key)
        write_candles(df, symbol, trade_date, parquet_root)
        rec = FetchResult(symbol, trade_date, "ok", len(df), instrument_key=instrument_key)
        checkpoint.mark({"symbol": symbol, "instrument_key": instrument_key,
                         "trade_date": trade_date, "status": "ok", "candle_count": len(df)})
        return rec

    # All retries exhausted
    rec = FetchResult(symbol, trade_date, "failed", error=last_error, instrument_key=instrument_key)
    checkpoint.mark({"symbol": symbol, "instrument_key": instrument_key,
                     "trade_date": trade_date, "status": "failed", "error": last_error})
    return rec


def fetch_universe(
    symbols: list[dict[str, str]],  # [{symbol, isin}, ...]
    from_date: date,
    to_date: date,
    *,
    resolver: UpstoxResolver | None = None,
    client: UpstoxClient | None = None,
    limiter: UpstoxRateLimiter | None = None,
    checkpoint: FetchCheckpoint | None = None,
    parquet_root: Path | None = None,
    workers: int = MAX_WORKERS,
    on_result: Callable[[FetchResult], None] | None = None,
) -> list[FetchResult]:
    """Fetch 1-minute candles for all symbols over a date range.

    - Skips weekends and exchange holidays (via NSE calendar).
    - Skips symbols with no Upstox instrument key (logs them as 'skipped').
    - Resumes from checkpoint: already-fetched symbol+date pairs are skipped.
    - Rate-limits all HTTP calls through the shared limiter.
    - Writes each day's candles to Parquet immediately after fetch.
    """
    root = parquet_root or settings.parquet_dir
    _resolver = resolver or UpstoxResolver()
    if not _resolver.is_loaded:
        _resolver.load()

    _checkpoint = checkpoint or FetchCheckpoint()
    _limiter = limiter or get_limiter()

    dates = trading_days(from_date, to_date)
    if not dates:
        logger.info("No trading days in %s → %s", from_date, to_date)
        return []

    # Build work items: (symbol, instrument_key, date)
    tasks: list[tuple[str, str, date]] = []
    skipped_no_key: list[str] = []

    for entry in symbols:
        sym = entry["symbol"]
        isin = entry.get("isin", "")
        key = _resolver.resolve_isin(isin) or _resolver.resolve_symbol(sym)
        if not key:
            skipped_no_key.append(sym)
            continue
        for d in dates:
            if not _checkpoint.is_done(sym, d):
                tasks.append((sym, key, d))

    if skipped_no_key:
        logger.warning("No Upstox key found for %d symbols: %s…", len(skipped_no_key), skipped_no_key[:5])
        for sym in skipped_no_key:
            for d in dates:
                _checkpoint.mark({"symbol": sym, "instrument_key": "", "trade_date": d, "status": "skipped"})

    logger.info("Fetch plan: %d tasks (%d symbols × %d dates, %d pre-done)",
                len(tasks), len(symbols) - len(skipped_no_key), len(dates),
                len(symbols) * len(dates) - len(tasks) - len(skipped_no_key) * len(dates))

    results: list[FetchResult] = []

    def _run(args: tuple[str, str, date]) -> FetchResult:
        sym, key, d = args
        owned_client = _create_client() if client is None else None
        c = client or owned_client
        try:
            return _fetch_one(sym, key, d, c, _limiter, _checkpoint, root)
        finally:
            if owned_client:
                owned_client.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run, task): task for task in tasks}
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            if on_result:
                on_result(res)

    summary = _checkpoint.summary()
    logger.info("Fetch complete. Checkpoint summary: %s", summary)
    return results


def _create_client() -> UpstoxClient:
    return UpstoxClient()
