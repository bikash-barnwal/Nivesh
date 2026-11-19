"""Tests for Milestone 2: calendar, rate limiter, checkpoint, fetcher.

All unit tests — no network, no Upstox token required.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from nivesh.data.upstox_calendar import (
    SessionStatus, classify, is_trading_day, trading_days, calendar_days_back,
)
from nivesh.data.upstox_limiter import UpstoxRateLimiter, reset_limiter
from nivesh.data.upstox_checkpoint import FetchCheckpoint
from nivesh.data.upstox_fetcher import FetchResult


# ---------------------------------------------------------------------------
# Calendar tests
# ---------------------------------------------------------------------------

class TestCalendar:
    def test_weekend_saturday(self) -> None:
        # 2026-11-21 is a Saturday
        assert classify(date(2026, 11, 21)) == SessionStatus.WEEKEND

    def test_weekend_sunday(self) -> None:
        assert classify(date(2026, 11, 22)) == SessionStatus.WEEKEND

    def test_republic_day_holiday(self) -> None:
        assert classify(date(2026, 1, 26)) == SessionStatus.EXCHANGE_HOLIDAY

    def test_christmas_holiday(self) -> None:
        assert classify(date(2026, 12, 25)) == SessionStatus.EXCHANGE_HOLIDAY

    def test_normal_weekday(self) -> None:
        # 2026-11-19 is a Thursday, not a holiday
        assert classify(date(2026, 11, 19)) == SessionStatus.TRADING

    def test_is_trading_day_true(self) -> None:
        assert is_trading_day(date(2026, 11, 19)) is True

    def test_is_trading_day_false_weekend(self) -> None:
        assert is_trading_day(date(2026, 11, 21)) is False

    def test_is_trading_day_false_holiday(self) -> None:
        assert is_trading_day(date(2026, 1, 26)) is False

    def test_trading_days_range(self) -> None:
        # Week of 2026-11-16 (Mon) to 2026-11-20 (Fri) — no holidays
        days = trading_days(date(2026, 11, 16), date(2026, 11, 20))
        assert len(days) == 5
        assert all(d.weekday() < 5 for d in days)

    def test_trading_days_excludes_weekend(self) -> None:
        # 2026-11-21 (Sat) and 2026-11-22 (Sun) must not appear
        days = trading_days(date(2026, 11, 20), date(2026, 11, 23))
        assert date(2026, 11, 21) not in days
        assert date(2026, 11, 22) not in days

    def test_calendar_days_back(self) -> None:
        end = date(2026, 11, 19)
        start, actual_end = calendar_days_back(30, end)
        assert actual_end == end
        assert (end - start).days == 30


# ---------------------------------------------------------------------------
# Rate limiter tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def setup_method(self) -> None:
        reset_limiter()

    def test_wait_does_not_raise(self) -> None:
        limiter = UpstoxRateLimiter(rate_per_min=600.0)  # very fast for tests
        limiter.wait("historical")  # should not raise

    def test_buckets_exist_for_all_categories(self) -> None:
        limiter = UpstoxRateLimiter()
        assert "historical" in limiter._buckets
        assert "quote" in limiter._buckets
        assert "default" in limiter._buckets

    def test_429_triggers_backoff(self) -> None:
        limiter = UpstoxRateLimiter(rate_per_min=600.0)
        before = limiter._backoff_until
        limiter.record_response(429, None)
        assert limiter._backoff_until > before

    def test_200_does_not_trigger_backoff(self) -> None:
        limiter = UpstoxRateLimiter(rate_per_min=600.0)
        before = limiter._backoff_until
        limiter.record_response(200, None)
        assert limiter._backoff_until == before

    def test_retry_after_header_respected(self) -> None:
        limiter = UpstoxRateLimiter(rate_per_min=600.0)
        limiter.record_response(429, "5")
        # backoff_until should be ~5s from now
        assert limiter._backoff_until > time.monotonic() + 4

    def test_set_rate_updates_buckets(self) -> None:
        limiter = UpstoxRateLimiter(rate_per_min=60.0)
        limiter.set_rate(120.0)
        assert limiter._buckets["historical"].rate_per_s == pytest.approx(120 * 0.70 / 60, rel=0.01)


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------

class TestFetchCheckpoint:
    def test_new_symbol_not_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = FetchCheckpoint(Path(tmpdir))
            assert not cp.is_done("RELIANCE", date(2026, 10, 23))

    def test_mark_ok_makes_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = FetchCheckpoint(Path(tmpdir))
            cp.mark({"symbol": "RELIANCE", "instrument_key": "k", "trade_date": date(2026, 10, 23),
                     "status": "ok", "candle_count": 375})
            assert cp.is_done("RELIANCE", date(2026, 10, 23))

    def test_mark_failed_not_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = FetchCheckpoint(Path(tmpdir))
            cp.mark({"symbol": "TATA", "instrument_key": "k", "trade_date": date(2026, 10, 23),
                     "status": "failed", "error": "timeout"})
            assert not cp.is_done("TATA", date(2026, 10, 23))
            assert cp.is_recorded("TATA", date(2026, 10, 23))

    def test_persistence_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir)
            cp1 = FetchCheckpoint(p)
            cp1.mark({"symbol": "INFOSYS", "instrument_key": "k", "trade_date": date(2026, 10, 23),
                      "status": "ok", "candle_count": 300})
            cp2 = FetchCheckpoint(p)  # new instance reads same file
            assert cp2.is_done("INFOSYS", date(2026, 10, 23))

    def test_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = FetchCheckpoint(Path(tmpdir))
            cp.mark({"symbol": "A", "instrument_key": "", "trade_date": date(2026, 10, 23),
                     "status": "ok", "candle_count": 1})
            cp.mark({"symbol": "B", "instrument_key": "", "trade_date": date(2026, 10, 23),
                     "status": "failed", "error": "err"})
            s = cp.summary()
            assert s["ok"] == 1
            assert s["failed"] == 1

    def test_thread_safe_concurrent_marks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = FetchCheckpoint(Path(tmpdir))
            errors = []

            def _mark(i: int) -> None:
                try:
                    cp.mark({"symbol": f"SYM{i}", "instrument_key": "", "trade_date": date(2026, 10, i % 28 + 1),
                             "status": "ok", "candle_count": i})
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=_mark, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors
