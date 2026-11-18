"""Text of NSE announcement attachments.

For many categories (orders, credit ratings, results) NSE's own text is a boilerplate headline such as "X has informed
the Exchange about Credit Rating"; the content is in the attached PDF. This module fetches the PDF from NSE's archive
host, extracts the first pages with pypdf and caches the text, so the model reads what the filing actually says.

Only https URLs on nseindia.com are fetched, downloads are size-capped, and a file that is not a readable PDF is
cached as empty so it is never fetched again. Transient failures (network, 403/429/5xx) raise
`AttachmentUnavailable` and cache nothing, so the item is retried on a later run.
"""

from __future__ import annotations

import hashlib
import io
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from nivesh.config import settings
from nivesh.data.nse import RATE_LIMIT_BACKOFF_S, RATE_LIMIT_STATUSES, Throttle
from nivesh.news.ingest import HEADERS

MAX_BYTES = 8_000_000
MAX_PAGES = 4
MAX_CHARS = 6000
BOILERPLATE_MAX_CHARS = 220
_BOILERPLATE = re.compile(r"informed the exchange (?:about|regarding)\s+[^.]{0,100}$", re.IGNORECASE)


class AttachmentUnavailable(RuntimeError):
    """The attachment could not be fetched right now; try again later."""


def is_boilerplate(text: str) -> bool:
    """True when the announcement text is only a headline, so the attachment carries the content."""
    t = " ".join(text.split())
    return len(t) <= BOILERPLATE_MAX_CHARS and bool(_BOILERPLATE.search(t))


def allowed_url(url: str) -> bool:
    parts = urlparse(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host == "nseindia.com" or host.endswith(".nseindia.com"))


def cache_path(url: str, raw_dir: Path | None = None) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:24]
    return (raw_dir or settings.raw_dir) / "news" / "nse_attachments" / f"{digest}.txt"


def pdf_text(data: bytes) -> str:
    """First pages of a PDF as one cleaned string; empty if it is not a readable text PDF."""
    if not data.startswith(b"%PDF"):
        return ""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(reader.pages[i].extract_text() or "") for i in range(min(MAX_PAGES, len(reader.pages)))]
    except Exception:  # noqa: BLE001 - pypdf raises many types on damaged or encrypted files
        return ""
    return " ".join(" ".join(pages).split())[:MAX_CHARS]


class AttachmentFetcher:
    """Callable `item -> text`; empty string means "no readable text", an exception means "retry later"."""

    def __init__(
        self,
        *,
        raw_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        rate: float = 1.0,
        backoff_s: float = 2.0,
    ) -> None:
        self.raw_dir = raw_dir
        self.throttle = Throttle(rate)
        self.backoff_s = backoff_s
        self._client = httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> AttachmentFetcher:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __call__(self, item: dict[str, Any]) -> str:
        url = item.get("url") or ""
        if not url or not allowed_url(url):
            return ""
        path = cache_path(url, self.raw_dir)
        if path.exists():
            return path.read_text(encoding="utf-8")
        text = pdf_text(self._download(url))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return text

    def _download(self, url: str) -> bytes:
        detail = ""
        for attempt in range(2):
            self.throttle.wait()
            try:
                with self._client.stream("GET", url) as resp:
                    if resp.status_code in (404, 410):
                        return b""
                    if resp.status_code != 200:
                        detail = f"HTTP {resp.status_code}"
                        limited = resp.status_code in RATE_LIMIT_STATUSES
                        time.sleep((RATE_LIMIT_BACKOFF_S if limited else self.backoff_s) * 2**attempt)
                        continue
                    declared = int(resp.headers.get("content-length") or 0)
                    if declared > MAX_BYTES:
                        return b""
                    body = bytearray()
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            return b""
                    return bytes(body)
            except httpx.HTTPError as exc:
                detail = f"{type(exc).__name__}: {exc}"
                time.sleep(self.backoff_s * 2**attempt)
        raise AttachmentUnavailable(detail or "retries exhausted")
