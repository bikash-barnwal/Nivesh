"""Centralized adaptive rate limiter for all Upstox API categories.

Design:
  - Token-bucket algorithm: fills at `rate_per_min / 60` tokens/sec.
  - Separate buckets per API category (historical, quote, default).
  - Shared across threads via a single lock.
  - Exponential backoff with jitter on 429 / 5xx responses.
  - Respects Retry-After header when present.
  - Initial budget: 100 req/min across all categories combined.
"""

from __future__ import annotations

import logging
import random
import time
import threading
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# Conservative starting budget — adjust when Upstox confirms actual limits.
DEFAULT_RATE_PER_MIN = 100.0

Category = Literal["historical", "quote", "default"]

# Per-category share of the global budget (must sum to ≤ 1.0)
_CATEGORY_SHARES: dict[str, float] = {
    "historical": 0.70,   # bulk backfill; gets the lion's share
    "quote": 0.20,        # live snapshots
    "default": 0.10,      # other endpoints
}

# Retry-eligible HTTP status codes
_RETRY_STATUSES = {429, 500, 502, 503, 504}

# Max seconds to cap exponential backoff
_MAX_BACKOFF_S = 120.0
_BASE_BACKOFF_S = 2.0


@dataclass
class _Bucket:
    """Single token-bucket for one API category."""
    rate_per_s: float           # tokens added per second
    capacity: float             # max burst tokens
    tokens: float = field(default=0.0)
    last_refill: float = field(default_factory=time.monotonic)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_s)
        self.last_refill = now

    def consume(self) -> float:
        """Block until one token is available; return wait time in seconds."""
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        # Calculate wait for token to become available
        wait = (1.0 - self.tokens) / self.rate_per_s
        time.sleep(wait)
        self._refill()
        self.tokens -= 1.0
        return wait


class UpstoxRateLimiter:
    """Thread-safe adaptive rate limiter with per-category token buckets.

    Usage:
        limiter = UpstoxRateLimiter()
        limiter.wait("historical")           # blocks until a slot is available
        limiter.record_response(429, None)   # triggers exponential backoff
    """

    def __init__(self, rate_per_min: float = DEFAULT_RATE_PER_MIN) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        self._backoff_until: float = 0.0
        self.set_rate(rate_per_min)

    def set_rate(self, rate_per_min: float) -> None:
        """Reconfigure all buckets. Safe to call at runtime."""
        with self._lock:
            for cat, share in _CATEGORY_SHARES.items():
                per_s = (rate_per_min * share) / 60.0
                # burst = 5 seconds worth of tokens
                cap = per_s * 5.0
                existing = self._buckets.get(cat)
                self._buckets[cat] = _Bucket(
                    rate_per_s=per_s,
                    capacity=cap,
                    tokens=existing.tokens if existing else cap,
                )

    def wait(self, category: Category = "default") -> None:
        """Block until a request slot is available for this category."""
        # First honour any global backoff (e.g. after a 429)
        with self._lock:
            backoff_remaining = self._backoff_until - time.monotonic()
            bucket = self._buckets.get(category, self._buckets["default"])

        if backoff_remaining > 0:
            logger.debug("Rate limiter: global backoff %.1fs for category=%s", backoff_remaining, category)
            time.sleep(backoff_remaining)

        wait_s = bucket.consume()
        if wait_s > 0.1:
            logger.debug("Rate limiter: waited %.2fs for category=%s", wait_s, category)

    def record_response(self, status_code: int, retry_after: str | None) -> None:
        """Record an HTTP response; apply backoff on 429 / 5xx."""
        if status_code not in _RETRY_STATUSES:
            return

        with self._lock:
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = _BASE_BACKOFF_S
            else:
                # Exponential backoff with jitter
                wait = min(
                    _MAX_BACKOFF_S,
                    _BASE_BACKOFF_S * (2 ** (status_code == 429 and 2 or 1)) + random.uniform(0, 1),
                )
            self._backoff_until = time.monotonic() + wait
            logger.warning("Rate limiter: %s response → backoff %.1fs", status_code, wait)


# Module-level singleton — shared by all fetcher threads in one process
_limiter: UpstoxRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_limiter(rate_per_min: float = DEFAULT_RATE_PER_MIN) -> UpstoxRateLimiter:
    """Return the shared process-level rate limiter, creating it if needed."""
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            _limiter = UpstoxRateLimiter(rate_per_min)
        return _limiter


def reset_limiter() -> None:
    """Reset the singleton — for testing only."""
    global _limiter
    with _limiter_lock:
        _limiter = None
