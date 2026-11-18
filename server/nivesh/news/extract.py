"""Turn announcements into structured events with a model, under a strict schema and numeric grounding.

The model only reads text and fills the schema. The symbol and timestamps come from the announcement, direction is fixed
by the event type, and any amount the model reports must appear in the announcement text or it is dropped. Items the
model could not be reached for stay unprocessed and are retried; items it answered badly are recorded as invalid.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import ValidationError

from nivesh.config import settings
from nivesh.copilot.client import LLM, LLMError, Message, parse_json
from nivesh.news.attachments import AttachmentUnavailable, is_boilerplate
from nivesh.news.schema import DIRECTION, ExtractedEvent

MARKET_CLOSE = time(15, 30)
MAX_TEXT_CHARS = 6500
MAX_CONSECUTIVE_FAILURES = 3

# Routine compliance filings that never carry a tradable event; skipped without a model call.
SKIP_CATEGORIES = (
    "trading window",
    "loss of share certificate",
    "duplicate share certificate",
    "newspaper",
    "analysts/institutional investor meet",
    "schedule of analyst",
    "investor presentation",
    "certificate under sebi",
    "compliance certificate",
    "intimation of closure",
    "copy of",
    "record date",
    "book closure",
    "voting results",
    "postal ballot",
    "agm",
    "annual general meeting",
    "statement of investor complaints",
    "reconciliation of share capital",
    "secretarial compliance",
    "structural digital database",
    "esop/esos/esps",
    "shareholders meeting",
)

EVENT_HELP = {
    "results_beat": "quarterly or annual results clearly ahead of the prior period or stated expectations",
    "results_miss": "results clearly behind the prior period or stated expectations (loss, sharp profit fall)",
    "order_win": "company received or won an order, contract or letter of award",
    "capex_announcement": "approved or announced a capacity expansion or capital expenditure plan",
    "promoter_buy": "promoter or promoter group bought shares or increased holding",
    "promoter_sell": "promoter or promoter group sold shares or reduced holding",
    "pledge_increase": "promoter shares newly pledged or pledge increased",
    "pledge_release": "promoter pledge released or reduced",
    "rating_upgrade": "credit rating or outlook upgraded",
    "rating_downgrade": "credit rating or outlook downgraded",
    "management_change": "resignation, appointment or change of CEO, CFO, MD, chairman or auditor",
    "regulatory_action": "penalty, show-cause notice, investigation, ban or other regulator action",
    "block_deal_buy": "a block or bulk deal where a buyer acquired a large stake",
    "block_deal_sell": "a block or bulk deal where a seller disposed of a large stake",
}

SYSTEM_PROMPT = (
    "You extract structured stock events from one Indian stock-exchange announcement. "
    "Report only an event the text explicitly states. Do not infer, speculate or use outside knowledge. "
    "If the announcement contains no event of the listed types, return an empty list. "
    "Report an amount only if it is written in the text; otherwise leave it null. value_cr is in Rs crore. "
    "confidence is how explicit the text is, from 0 to 1.\n\n"
    "Event types:\n" + "\n".join(f"- {k}: {v}" for k, v in EVENT_HELP.items()) + "\n\n"
    'Reply with JSON only: {"events": [{"event_type": "...", "confidence": 0.0, "summary": "<=200 chars", '
    '"magnitude": {"value_cr": null, "pct_of_revenue": null}}]}'
)

EVENT_COLUMNS = {
    "source_id": pl.Utf8,
    "symbol": pl.Utf8,
    "event_type": pl.Utf8,
    "direction": pl.Utf8,
    "event_date": pl.Date,
    "published_at": pl.Datetime,
    "available_from": pl.Date,
    "confidence": pl.Float64,
    "summary": pl.Utf8,
    "value_cr": pl.Float64,
    "pct_of_revenue": pl.Float64,
    "magnitude_grounded": pl.Boolean,
    "url": pl.Utf8,
    "model": pl.Utf8,
}
STATE_COLUMNS = {
    "source_id": pl.Utf8,
    "status": pl.Utf8,  # ok | none | invalid | skipped
    "reason": pl.Utf8,
    "model": pl.Utf8,
    "extracted_at": pl.Datetime,
}


def available_from(published: datetime) -> date:
    """First session whose close can contain the news: same day if published before the close, else the next weekday.

    Signals are taken at a session's close and filled at the next open, so using this date cannot look ahead.
    """
    d = published.date()
    if published.time() >= MARKET_CLOSE:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def is_routine(category: str) -> bool:
    c = category.lower()
    return any(k in c for k in SKIP_CATEGORIES)


def number_in_text(value: float, text: str) -> bool:
    """True if `value` appears among the numbers written in `text` (commas ignored, 0.5% tolerance)."""
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            found = float(raw.replace(",", ""))
        except ValueError:
            continue
        if found == value or (found > 0 and abs(found - value) / found <= 0.005):
            return True
    return False


def messages_for(item: dict[str, Any]) -> list[Message]:
    body = f"Company: {item['symbol']}\nCategory: {item['category']}\nText: {item['text'][:MAX_TEXT_CHARS]}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": body}]


def interpret(item: dict[str, Any], data: dict[str, Any] | None, model: str) -> tuple[list[dict[str, Any]], str, str]:
    """Validate one model reply into (events, status, reason). Never raises on bad model output."""
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return [], "invalid", "reply has no events list"
    events: list[dict[str, Any]] = []
    reasons: list[str] = []
    seen: set[str] = set()
    for raw in data["events"]:
        try:
            ev = ExtractedEvent.model_validate(raw)
        except ValidationError as exc:
            reasons.append(exc.errors()[0]["msg"][:80])
            continue
        if ev.event_type in seen:
            continue
        seen.add(ev.event_type)
        value, pct = ev.magnitude.value_cr, ev.magnitude.pct_of_revenue
        grounded = True
        if value is not None and not number_in_text(value, item["text"]):
            value, grounded = None, False
        if pct is not None and not number_in_text(pct, item["text"]):
            pct, grounded = None, False
        published: datetime = item["published_at"]
        events.append(
            {
                "source_id": item["source_id"],
                "symbol": item["symbol"],
                "event_type": ev.event_type,
                "direction": DIRECTION[ev.event_type],
                "event_date": published.date(),
                "published_at": published,
                "available_from": available_from(published),
                "confidence": ev.confidence,
                "summary": ev.summary,
                "value_cr": value,
                "pct_of_revenue": pct,
                "magnitude_grounded": grounded,
                "url": item.get("url"),
                "model": model,
            }
        )
    if events:
        return events, "ok", ""
    if reasons:
        return [], "invalid", "; ".join(reasons[:3])
    return [], "none", ""


def _read(path: Path, schema: dict[str, Any]) -> pl.DataFrame:
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=schema)


def _write(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    df.write_parquet(tmp, compression="zstd")
    tmp.replace(path)


def run_extraction(
    llm: LLM,
    *,
    limit: int | None = None,
    universe: set[str] | None = None,
    retry_invalid: bool = False,
    fetch_text: Callable[[dict[str, Any]], str] | None = None,
    parquet_dir: Path | None = None,
) -> dict[str, Any]:
    """Extract events for announcements not yet processed, newest first. Idempotent and resumable.

    Headline-only announcements (NSE boilerplate) need their attachment: `fetch_text(item)` returns its text, "" when
    there is none (the item is recorded as skipped) or raises `AttachmentUnavailable` (left for a later run). Without
    a `fetch_text`, headline-only items are skipped because the model would have nothing to read.
    """
    parquet_dir = parquet_dir or settings.parquet_dir
    items_path = parquet_dir / "news_items" / "news_items.parquet"
    if not items_path.exists():
        return {
            "processed": 0,
            "events": 0,
            "skipped": 0,
            "invalid": 0,
            "failed": 0,
            "deferred": 0,
            "remaining": 0,
            "aborted": False,
        }
    state_path = parquet_dir / "news_extracted" / "news_extracted.parquet"
    events_path = parquet_dir / "stock_events" / "stock_events.parquet"
    state = _read(state_path, STATE_COLUMNS)
    events = _read(events_path, EVENT_COLUMNS)

    done = state if not retry_invalid else state.filter(pl.col("status") != "invalid")
    items = pl.read_parquet(items_path).join(done.select("source_id"), on="source_id", how="anti")
    if universe is not None:
        items = items.filter(pl.col("symbol").is_in(sorted(universe)))
    items = items.sort("published_at", descending=True)
    pending = items.to_dicts()

    new_state: list[dict[str, Any]] = []
    new_events: list[dict[str, Any]] = []
    counts = {"processed": 0, "events": 0, "skipped": 0, "invalid": 0, "failed": 0, "deferred": 0}
    consecutive, aborted, model_calls = 0, False, 0
    now = datetime.now(UTC).replace(tzinfo=None)
    for item in pending:
        if is_routine(item["category"]):
            new_state.append(
                {
                    "source_id": item["source_id"],
                    "status": "skipped",
                    "reason": "routine filing",
                    "model": None,
                    "extracted_at": now,
                }
            )
            counts["skipped"] += 1
            continue
        if limit is not None and model_calls >= limit:
            break
        if is_boilerplate(item["text"]):
            try:
                attachment = fetch_text(item) if fetch_text and item.get("url") else ""
            except AttachmentUnavailable:
                counts["deferred"] += 1
                continue  # left unprocessed; retried on a later run
            if not attachment:
                new_state.append(
                    {
                        "source_id": item["source_id"],
                        "status": "skipped",
                        "reason": "no text",
                        "model": None,
                        "extracted_at": now,
                    }
                )
                counts["skipped"] += 1
                continue
            item = {**item, "text": f"{item['text']} Attachment: {attachment}"}
        model_calls += 1
        try:
            result = llm.chat("extract", messages_for(item), json_mode=True)
            data = result.data
            if data is None:
                try:
                    data = parse_json(result.text)
                except ValueError:
                    data = None
            model = result.model_id if hasattr(result, "model_id") else result.model
        except LLMError:
            counts["failed"] += 1
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                aborted = True
                break
            continue  # leave unprocessed so the next run retries it
        consecutive = 0
        found, status, reason = interpret(item, data, model)
        new_events.extend(found)
        new_state.append(
            {"source_id": item["source_id"], "status": status, "reason": reason, "model": model, "extracted_at": now}
        )
        counts["processed"] += 1
        counts["events"] += len(found)
        counts["invalid"] += status == "invalid"

    if new_state:
        replaced = {s["source_id"] for s in new_state}
        merged_state = pl.concat(
            [state.filter(~pl.col("source_id").is_in(sorted(replaced))), pl.DataFrame(new_state, schema=STATE_COLUMNS)]
        )
        _write(merged_state, state_path)
        kept = events.filter(~pl.col("source_id").is_in(sorted(replaced)))
        merged_events = pl.concat([kept, pl.DataFrame(new_events, schema=EVENT_COLUMNS)]) if new_events else kept
        _write(merged_events.sort(["available_from", "symbol"]), events_path)
    handled = {s["source_id"] for s in new_state}
    remaining = items.filter(~pl.col("source_id").is_in(sorted(handled))).height
    return {**counts, "remaining": remaining, "aborted": aborted}
