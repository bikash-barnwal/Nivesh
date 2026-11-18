"""Sector and industry-group intelligence endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Query

from nivesh.api.common import Envelope, envelope, latest_date, require_view
from nivesh.config import settings
from nivesh.data.engine import Engine, get_engine

router = APIRouter(prefix="/api/sectors", tags=["sectors"])

AsOf = Annotated[date | None, Query(description="Show the state as of this date (default: latest)")]
IMPROVING = ("ACCELERATING", "IMPROVING")
DETERIORATING = ("DETERIORATING", "FADING")


def _cfg() -> dict[str, Any]:
    return yaml.safe_load((settings.context_dir / "sectors.yaml").read_text(encoding="utf-8")) or {}


def _header(engine: Engine, state: date, rows: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = _cfg()
    cyclical, defensive = cfg.get("cyclical", []), cfg.get("defensive", [])

    def mean_excess(names: list[str]) -> float | None:
        vals = [r["excess_3m"] for r in rows if r["sector"] in names and r["excess_3m"] is not None]
        return sum(vals) / len(vals) if vals else None

    cyc, dfn = mean_excess(cyclical), mean_excess(defensive)
    spread_pp = None if cyc is None or dfn is None else round((cyc - dfn) * 100, 1)
    posture = None if spread_pp is None else ("risk-on" if spread_pp > 0 else "risk-off")

    dispersion = engine.query(
        "WITH d AS (SELECT date, stddev_samp(excess_3m) AS disp FROM sector_daily "
        "WHERE rank IS NOT NULL AND excess_3m IS NOT NULL AND date <= ? GROUP BY date) "
        "SELECT 100.0 * count(*) FILTER (WHERE disp <= (SELECT disp FROM d WHERE date = ?)) / count(*) FROM d",
        [state, state],
    ).rows[0][0]

    persistence = engine.query(
        "WITH r AS (SELECT date, sector, rank, n_sectors, "
        "lead(rank, 21) OVER (PARTITION BY sector ORDER BY date) AS r21, "
        "lead(date, 21) OVER (PARTITION BY sector ORDER BY date) AS d21 "
        "FROM sector_daily WHERE rank IS NOT NULL AND date <= ?) "
        "SELECT avg((r21 <= ceil(n_sectors / 4.0))::INT), count(*) FROM r "
        "WHERE rank <= ceil(n_sectors / 4.0) AND r21 IS NOT NULL AND d21 <= ?",
        [state, state],
    ).rows[0]

    ranked = sorted((r for r in rows if r["rank"] is not None), key=lambda r: r["rank"])
    leaders = [f"{r['sector']} (#{r['rank']})" for r in ranked[:3]]
    laggards = [r["sector"] for r in ranked[-3:]]
    narrative = f"Leadership: {', '.join(leaders)}; weakest: {', '.join(laggards)}." if ranked else ""
    if posture:
        side = "leading" if spread_pp > 0 else "trailing"
        narrative += f" Risk posture: {posture} (cyclicals {side} defensives by {spread_pp:+.1f}pp over 3 months)."
    if dispersion is not None:
        narrative += f" Cross-group dispersion at the {dispersion:.0f}th percentile."
    if persistence[0] is not None:
        narrative += (
            f" Base rate: a top-quartile group stayed top-quartile one month later {persistence[0] * 100:.0f}% of the "
            f"time (n={persistence[1]})."
        )
    return {
        "risk_posture": posture,
        "cyclical_minus_defensive_pp": spread_pp,
        "dispersion_percentile": None if dispersion is None else round(dispersion, 1),
        "leader_persistence_1m": None if persistence[0] is None else round(persistence[0], 3),
        "leader_persistence_n": persistence[1],
        "narrative": narrative,
    }


def _trajectories(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    def card(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "sector": r["sector"],
            "rs_trend": r["rs_trend"],
            "rank_path": [r["rank_1q"], r["rank_2m"], r["rank_1m"], r["rank"]],
            "ranks_per_quarter": r["d1q"],
            "participation": r["participation"],
            "flow": r["flow"],
            "exhaustion": r["exhaustion"],
        }

    ranked = [r for r in rows if r["rank"] is not None and r["d1q"] is not None]
    improving = sorted((r for r in ranked if r["rs_trend"] in IMPROVING), key=lambda r: -r["d1q"])
    deteriorating = sorted((r for r in ranked if r["rs_trend"] in DETERIORATING), key=lambda r: r["d1q"])
    return {"improving": [card(r) for r in improving], "deteriorating": [card(r) for r in deteriorating]}


@router.get("", response_model=Envelope)
def sectors(as_of: AsOf = None) -> dict[str, Any]:
    engine = get_engine()
    require_view(engine, "sector_daily")
    state = latest_date(engine, "sector_daily", as_of)
    if state is None:
        return envelope([], as_of=None, engine=engine, source="sector_daily", calculation="not built")
    rows = engine.query("SELECT * FROM sector_daily WHERE date = ? ORDER BY rank NULLS LAST, sector", [state]).records()
    return envelope(
        rows,
        as_of=state,
        engine=engine,
        source="sector_daily",
        calculation="sector RS/participation/flow scores ranked by strength (context/sectors.yaml)",
        header=_header(engine, state, rows),
        trajectories=_trajectories(rows),
    )


@router.get("/events", response_model=Envelope)
def sector_events(as_of: AsOf = None, sessions: Annotated[int, Query(ge=1, le=250)] = 10) -> dict[str, Any]:
    engine = get_engine()
    require_view(engine, "sector_events")
    state = latest_date(engine, "sector_events", as_of)
    if state is None:
        return envelope([], as_of=None, engine=engine, source="sector_events", calculation="none built")
    recent = engine.query(
        "WITH d AS (SELECT DISTINCT date FROM sector_daily WHERE date <= ? ORDER BY date DESC LIMIT ?) "
        "SELECT e.* FROM sector_events e WHERE e.date IN (SELECT date FROM d) ORDER BY e.date DESC, e.sector",
        [as_of or state, sessions],
    ).records()
    aggs = ", ".join(
        f"median(fwd_{h}) FILTER (WHERE fwd_{h}_date <= ?) AS med_{h}, "
        f"avg((fwd_{h} > 0)::INT) FILTER (WHERE fwd_{h}_date <= ?) AS hit_{h}, "
        f"count(fwd_{h}) FILTER (WHERE fwd_{h}_date <= ?) AS n_{h}"
        for h in ("1w", "1m", "3m")
    )
    cutoff = as_of or state
    stats = {
        r["event"]: r
        for r in engine.query(
            f"SELECT event, count(*) AS occurrences, {aggs} FROM sector_events WHERE date <= ? GROUP BY event",
            [*([cutoff] * 9), cutoff],
        ).records()
    }
    for row in recent:
        row["historical"] = stats.get(row["event"], {})
    return envelope(
        recent,
        as_of=cutoff,
        engine=engine,
        source="sector_events",
        calculation="events in the last N sessions; history uses forward excess returns observed by as_of",
    )
