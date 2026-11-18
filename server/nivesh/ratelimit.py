"""In-process sliding-window limits per user and action.

The API runs as one process, so memory is enough; limits reset on restart, which only ever makes them looser.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def allow(user_id: str, action: str, limit: int, window_s: float) -> bool:
    """Record one hit and return True, or return False (nothing recorded) when the window is full."""
    now = time.monotonic()
    with _lock:
        hits = _hits[(user_id, action)]
        while hits and hits[0] <= now - window_s:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def reset() -> None:
    with _lock:
        _hits.clear()
