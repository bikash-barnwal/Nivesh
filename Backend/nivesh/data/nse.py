"""Download daily NSE archive files into an immutable raw store.

Layout: data/raw/<dataset>/<YYYY>/<YYYYMMDD>.<csv|zip|dat>
A `.missing` marker is written for dates that 404 long enough after the fact (holidays),
so reruns never hit NSE for them again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import httpx

from nivesh.config import settings

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/octet-stream,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# A 404 for a date older than this is treated as a non-trading day and remembered.
MISSING_GRACE_DAYS = 3
RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
# 403/429 from NSE means "slow down": back off for much longer than for a transient server error.
RATE_LIMIT_STATUSES = {403, 429}
RATE_LIMIT_BACKOFF_S = 30.0
DEFAULT_RATE_PER_S = 2.5


class Throttle:
    """Caps the combined request rate of all worker threads."""

    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            time.sleep(start - now)


@dataclass(frozen=True)
class Dataset:
    name: str
    url: str
    signature: str  # lower-cased header prefix for CSVs ("" = any non-HTML text); ignored for zips
    ext: str = ".csv"
    # Snapshot datasets have no dated URL: they can only be captured for today, and the
    # dated raw copies build up their history over time.
    snapshot: bool = False
    # Inclusive availability window; no requests are made for dates outside it.
    since: date | None = None
    until: date | None = None

    def covers(self, d: date) -> bool:
        return (self.since is None or d >= self.since) and (self.until is None or d <= self.until)


# NSE switched to sec_bhavdata_full (with delivery) in 2019; older sessions use the legacy zip + MTO file.
NEW_BHAV_SINCE = date(2019, 8, 1)
OLD_BHAV_UNTIL = date(2019, 9, 30)


_ARCHIVE = "https://nsearchives.nseindia.com/content"

SNAPSHOTS: dict[str, Dataset] = {
    "ref_equity_list": Dataset("ref_equity_list", f"{_ARCHIVE}/equities/EQUITY_L.csv", "symbol", snapshot=True),
    "ref_symbol_change": Dataset("ref_symbol_change", f"{_ARCHIVE}/equities/symbolchange.csv", "", snapshot=True),
    "ref_fno_lots": Dataset("ref_fno_lots", f"{_ARCHIVE}/fo/fo_mktlots.csv", "underlying", snapshot=True),
    **{
        f"ref_idx_{key}": Dataset(f"ref_idx_{key}", f"{_ARCHIVE}/indices/{file}", "company name", snapshot=True)
        for key, file in {
            "nifty50": "ind_nifty50list.csv",
            "nifty500": "ind_nifty500list.csv",
            "midsmallcap400": "ind_niftymidsmallcap400list.csv",
            "totalmarket": "ind_niftytotalmarket_list.csv",
            "microcap250": "ind_niftymicrocap250_list.csv",
        }.items()
    },
}

DATASETS: dict[str, Dataset] = {
    "bhav": Dataset(
        "bhav",
        "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv",
        "symbol",
        since=NEW_BHAV_SINCE,
    ),
    # Legacy bhavcopy (no delivery); delivery for these dates comes from the MTO file below.
    "bhav_old": Dataset(
        "bhav_old",
        "https://nsearchives.nseindia.com/content/historical/EQUITIES/{d:%Y}/{MON}/cm{d:%d}{MON}{d:%Y}bhav.csv.zip",
        "",
        ".zip",
        until=OLD_BHAV_UNTIL,
    ),
    "mto": Dataset(
        "mto",
        "https://nsearchives.nseindia.com/archives/equities/mto/MTO_{d:%d%m%Y}.DAT",
        "security wise delivery",
        ".dat",
        until=OLD_BHAV_UNTIL,
    ),
    "index": Dataset(
        "index",
        "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv",
        "index name",
        since=date(2013, 1, 1),
    ),
    # PR zip: bc (corporate actions), bh (price-band hits), mcap, hl (52w high/low), an/bm (announcements)
    "pr": Dataset(
        "pr",
        "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{d:%d%m%y}.zip",
        "",
        ".zip",
    ),
    # Daily price-band list; archive goes back to ~2022.
    "bands": Dataset(
        "bands",
        "https://nsearchives.nseindia.com/content/equities/sec_list_{d:%d%m%Y}.csv",
        "symbol",
        since=date(2022, 1, 1),
    ),
    **SNAPSHOTS,
}

DAILY_DEFAULT = ("bhav", "bhav_old", "mto", "index", "pr", "bands")
ALL_DEFAULT = (*DAILY_DEFAULT, *SNAPSHOTS)

Status = Literal["cached", "downloaded", "missing", "skipped", "error"]


@dataclass
class FetchResult:
    dataset: str
    day: date
    status: Status
    detail: str = ""


def weekdays(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def raw_path(dataset: str, d: date, raw_dir: Path | None = None) -> Path:
    base = raw_dir or settings.raw_dir
    ext = DATASETS[dataset].ext if dataset in DATASETS else ".csv"
    return base / dataset / f"{d:%Y}" / f"{d:%Y%m%d}{ext}"


def _looks_valid(content: bytes, ds: Dataset) -> bool:
    if ds.ext == ".zip":
        return content[:2] == b"PK"
    head = content[:256].decode("utf-8", errors="ignore").lstrip("﻿").strip().lower()
    if not head or head.startswith("<"):
        return False
    return head.startswith(ds.signature)


def fetch_day(
    client: httpx.Client,
    ds: Dataset,
    d: date,
    *,
    today: date | None = None,
    raw_dir: Path | None = None,
    backoff_s: float = 2.0,
    throttle: Throttle | None = None,
) -> FetchResult:
    today = today or date.today()
    dest = raw_path(ds.name, d, raw_dir)
    marker = dest.with_suffix(".missing")
    if dest.exists():
        return FetchResult(ds.name, d, "cached")
    if marker.exists():
        return FetchResult(ds.name, d, "skipped")

    url = ds.url.format(d=d, MON=d.strftime("%b").upper())
    detail = ""
    for attempt in range(3):
        if throttle:
            throttle.wait()
        try:
            resp = client.get(url)
        except httpx.HTTPError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            time.sleep(backoff_s * 2**attempt)
            continue

        if resp.status_code == 404:
            if (today - d).days > MISSING_GRACE_DAYS:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
            return FetchResult(ds.name, d, "missing")
        if resp.status_code in RETRY_STATUSES:
            detail = f"HTTP {resp.status_code}"
            limited = resp.status_code in RATE_LIMIT_STATUSES
            time.sleep((RATE_LIMIT_BACKOFF_S if limited else backoff_s) * 2**attempt)
            continue
        if resp.status_code != 200:
            return FetchResult(ds.name, d, "error", f"HTTP {resp.status_code}")
        if not _looks_valid(resp.content, ds):
            # NSE sometimes answers 200 with an HTML page; never store it.
            return FetchResult(ds.name, d, "error", "unexpected content")

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)
        return FetchResult(ds.name, d, "downloaded")

    return FetchResult(ds.name, d, "error", detail or "retries exhausted")


def download_range(
    start: date,
    end: date,
    datasets: Iterable[str] = DAILY_DEFAULT,
    *,
    workers: int = 4,
    raw_dir: Path | None = None,
    on_result: Callable[[FetchResult], None] | None = None,
    transport: httpx.BaseTransport | None = None,
    today: date | None = None,
    rate: float | None = DEFAULT_RATE_PER_S,
) -> list[FetchResult]:
    today = today or date.today()
    jobs: list[tuple[Dataset, date]] = []
    for name in datasets:
        ds = DATASETS[name]
        if ds.snapshot:
            if end >= today:  # snapshots can only be captured as of today
                jobs.append((ds, today))
        else:
            jobs.extend((ds, d) for d in weekdays(start, end) if ds.covers(d))
    throttle = Throttle(rate) if rate else None
    results: list[FetchResult] = []
    with httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True, transport=transport) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(fetch_day, client, ds, d, raw_dir=raw_dir, today=today, throttle=throttle) for ds, d in jobs
            ]
            for fut in as_completed(futures):
                res = fut.result()
                results.append(res)
                if on_result:
                    on_result(res)
    results.sort(key=lambda r: (r.dataset, r.day))
    return results


def last_downloaded(dataset: str, raw_dir: Path | None = None) -> date | None:
    base = (raw_dir or settings.raw_dir) / dataset
    files = sorted(base.glob(f"*/*{DATASETS[dataset].ext}"))
    if not files:
        return None
    stem = files[-1].stem
    return date(int(stem[:4]), int(stem[4:6]), int(stem[6:8]))
