"""News and filings events: structured, source-linked, and never visible before they were published."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from nivesh.api.common import Envelope, envelope, require_view
from nivesh.data.engine import get_engine

router = APIRouter(prefix="/api/news", tags=["news"])

EVENT_FIELDS = (
    "source_id, symbol, event_type, direction, event_date, published_at, available_from, confidence, summary, "
    "value_cr, pct_of_revenue, magnitude_grounded, url"
)


@router.get("/events", response_model=Envelope)
def events(
    symbol: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    as_of: Annotated[date | None, Query(description="Only events usable on or before this session")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    engine = get_engine()
    require_view(engine, "stock_events")
    where, params = ["1 = 1"], []
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if event_type:
        where.append("event_type = ?")
        params.append(event_type)
    if as_of:
        where.append("available_from <= ?")
        params.append(as_of)
    rows = engine.query(
        f"SELECT {EVENT_FIELDS} FROM stock_events WHERE {' AND '.join(where)} ORDER BY published_at DESC LIMIT ?",
        [*params, limit],
    ).records()
    return envelope(
        rows,
        as_of=as_of,
        engine=engine,
        source="stock_events",
        calculation="events a model extracted from NSE announcements; amounts appear only if written in the filing",
    )


@router.get("/study", response_model=Envelope)
def study() -> dict[str, Any]:
    engine = get_engine()
    require_view(engine, "stock_event_study")
    rows = engine.query("SELECT * FROM stock_event_study ORDER BY event_type").records()
    return envelope(
        rows,
        as_of=None,
        engine=engine,
        source="stock_event_study",
        calculation=(
            "excess return vs the equal-weight liquid market, entering at the open after the first session that "
            "could contain the news; t_stat is mean / standard error"
        ),
    )


@router.get("/status", response_model=Envelope)
def status() -> dict[str, Any]:
    engine = get_engine()
    views = set(engine.views())
    out: dict[str, Any] = {"items": 0, "extracted": {}, "events_by_type": {}}
    if "news_items" in views:
        out["items"] = engine.query("SELECT count(*) FROM news_items").rows[0][0]
    if "news_extracted" in views:
        out["extracted"] = {
            r["status"]: r["n"]
            for r in engine.query("SELECT status, count(*) AS n FROM news_extracted GROUP BY 1").records()
        }
    if "stock_events" in views:
        out["events_by_type"] = {
            r["event_type"]: r["n"]
            for r in engine.query(
                "SELECT event_type, count(*) AS n FROM stock_events GROUP BY 1 ORDER BY 2 DESC"
            ).records()
        }
    return envelope(out, as_of=None, engine=engine, source="news_*", calculation="counts")
