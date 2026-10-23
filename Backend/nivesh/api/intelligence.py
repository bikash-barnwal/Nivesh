"""Read-only intelligence API with bounded, as-of-aware responses."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from nivesh.api.common import Envelope, history_snapshot, state_snapshot
from nivesh.appdb import add_watch, list_watch, remove_watch
from nivesh.audit import record
from nivesh.catalog import catalog_summary
from nivesh.config import settings
from nivesh.data.engine import get_engine
from nivesh.evidence import Evidence
from nivesh.llm import validate_readonly_sql

router = APIRouter(prefix="/api", tags=["intelligence"])


class SqlRequest(BaseModel):
    sql: str


def _snapshot(
    view: str,
    as_of: date | None,
    limit: int = 500,
    *,
    history: bool = False,
    extra_where: str = "",
    params: list[Any] | None = None,
) -> dict[str, Any]:
    engine = get_engine()
    if history:
        return history_snapshot(engine, view, as_of, limit, extra_where=extra_where, params=params)
    return state_snapshot(engine, view, as_of, limit, extra_where=extra_where, params=params)


@router.get("/catalog", response_model=Envelope)
def catalog() -> dict[str, Any]:
    summary = catalog_summary()
    return {
        "as_of": summary.get("data_through"),
        "data_through": summary.get("data_through"),
        "version": 1,
        "data": summary,
        "provenance": {"source": "catalog", "calculation": "catalog summary"},
    }


@router.post("/copilot/sql")
def copilot_sql(request: SqlRequest) -> dict[str, Any]:
    engine = get_engine()
    try:
        safe_sql = validate_readonly_sql(request.sql, set(engine.views()))
        bounded = f"SELECT * FROM ({safe_sql}) AS guarded LIMIT {settings.max_result_rows + 1}"
        result = engine.query(bounded, max_rows=settings.max_result_rows)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    evidence = Evidence("duckdb", engine.data_through(), "copilot-sql", "validated read-only SQL", 1.0)
    return {
        "as_of": engine.data_through(),
        "data_through": engine.data_through(),
        "version": 1,
        "data": result.records(),
        "provenance": {
            "source": "duckdb",
            "calculation": safe_sql,
            "evidence": evidence.as_dict(),
            "truncated": result.truncated,
        },
    }


@router.get("/brief", response_model=Envelope)
def brief(as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    from nivesh.api.market import events, overview

    return {**overview(as_of), "data": {"overview": overview(as_of)["data"], "events": events(as_of)["data"]}}


@router.get("/watchlist", response_model=Envelope)
def watchlist() -> dict[str, Any]:
    through = get_engine().data_through()
    return {
        "as_of": through,
        "data_through": through,
        "version": 1,
        "data": list_watch(),
        "provenance": {"source": "appdb.watchlist", "calculation": "local watchlist"},
    }


@router.post("/watchlist/{symbol}")
def watch_add(symbol: str) -> dict[str, Any]:
    result = add_watch(symbol)
    record("watchlist.add", {"symbol": symbol.upper()})
    return result


@router.delete("/watchlist/{symbol}")
def watch_delete(symbol: str) -> dict[str, str]:
    remove_watch(symbol)
    record("watchlist.remove", {"symbol": symbol.upper()})
    return {"status": "removed", "symbol": symbol.upper()}


@router.get("/scans", response_model=Envelope)
def scans(as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    return _snapshot("scan_results", as_of)


@router.get("/scans/{scan_id}", response_model=Envelope)
def scan(
    scan_id: str, as_of: Annotated[date | None, Query()] = None, top: Annotated[int, Query(ge=1, le=200)] = 20
) -> dict[str, Any]:
    return _snapshot("scan_results", as_of, top, extra_where="AND scan_id = ?", params=[scan_id])


@router.get("/stocks/search", response_model=Envelope)
def stock_search(q: str = "", limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, Any]:
    engine = get_engine()
    if "stock_features" not in engine.views():
        raise HTTPException(status_code=404, detail="stock_features is not built")
    latest = engine.data_through()
    result = engine.query(
        "SELECT symbol, date, close FROM stock_features WHERE date=? AND symbol ILIKE ? ORDER BY symbol LIMIT ?",
        [latest, f"%{q.upper()}%", limit],
    )
    return {
        "as_of": latest,
        "data_through": latest,
        "version": 1,
        "data": result.records(),
        "provenance": {"source": "stock_features", "calculation": "latest symbol snapshot"},
    }


@router.get("/stocks/{symbol}", response_model=Envelope)
def stock_panel(symbol: str, as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    return _snapshot("stock_features", as_of, 1, extra_where="AND symbol = ?", params=[symbol.upper()])


@router.get("/stocks/{symbol}/features", response_model=Envelope)
def stock_features(
    symbol: str, as_of: Annotated[date | None, Query()] = None, limit: Annotated[int, Query(ge=1, le=500)] = 1
) -> dict[str, Any]:
    return _snapshot(
        "stock_features", as_of, limit, history=True, extra_where="AND symbol = ?", params=[symbol.upper()]
    )


@router.get("/stocks/{symbol}/ohlcv", response_model=Envelope)
def stock_ohlcv(
    symbol: str, as_of: Annotated[date | None, Query()] = None, limit: Annotated[int, Query(ge=1, le=2000)] = 250
) -> dict[str, Any]:
    return _snapshot("stock_daily", as_of, limit, history=True, extra_where="AND symbol = ?", params=[symbol.upper()])


def _context_yaml(name: str) -> dict[str, Any]:
    import yaml

    return yaml.safe_load((settings.context_dir / name).read_text(encoding="utf-8")) or {}


@router.get("/scan-catalog", response_model=Envelope)
def scan_catalog() -> dict[str, Any]:
    engine = get_engine()
    cfg = _context_yaml("scans.yaml")
    scans = [{"id": s["id"], "label": s["label"], "state": s["state"], "when": s["when"]} for s in cfg["scans"]]
    return {
        "as_of": engine.data_through(),
        "data_through": engine.data_through(),
        "version": 1,
        "data": {"universe": cfg["universe"], "scans": scans},
        "provenance": {"source": "context/scans.yaml", "calculation": "configured scans"},
    }


@router.get("/catalog/engines", response_model=Envelope)
def engine_catalog() -> dict[str, Any]:
    engine = get_engine()
    cfg = _context_yaml("engines.yaml")
    return {
        "as_of": engine.data_through(),
        "data_through": engine.data_through(),
        "version": 1,
        "data": {
            "window_sessions": cfg.get("window_sessions"),
            "minimum_sessions": cfg.get("minimum_sessions"),
            "engines": cfg.get("engines", {}),
            "label_bands": cfg.get("label_bands", {}),
        },
        "provenance": {"source": "context/engines.yaml", "calculation": "engine definitions"},
    }
