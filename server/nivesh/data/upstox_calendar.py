"""NSE trading calendar — determines valid trading sessions.

Provides:
  - weekend detection
  - official NSE holiday list (maintained manually; update annually)
  - SessionStatus enum used by the fetcher to classify each date
  - trading_days(start, end) iterator — yields only expected trading dates
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum


class SessionStatus(str, Enum):
    """Classification of each calendar date."""

    TRADING = "trading"          # Expected normal session
    WEEKEND = "weekend"          # Saturday or Sunday
    EXCHANGE_HOLIDAY = "holiday" # Official NSE holiday
    SPECIAL_SESSION = "special"  # Non-standard (Muhurat etc.)
    DATA_MISSING = "data_missing"     # Expected data not received from API
    DATA_UNAVAILABLE = "data_unavailable"  # API returned empty / error
    FETCH_FAILED = "fetch_failed"     # Network/HTTP failure during collection


# Official NSE holidays for FY2025-26 and FY2026-27.
# Source: NSE India — Market Timings & Holidays.
# Update this list at the start of each financial year.
# Format: date(YYYY, M, D)
_NSE_HOLIDAYS: frozenset[date] = frozenset([
    # 2025
    date(2025, 1, 26),   # Republic Day
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr (Ramadan Eid)
    date(2025, 4, 10),   # Shri Ram Navami
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti
    date(2025, 10, 2),   # Dussehra
    date(2025, 10, 24),  # Diwali Laxmi Pujan (Muhurat trading day)
    date(2025, 11, 5),   # Prakash Gurpurb
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi
    date(2026, 4, 2),    # Ram Navami
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti / Baisakhi
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 11, 14),  # Diwali Laxmi Pujan
    date(2026, 12, 25),  # Christmas
])

# Muhurat trading dates — market is open briefly in evening; treat as special.
# These are NOT in _NSE_HOLIDAYS even though they are non-standard.
_MUHURAT_DATES: frozenset[date] = frozenset([
    date(2025, 10, 20),  # Diwali 2025 Muhurat
    date(2026, 11, 8),   # Diwali 2026 Muhurat (approximate; verify)
])


def classify(d: date) -> SessionStatus:
    """Return the trading status of a given calendar date."""
    if d.weekday() >= 5:
        return SessionStatus.WEEKEND
    if d in _NSE_HOLIDAYS:
        return SessionStatus.EXCHANGE_HOLIDAY
    if d in _MUHURAT_DATES:
        return SessionStatus.SPECIAL_SESSION
    return SessionStatus.TRADING


def is_trading_day(d: date) -> bool:
    """True if the exchange is expected to have a normal or special session."""
    status = classify(d)
    return status in (SessionStatus.TRADING, SessionStatus.SPECIAL_SESSION)


def trading_days(start: date, end: date) -> list[date]:
    """Return sorted list of expected trading dates in [start, end] inclusive."""
    days: list[date] = []
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            days.append(cur)
        cur += timedelta(days=1)
    return days


def calendar_days_back(n_calendar_days: int, from_date: date | None = None) -> tuple[date, date]:
    """Return (start, end) spanning the last n_calendar_days ending at from_date."""
    end = from_date or date.today()
    start = end - timedelta(days=n_calendar_days)
    return start, end
