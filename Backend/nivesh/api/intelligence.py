"""Read-only intelligence API with bounded, as-of-aware responses."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from nivesh.appdb import add_alert, add_watch, list_alerts, list_watch, remove_watch
from nivesh.audit import record
from nivesh.catalog import catalog_summary
from nivesh.config import settings
from nivesh.data.engine import get_engine
from nivesh.evidence import Evidence
from nivesh.llm import validate_readonly_sql
from nivesh.strategy import backtest, load_strategy

router = APIRouter(prefix="/api", tags=["intelligence"])


class Envelope(BaseModel):
    model_config = ConfigDict(extra="allow")
    as_of: date | None = None
    data_through: date | None = None
    version: int = 1
    data: Any
    provenance: dict[str, Any]


class SqlRequest(BaseModel):
    sql: str


class AlertRequest(BaseModel):
    name: str
    scan_id: str | None = None
    min_score: float = 0


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
    if view not in engine.views():
        raise HTTPException(status_code=404, detail=f"analytics view '{view}' is not built")
    requested = as_of or engine.data_through()
    args = list(params or [])
    if history:
        where = "date <= ?" if requested else "TRUE"
        if requested:
            args.append(requested)
        sql = f'SELECT * FROM "{view}" WHERE {where} {extra_where} ORDER BY date DESC LIMIT ?'
        args.append(limit)
    else:
        if not requested:
            return {
                "as_of": None,
                "data_through": engine.data_through(),
                "version": 1,
                "data": [],
                "provenance": {"source": view, "calculation": "bounded latest snapshot"},
            }
        sql = f'SELECT * FROM "{view}" WHERE date = ? {extra_where} ORDER BY date DESC LIMIT ?'
        args = [requested, *args, limit]
    result = engine.query(sql, args)
    return {
        "as_of": requested,
        "data_through": engine.data_through(),
        "version": 1,
        "data": result.records(),
        "provenance": {"source": view, "calculation": "bounded DuckDB/Parquet snapshot"},
    }


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
        bounded = f"SELECT * FROM ({safe_sql}) AS guarded LIMIT {settings.max_result_rows}"
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
    regime = _snapshot("market_regime", as_of, 1)
    events = _snapshot("market_events", as_of, 20) if "market_events" in get_engine().views() else {"data": []}
    return {
        "as_of": regime["as_of"],
        "data_through": regime["data_through"],
        "version": 1,
        "data": {"regime": regime["data"], "events": events["data"]},
        "provenance": {"source": "market_regime,market_events", "calculation": "deterministic daily brief"},
    }


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


@router.get("/alerts", response_model=Envelope)
def alerts() -> dict[str, Any]:
    through = get_engine().data_through()
    return {
        "as_of": through,
        "data_through": through,
        "version": 1,
        "data": list_alerts(),
        "provenance": {"source": "appdb.alert_rules", "calculation": "local alerts"},
    }


@router.post("/alerts")
def alert_create(request: AlertRequest) -> dict[str, Any]:
    return add_alert(request.name, request.scan_id, request.min_score)


@router.get("/market/breadth", response_model=Envelope)
def breadth(as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    return _snapshot("market_breadth", as_of)


@router.get("/market/regime-history", response_model=Envelope)
def regime_history(
    as_of: Annotated[date | None, Query()] = None, sessions: Annotated[int, Query(ge=1, le=2000)] = 150
) -> dict[str, Any]:
    return _snapshot("market_regime", as_of, sessions, history=True)


@router.get("/market/events/log", response_model=Envelope)
def event_log(
    as_of: Annotated[date | None, Query()] = None, sessions: Annotated[int, Query(ge=1, le=2000)] = 30
) -> dict[str, Any]:
    return _snapshot("market_events", as_of, sessions, history=True)


@router.get("/market/engines", response_model=Envelope)
def engines(as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    return _snapshot("market_engines", as_of)


@router.get("/sectors", response_model=Envelope)
def sectors(as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    return _snapshot("sector_daily", as_of)


@router.get("/market/events", response_model=Envelope)
def events(as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    return _snapshot("market_events", as_of)


@router.get("/market/analogues", response_model=Envelope)
def analogues(as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    return _snapshot("market_analogues", as_of)


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


@router.get("/strategies", response_model=Envelope)
def strategies() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2] / "config" / "strategies"
    data = []
    for path in sorted(root.glob("*.yaml")):
        strategy = load_strategy(str(path))
        data.append(
            {
                "name": strategy.name,
                "version": strategy.version,
                "entry": strategy.entry,
                "exit": strategy.exit,
                "position_size": strategy.position_size,
            }
        )
    through = get_engine().data_through()
    return {
        "as_of": through,
        "data_through": through,
        "version": 1,
        "data": data,
        "provenance": {"source": "strategy config", "calculation": "configured strategies"},
    }


@router.get("/strategies/{name}/backtest", response_model=Envelope)
def strategy_backtest(
    name: str, symbol: str | None = None, limit: Annotated[int, Query(ge=10, le=20000)] = 5000
) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "config" / "strategies" / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="strategy not found")
    engine = get_engine()
    if "stock_features" not in engine.views():
        raise HTTPException(status_code=404, detail="stock_features is not built")
    where = "" if not symbol else " WHERE symbol = ?"
    params = [] if not symbol else [symbol.upper()]
    rows = engine.query(
        f"SELECT symbol, date, close, sma_stack_ok AS entry_signal, "
        f"NOT sma_stack_ok AS exit_signal FROM stock_features{where} ORDER BY symbol, date LIMIT ?",
        params + [limit],
    ).records()
    return {
        "as_of": engine.data_through(),
        "data": backtest(rows, load_strategy(str(path))),
        "provenance": {"source": "stock_features", "calculation": "deterministic strategy backtest"},
    }
