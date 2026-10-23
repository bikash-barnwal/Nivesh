import threading
import time
from datetime import date

import httpx

from nivesh.data import nse


def test_throttle_spaces_requests_across_threads():
    throttle = nse.Throttle(20)  # one request every 50 ms
    stamps: list[float] = []
    lock = threading.Lock()

    def worker():
        for _ in range(3):
            throttle.wait()
            with lock:
                stamps.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stamps.sort()
    assert len(stamps) == 9
    # 9 requests at 20/s need at least 8 intervals (0.4s); allow a little timer slack. Individual gaps are not
    # asserted: thread wake-up jitter on a busy machine can bunch two stamps even though slots are 50 ms apart.
    assert stamps[-1] - stamps[0] >= 0.36


def test_403_backs_off_and_is_not_cached_as_missing(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(nse.time, "sleep", lambda s: sleeps.append(s))
    transport = httpx.MockTransport(lambda request: httpx.Response(403))
    with httpx.Client(transport=transport) as client:
        result = nse.fetch_day(
            client, nse.DATASETS["bhav"], date(2026, 1, 5), today=date(2026, 10, 7), raw_dir=tmp_path
        )
    assert result.status == "error" and "403" in result.detail
    assert sleeps == [nse.RATE_LIMIT_BACKOFF_S * 2**i for i in range(3)]
    assert not any(tmp_path.rglob("*.missing")), "a throttled request must be retried next run, not remembered"


def test_server_error_uses_short_backoff(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(nse.time, "sleep", lambda s: sleeps.append(s))
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with httpx.Client(transport=transport) as client:
        nse.fetch_day(
            client, nse.DATASETS["bhav"], date(2026, 1, 5), today=date(2026, 10, 7), raw_dir=tmp_path, backoff_s=2.0
        )
    assert sleeps == [2.0, 4.0, 8.0]
