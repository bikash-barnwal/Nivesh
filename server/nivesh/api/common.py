"""Shared response envelope and as-of helpers for the read-only API."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from nivesh.data.engine import Engine


class Envelope(BaseModel):
    model_config = ConfigDict(extra="allow")
    as_of: date | None = None
    data_through: date | None = None
    version: int = 1
    data: Any
    provenance: dict[str, Any]


def envelope(
    data: Any, *, as_of: date | None, engine: Engine, source: str, calculation: str, **extra: Any
) -> dict[str, Any]:
    return {
        "as_of": as_of,
        "data_through": engine.data_through(),
        "version": 1,
        "data": data,
        "provenance": {"source": source, "calculation": calculation},
        **extra,
    }


def require_view(engine: Engine, view: str) -> None:
    if view not in engine.views():
        raise HTTPException(status_code=404, detail=f"analytics view '{view}' is not built")


def latest_date(engine: Engine, view: str, as_of: date | None, where: str = "", params: list[Any] | None = None):
    """Latest date in `view` that is on or before `as_of` (or the latest overall)."""
    clauses = [c for c in (where, "date <= ?" if as_of else "") if c]
    args = [*(params or []), *([as_of] if as_of else [])]
    sql = f'SELECT max(date) FROM "{view}"' + (f" WHERE {' AND '.join(clauses)}" if clauses else "")
    return engine.query(sql, args).rows[0][0]


def state_snapshot(
    engine: Engine,
    view: str,
    as_of: date | None,
    limit: int = 500,
    *,
    extra_where: str = "",
    params: list[Any] | None = None,
    order: str = "date DESC",
) -> dict[str, Any]:
    """Rows of the latest date on or before `as_of` (one state, not a history)."""
    require_view(engine, view)
    where = extra_where.removeprefix("AND ").strip()
    state = latest_date(engine, view, as_of, where, params)
    rows: list[dict[str, Any]] = []
    if state is not None:
        clauses = ["date = ?", *([where] if where else [])]
        sql = f'SELECT * FROM "{view}" WHERE {" AND ".join(clauses)} ORDER BY {order} LIMIT ?'
        rows = engine.query(sql, [state, *(params or []), limit]).records()
    return envelope(rows, as_of=state, engine=engine, source=view, calculation="latest state on or before as_of")


def history_snapshot(
    engine: Engine,
    view: str,
    as_of: date | None,
    limit: int,
    *,
    extra_where: str = "",
    params: list[Any] | None = None,
) -> dict[str, Any]:
    require_view(engine, view)
    where = extra_where.removeprefix("AND ").strip()
    clauses = [c for c in (where, "date <= ?" if as_of else "") if c]
    args = [*(params or []), *([as_of] if as_of else []), limit]
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f'SELECT * FROM "{view}"{where_sql} ORDER BY date DESC LIMIT ?'
    rows = engine.query(sql, args).records()
    return envelope(
        rows,
        as_of=as_of or (rows[0]["date"] if rows else None),
        engine=engine,
        source=view,
        calculation="bounded history up to as_of",
    )
