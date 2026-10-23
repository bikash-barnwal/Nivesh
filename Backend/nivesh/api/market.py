"""Market-level intelligence endpoints: overview, engines, events with historical outcomes, analogues."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Query

from nivesh.api.common import (
    Envelope,
    envelope,
    history_snapshot,
    latest_date,
    require_view,
    state_snapshot,
)
from nivesh.config import settings
from nivesh.data.engine import Engine, get_engine

router = APIRouter(prefix="/api/market", tags=["market"])

SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2, "INFO": 3}
HORIZONS = (("1w", "fwd_1w"), ("1m", "fwd_1m"), ("3m", "fwd_3m"))

AsOf = Annotated[date | None, Query(description="Show the state as of this date (default: latest)")]


def _yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((settings.context_dir / name).read_text(encoding="utf-8")) or {}


def _benchmark() -> str:
    return _yaml("regimes.yaml").get("benchmark_index", "Nifty 500")


def _baseline(engine: Engine, as_of: date) -> dict[str, Any]:
    """Median forward return and hit rate over all benchmark days, using only returns observed by `as_of`."""
    if "index_daily" not in engine.views():
        return {}
    leads = ", ".join(
        f"lead(close, {n}) OVER w / nullif(close, 0) - 1 AS f{label}, lead(date, {n}) OVER w AS d{label}"
        for label, n in (("1w", 5), ("1m", 21), ("3m", 63))
    )
    aggs = ", ".join(
        f"median(f{label}) FILTER (WHERE d{label} <= ?) AS med_{label}, "
        f"avg((f{label} > 0)::INT) FILTER (WHERE d{label} <= ?) AS hit_{label}"
        for label, _ in HORIZONS
    )
    sql = (
        f"WITH b AS (SELECT date, close, {leads} FROM index_daily WHERE index_name = ? AND date <= ? "
        f"WINDOW w AS (ORDER BY date)) SELECT {aggs} FROM b"
    )
    row = engine.query(sql, [_benchmark(), as_of, *([as_of] * 6)])
    return dict(zip(row.columns, row.rows[0], strict=True)) if row.rows else {}


def _event_outcomes(engine: Engine, state: date) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Per event: forward-return stats over prior episodes (first day of each streak) observed by `state`."""
    aggs = ", ".join(
        f"median({col}) FILTER (WHERE {col}_date <= ?) AS med_{label}, "
        f"avg(({col} > 0)::INT) FILTER (WHERE {col}_date <= ?) AS hit_{label}, "
        f"count({col}) FILTER (WHERE {col}_date <= ?) AS n_{label}"
        for label, col in HORIZONS
    )
    stats = engine.query(
        f"SELECT event, count(*) AS episodes, {aggs} FROM market_events WHERE streak = 1 AND date < ? GROUP BY event",
        [*([state] * 9), state],
    ).records()
    recent_rows = engine.query(
        "SELECT event, date, fwd_1m FROM market_events WHERE streak = 1 AND date < ? AND fwd_1m_date <= ? "
        "ORDER BY date DESC",
        [state, state],
    ).records()
    recent: dict[str, list[dict[str, Any]]] = {}
    for row in recent_rows:
        bucket = recent.setdefault(row["event"], [])
        if len(bucket) < 3:
            bucket.append({"date": row["date"], "fwd_1m": row["fwd_1m"]})
    return {s["event"]: s for s in stats}, recent


@router.get("/breadth", response_model=Envelope)
def breadth(as_of: AsOf = None) -> dict[str, Any]:
    return state_snapshot(get_engine(), "market_breadth", as_of)


@router.get("/engines", response_model=Envelope)
def engines(as_of: AsOf = None, sparkline: Annotated[int, Query(ge=0, le=500)] = 60) -> dict[str, Any]:
    engine = get_engine()
    result = state_snapshot(engine, "market_engines", as_of, order="engine")
    state = result["as_of"]
    if state is not None and sparkline:
        spark = engine.query(
            "SELECT engine, list(value ORDER BY date) AS spark FROM ("
            "SELECT engine, date, value FROM market_engines WHERE date <= ? AND value IS NOT NULL "
            "QUALIFY row_number() OVER (PARTITION BY engine ORDER BY date DESC) <= ?) GROUP BY engine",
            [state, sparkline],
        ).records()
        by_engine = {s["engine"]: s["spark"] for s in spark}
        for row in result["data"]:
            row["spark"] = by_engine.get(row["engine"], [])
    return result


@router.get("/regime-history", response_model=Envelope)
def regime_history(as_of: AsOf = None, sessions: Annotated[int, Query(ge=1, le=3000)] = 150) -> dict[str, Any]:
    return history_snapshot(get_engine(), "market_regime", as_of, sessions)


@router.get("/events", response_model=Envelope)
def events(as_of: AsOf = None) -> dict[str, Any]:
    engine = get_engine()
    require_view(engine, "market_events")
    state = latest_date(engine, "market_events", as_of)
    if state is None:
        return envelope([], as_of=None, engine=engine, source="market_events", calculation="no events built")
    # Only events firing on the engine-state date are "active"; older rows belong to the log.
    engine_state = latest_date(engine, "market_engines", as_of) if "market_engines" in engine.views() else state
    active_date = engine_state or state
    rows = engine.query("SELECT * FROM market_events WHERE date = ?", [active_date]).records()
    stats, recent = _event_outcomes(engine, active_date)
    playbooks = _yaml("events.yaml")
    playbook_by_event = {e["name"]: e.get("playbook") for e in playbooks.get("events", [])}
    for row in rows:
        row["outcomes"] = stats.get(row["event"], {})
        row["recent_occurrences"] = recent.get(row["event"], [])
        row["actions"] = playbooks.get("playbooks", {}).get(playbook_by_event.get(row["event"]), {})
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), -r["confidence"]))
    return envelope(
        rows,
        as_of=active_date,
        engine=engine,
        source="market_events",
        calculation="events firing on the state date; outcomes use prior episodes observed by that date",
        baseline=_baseline(engine, active_date),
    )


@router.get("/events/log", response_model=Envelope)
def event_log(as_of: AsOf = None, sessions: Annotated[int, Query(ge=1, le=2000)] = 30) -> dict[str, Any]:
    return history_snapshot(get_engine(), "market_events", as_of, sessions * 6)


@router.get("/analogues", response_model=Envelope)
def analogues(as_of: AsOf = None) -> dict[str, Any]:
    engine = get_engine()
    require_view(engine, "market_analogues")
    state = latest_date(engine, "market_analogues", as_of)
    if state is None:
        return envelope([], as_of=None, engine=engine, source="market_analogues", calculation="none built")
    regime_join = "LEFT JOIN market_regime r ON r.date = a.analogue_date" if "market_regime" in engine.views() else ""
    regime_col = "r.regime AS regime_then" if regime_join else "NULL AS regime_then"
    rows = engine.query(
        f"SELECT a.*, {regime_col} FROM market_analogues a {regime_join} WHERE a.date = ? ORDER BY a.rank", [state]
    ).records()
    observed_1m = [r["fwd_1m"] for r in rows if r["fwd_1m"] is not None]
    observed_3m = [r["fwd_3m"] for r in rows if r["fwd_3m"] is not None]

    def median(values: list[float]) -> float | None:
        ordered = sorted(values)
        n = len(ordered)
        return None if not n else (ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2)

    summary = {
        "matches": len(rows),
        "median_1m": median(observed_1m),
        "share_positive_1m": (sum(v > 0 for v in observed_1m) / len(observed_1m)) if observed_1m else None,
        "median_3m": median(observed_3m),
        "share_positive_3m": (sum(v > 0 for v in observed_3m) / len(observed_3m)) if observed_3m else None,
        "note": "Analogues describe tendency, not certainty; use as context, not prediction.",
    }
    return envelope(
        rows,
        as_of=state,
        engine=engine,
        source="market_analogues",
        calculation="nearest earlier engine profiles, >=42 days apart, outcomes observed by as_of",
        summary=summary,
    )


@router.get("/overview", response_model=Envelope)
def overview(as_of: AsOf = None) -> dict[str, Any]:
    engine = get_engine()
    require_view(engine, "market_regime")
    state = latest_date(engine, "market_regime", as_of)
    if state is None:
        return envelope(None, as_of=None, engine=engine, source="market_regime", calculation="not built")
    regime = engine.query("SELECT * FROM market_regime WHERE date = ?", [state]).records()[0]
    active: list[dict[str, Any]] = []
    if "market_events" in engine.views():
        active = engine.query(
            "SELECT event, severity, confidence, streak FROM market_events WHERE date = ?", [state]
        ).records()
        active.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), -r["confidence"]))
    narrative = (
        f"Market is in a {regime['regime']} regime (stable in {regime['confidence']:.0f}% of the last sessions, "
        f"{regime['sessions_active']} sessions active). Posture: {regime['posture']}."
    )
    concern = next((e for e in active if e["severity"] in ("CRITICAL", "WARNING")), None)
    support = next((e for e in active if e["severity"] == "INFO"), None)
    if concern:
        narrative += f" Primary concern: {concern['event'].replace('_', ' ')}."
    if support:
        narrative += f" Primary support: {support['event'].replace('_', ' ')}."
    return envelope(
        {"regime": regime, "active_events": active, "narrative": narrative},
        as_of=state,
        engine=engine,
        source="market_regime,market_events",
        calculation="regime rules (context/regimes.yaml) with a templated narrative",
    )
