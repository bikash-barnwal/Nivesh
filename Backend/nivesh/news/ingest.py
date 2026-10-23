"""NSE corporate announcements into an immutable raw store, then a normalised, de-duplicated table.

Layout: data/raw/news/nse_announcements/<YYYY>/<YYYYMMDD>.json. Past days are never refetched; today is refreshed
because announcements keep arriving. NSE needs a session cookie from the home page and answers 403 when pushed, so
requests are throttled and back off for a long time on 403/429 (same policy as the bhavcopy downloader).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from nivesh.config import settings
from nivesh.data.nse import RATE_LIMIT_BACKOFF_S, RATE_LIMIT_STATUSES, RETRY_STATUSES, Throttle

HOME = "https://www.nseindia.com/"
API = "https://www.nseindia.com/api/corporate-announcements"
SOURCE = "nse_announcements"
REPORT_ENDPOINTS = {
    "nse_bulk_deals": "https://www.nseindia.com/api/historical/bulk-deals",
    "nse_block_deals": "https://www.nseindia.com/api/historical/block-deals",
    "nse_pit": "https://www.nseindia.com/api/corporate-filings-pit-annual",
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}
ITEM_COLUMNS = {
    "source_id": pl.Utf8,
    "symbol": pl.Utf8,
    "published_at": pl.Datetime,
    "category": pl.Utf8,
    "text": pl.Utf8,
    "url": pl.Utf8,
    "content_hash": pl.Utf8,
}
_STAMPS = ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d-%b-%Y")


def report_raw_path(source: str, day: date, raw_dir: Path | None = None) -> Path:
    base = raw_dir or settings.raw_dir
    return base / "news" / source / str(day.year) / f"{day:%Y%m%d}.json"


def _report_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "bulk_deals", "block_deals", "BULK_DEALS_DATA", "BLOCK_DEALS_DATA", "pit_data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


def parse_market_report(payload: Any, source: str, day: date) -> list[dict[str, Any]]:
    """Normalize deal/PIT rows into news_items so the existing extractor can label them."""
    rows = []
    for index, row in enumerate(_report_rows(payload)):
        symbol = _clean(row.get("symbol") or row.get("Symbol"))
        if not symbol:
            continue
        side = _clean(row.get("buySell") or row.get("BUY_SELL") or row.get("transactionType"))
        client = _clean(row.get("clientName") or row.get("client_name") or row.get("name"))
        quantity = _clean(row.get("qty") or row.get("quantity") or row.get("shares"))
        price = _clean(row.get("watp") or row.get("price") or row.get("tradePrice"))
        label = "PIT disclosure" if source == "nse_pit" else source.removeprefix("nse_").replace("_", " ")
        text = " | ".join(part for part in (label, side, client, quantity, price) if part)
        digest = hashlib.sha256(f"{source}|{symbol}|{day}|{text}".encode()).hexdigest()[:16]
        rows.append(
            {
                "source_id": f"{source}_{row.get('id') or index}_{day:%Y%m%d}",
                "symbol": symbol.upper(),
                "published_at": datetime.combine(day, datetime.min.time()),
                "category": label,
                "text": text,
                "url": REPORT_ENDPOINTS.get(source),
                "content_hash": digest,
            }
        )
    return rows


def fetch_report_day(
    client: httpx.Client,
    source: str,
    day: date,
    *,
    raw_dir: Path | None = None,
    throttle: Throttle | None = None,
    backoff_s: float = 2.0,
) -> str:
    """Fetch one bulk/block/PIT report without storing HTML or overwriting historical days."""
    if source not in REPORT_ENDPOINTS:
        return f"error: unknown source {source}"
    dest = report_raw_path(source, day, raw_dir)
    if dest.exists():
        return "cached"
    if throttle:
        throttle.wait()
    try:
        params = {"from_date": f"{day:%d-%m-%Y}", "to_date": f"{day:%d-%m-%Y}"}
        response = client.get(REPORT_ENDPOINTS[source], params=params)
    except httpx.HTTPError as exc:
        return f"error: {type(exc).__name__}: {exc}"
    if response.status_code != 200:
        return f"error: HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return "error: not JSON"
    if not _report_rows(payload):
        return "empty"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload), encoding="utf-8")
    return "downloaded"


def download_reports(
    start: date,
    end: date,
    *,
    sources: tuple[str, ...] = tuple(REPORT_ENDPOINTS),
    raw_dir: Path | None = None,
    transport: httpx.BaseTransport | None = None,
    rate: float = 1.0,
) -> dict[tuple[str, date], str]:
    throttle = Throttle(rate)
    results = {}
    with httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True, transport=transport) as client:
        for source in sources:
            day = start
            while day <= end:
                results[(source, day)] = fetch_report_day(client, source, day, raw_dir=raw_dir, throttle=throttle)
                day += timedelta(days=1)
    return results


def raw_path(day: date, raw_dir: Path | None = None) -> Path:
    base = raw_dir or settings.raw_dir
    return base / "news" / SOURCE / str(day.year) / f"{day:%Y%m%d}.json"


def _stamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    for fmt in _STAMPS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def parse_announcements(payload: Any) -> list[dict[str, Any]]:
    """Normalise one day's NSE JSON. Rows without a symbol, a timestamp or any text are dropped."""
    rows = payload.get("data", []) if isinstance(payload, dict) else payload
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = _clean(row.get("symbol")).upper()
        published = _stamp(row.get("an_dt") or row.get("sort_date"))
        category = _clean(row.get("desc"))
        text = _clean(row.get("attchmntText") or row.get("desc"))
        if not symbol or published is None or not text:
            continue
        digest = hashlib.sha256(f"{symbol}|{category}|{text}".encode()).hexdigest()[:16]
        source_id = _clean(row.get("seq_id")) or digest
        out.append(
            {
                "source_id": f"nse_ann_{source_id}",
                "symbol": symbol,
                "published_at": published,
                "category": category,
                "text": text,
                "url": _clean(row.get("attchmntFile")) or None,
                "content_hash": digest,
            }
        )
    return out


def fetch_day(
    client: httpx.Client,
    day: date,
    *,
    today: date | None = None,
    raw_dir: Path | None = None,
    throttle: Throttle | None = None,
    backoff_s: float = 2.0,
) -> str:
    """'cached' | 'downloaded' | 'error: <why>'."""
    today = today or date.today()
    dest = raw_path(day, raw_dir)
    if dest.exists() and day < today:
        return "cached"
    params = {"index": "equities", "from_date": f"{day:%d-%m-%Y}", "to_date": f"{day:%d-%m-%Y}"}
    detail = ""
    for attempt in range(3):
        if throttle:
            throttle.wait()
        try:
            resp = client.get(API, params=params)
        except httpx.HTTPError as exc:
            detail = f"{type(exc).__name__}: {exc}"
            time.sleep(backoff_s * 2**attempt)
            continue
        if resp.status_code in RETRY_STATUSES:
            detail = f"HTTP {resp.status_code}"
            limited = resp.status_code in RATE_LIMIT_STATUSES
            time.sleep((RATE_LIMIT_BACKOFF_S if limited else backoff_s) * 2**attempt)
            continue
        if resp.status_code != 200:
            return f"error: HTTP {resp.status_code}"
        try:
            payload = resp.json()
        except ValueError:
            return "error: not JSON"  # an HTML block page; never store it
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(dest)
        return "downloaded"
    return f"error: {detail or 'retries exhausted'}"


def download_days(
    start: date,
    end: date,
    *,
    raw_dir: Path | None = None,
    transport: httpx.BaseTransport | None = None,
    today: date | None = None,
    rate: float = 1.0,
    backoff_s: float = 2.0,
) -> dict[date, str]:
    """Fetch every calendar day (announcements also arrive on weekends) from `start` to `end`."""
    throttle = Throttle(rate)
    results: dict[date, str] = {}
    with httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True, transport=transport) as client:
        try:
            client.get(HOME)  # sets the cookies the API insists on
        except httpx.HTTPError:
            pass
        day = start
        while day <= end:
            results[day] = fetch_day(client, day, today=today, raw_dir=raw_dir, throttle=throttle, backoff_s=backoff_s)
            day += timedelta(days=1)
    return results


def build_items(*, raw_dir: Path | None = None, parquet_dir: Path | None = None) -> int:
    """Normalise every raw day into `news_items`, de-duplicated by source id and by (symbol, content hash)."""
    base = (raw_dir or settings.raw_dir) / "news" / SOURCE
    parquet_dir = parquet_dir or settings.parquet_dir
    rows: list[dict[str, Any]] = []
    for f in sorted(base.glob("*/*.json")):
        try:
            rows.extend(parse_announcements(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    if not rows:
        return 0
    df = (
        pl.DataFrame(rows, schema=ITEM_COLUMNS)
        .sort("published_at")
        .unique(["source_id"], keep="first", maintain_order=True)
        .unique(["symbol", "content_hash"], keep="first", maintain_order=True)
        .sort("published_at")
    )
    out = parquet_dir / "news_items" / "news_items.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out, compression="zstd")
    return df.height
