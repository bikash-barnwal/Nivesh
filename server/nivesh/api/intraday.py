"""Intraday stock market data endpoints (1-minute candles and session VWAP)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from nivesh.api.common import Envelope, envelope
from nivesh.data.engine import get_engine

router = APIRouter(prefix="/api/stocks", tags=["stocks-intraday"])


@router.get("/{symbol}/intraday", response_model=Envelope[dict[str, Any]])
def stock_intraday(
    symbol: str,
    trade_date: Annotated[date | None, Query(alias="date", help="Trading date (YYYY-MM-DD); default=latest available")] = None,
) -> Envelope[dict[str, Any]]:
    """Return 1-minute OHLCV candles and calculated running VWAP for a given stock session."""
    engine = get_engine()
    views = engine.views()
    if "stock_intraday_1m" not in views:
        raise HTTPException(
            status_code=503,
            detail="Intraday 1-minute candle view is not available yet. Please complete backfill or ingest data.",
        )

    sym = symbol.strip().upper()

    # If no date supplied, find latest session available for this symbol
    if trade_date is None:
        latest_res = engine.query(
            "SELECT max(CAST(timestamp AS DATE)) FROM stock_intraday_1m WHERE symbol = ?",
            [sym],
        ).rows
        if not latest_res or not latest_res[0][0]:
            raise HTTPException(status_code=404, detail=f"No intraday data found for symbol '{sym}'")
        target_date = latest_res[0][0]
    else:
        target_date = trade_date

    # Query 1m candles for target date ordered ascending
    # Cast timestamp to ISO string to ensure consistent client parsing
    rows = engine.query(
        """
        SELECT 
            strftime(timestamp, '%Y-%m-%dT%H:%M:%S%z') AS ts_str,
            epoch(timestamp) AS epoch_seconds,
            open,
            high,
            low,
            close,
            volume,
            open_interest
        FROM stock_intraday_1m 
        WHERE symbol = ? AND CAST(timestamp AS DATE) = ?
        ORDER BY timestamp ASC
        """,
        [sym, target_date],
    ).records()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No intraday candles found for symbol '{sym}' on date {target_date}",
        )

    # Compute running intraday VWAP: Cumulative(Price * Volume) / Cumulative(Volume)
    cum_vol = 0
    cum_pv = 0.0
    candles: list[dict[str, Any]] = []

    for r in rows:
        vol = int(r["volume"])
        typical_price = (r["high"] + r["low"] + r["close"]) / 3.0
        cum_vol += vol
        cum_pv += typical_price * vol
        vwap = round(cum_pv / cum_vol, 2) if cum_vol > 0 else r["close"]

        candles.append({
            "timestamp": r["ts_str"],
            "time": r["epoch_seconds"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": vol,
            "vwap": vwap,
        })

    day_open = candles[0]["open"]
    day_close = candles[-1]["close"]
    day_high = max(c["high"] for c in candles)
    day_low = min(c["low"] for c in candles)
    total_volume = sum(c["volume"] for c in candles)
    session_vwap = round(cum_pv / cum_vol, 2) if cum_vol > 0 else day_close

    payload = {
        "symbol": sym,
        "date": str(target_date),
        "summary": {
            "open": day_open,
            "high": day_high,
            "low": day_low,
            "close": day_close,
            "volume": total_volume,
            "vwap": session_vwap,
            "candle_count": len(candles),
        },
        "candles": candles,
    }

    return envelope(
        payload,
        as_of=target_date,
        engine=engine,
        source="stock_intraday_1m",
        calculation="1-minute OHLCV candles from Upstox with running VWAP",
    )
