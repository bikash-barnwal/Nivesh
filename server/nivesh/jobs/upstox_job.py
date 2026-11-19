"""APScheduler job: daily intraday candle fetch.

Runs at 16:15 IST Mon-Fri via scheduler.py.
Fetches the previous trading day's 1-minute candles for the full EQ universe.
Logically independent of the EOD pipeline and the browser/frontend.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from nivesh.config import settings

log = logging.getLogger(__name__)


def _log_job(record: dict[str, Any]) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    with (settings.logs_dir / "intraday_jobs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def run_intraday_fetch_job(trade_date: date | None = None) -> dict[str, Any]:
    """Fetch 1-minute candles for the full NSE EQ universe for one trading date.

    Args:
        trade_date: The session to fetch. Defaults to the most recent weekday.

    Returns:
        Job record dict (also written to data/logs/intraday_jobs.jsonl).
    """
    from nivesh.data.upstox_calendar import classify, SessionStatus
    from nivesh.data.upstox_checkpoint import FetchCheckpoint
    from nivesh.data.upstox_fetcher import build_symbol_universe, fetch_universe
    from nivesh.data.upstox_limiter import get_limiter
    from nivesh.data.upstox_resolver import UpstoxResolver

    started = time.perf_counter()
    target = trade_date or _last_trading_day()

    record: dict[str, Any] = {
        "job": "intraday_fetch",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "trade_date": str(target),
    }

    try:
        cal_status = classify(target)
        if cal_status not in (SessionStatus.TRADING, SessionStatus.SPECIAL_SESSION):
            record["status"] = "skipped"
            record["reason"] = f"calendar status: {cal_status}"
            log.info("Intraday fetch skipped for %s: %s", target, cal_status)
            _log_job(record)
            return record

        universe = build_symbol_universe()
        resolver = UpstoxResolver()
        resolver.load()
        limiter = get_limiter()
        checkpoint = FetchCheckpoint()

        log.info("Intraday fetch: %d symbols for %s", len(universe), target)
        results = fetch_universe(
            universe, target, target,
            resolver=resolver, limiter=limiter, checkpoint=checkpoint,
        )

        counts = Counter(r.status for r in results)
        record["counts"] = dict(counts)
        record["failed_examples"] = [
            {"symbol": r.symbol, "error": r.error}
            for r in results if r.status == "failed"
        ][:10]
        record["status"] = "ok" if counts.get("failed", 0) == 0 else "partial"

    except Exception as exc:
        log.exception("Intraday fetch job failed for %s", target)
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"

    record["duration_s"] = round(time.perf_counter() - started, 1)
    _log_job(record)
    return record


def _last_trading_day() -> date:
    """Return the most recent weekday that is not today."""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d
