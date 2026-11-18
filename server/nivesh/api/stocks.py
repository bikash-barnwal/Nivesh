"""Single-stock intelligence panel: safety filter, lifecycle, gauges and recent events."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from nivesh.api.common import Envelope, envelope, latest_date, require_view
from nivesh.data.engine import Engine, get_engine
from nivesh.rules import compile_expr
from nivesh.stock_panel import feature_columns as _feature_columns
from nivesh.stock_panel import lifecycle as _lifecycle
from nivesh.stock_panel import load_cfg as _cfg

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


def _rs(
    engine: Engine, symbol: str, state: date, ret_63d: float | None, benchmark: str
) -> tuple[float | None, float | None]:
    """Percentile of the 63-session return among liquid stocks that day, and the return relative to the benchmark."""
    rs_rank = None
    if ret_63d is not None:
        rows = engine.query(
            "WITH u AS (SELECT symbol, 100 * percent_rank() OVER (ORDER BY ret_63d) AS r FROM stock_features "
            "WHERE date = ? AND ret_63d IS NOT NULL AND history_sessions >= 63 AND avg_turnover20_lacs >= 100 "
            "AND close >= 20) SELECT r FROM u WHERE symbol = ?",
            [state, symbol],
        ).rows
        rs_rank = rows[0][0] if rows else None

    rs_500 = None
    if ret_63d is not None and "index_daily" in engine.views():
        closes = engine.query(
            "SELECT close FROM index_daily WHERE index_name = ? AND date <= ? ORDER BY date DESC LIMIT 64",
            [benchmark, state],
        ).rows
        if len(closes) == 64 and closes[-1][0]:
            bench = closes[0][0] / closes[-1][0] - 1
            rs_500 = 100 * (1 + ret_63d) / (1 + bench)
    return rs_rank, rs_500


def _gauges(
    engine: Engine, symbol: str, state: date, rs_rank: float | None, rs_500: float | None
) -> list[dict[str, Any]]:
    gauges = _cfg().get("gauges", [])
    if not gauges:
        return []
    columns = [*_feature_columns(engine), "rs_rank", "rs_500"]
    exprs = ", ".join(f"({compile_expr(g['expr'], columns)}) AS g{i}" for i, g in enumerate(gauges))
    row = engine.query(
        f"SELECT {exprs} FROM (SELECT f.*, CAST(? AS DOUBLE) AS rs_rank, CAST(? AS DOUBLE) AS rs_500 "
        "FROM stock_features f WHERE symbol = ? AND date = ?)",
        [rs_rank, rs_500, symbol, state],
    ).rows
    values = row[0] if row else [None] * len(gauges)
    return [
        {
            "name": g["name"],
            "label": g.get("label", g["name"]),
            "value": None if v is None else round(max(0.0, min(100.0, float(v))), 1),
        }
        for g, v in zip(gauges, values, strict=True)
    ]


def _check(name: str, value: Any, threshold: str, passed: bool | None) -> dict[str, Any]:
    return {
        "check": name,
        "value": value,
        "threshold": threshold,
        "status": "UNKNOWN" if passed is None else ("PASS" if passed else "FAIL"),
    }


def _safety(engine: Engine, symbol: str, state: date, close: float | None) -> dict[str, Any]:
    cfg = _cfg()["safety_stage1"]
    views = set(engine.views())
    sessions = [
        r[0]
        for r in engine.query(
            "SELECT date FROM stock_daily WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 50", [symbol, state]
        ).rows
    ]
    hits: set[date] = set()
    if "circuit_hits" in views and sessions:
        hits = {
            r[0]
            for r in engine.query(
                "SELECT date FROM circuit_hits WHERE symbol = ? AND date >= ? AND date <= ?",
                [symbol, sessions[-1], state],
            ).rows
        }
    last20 = sessions[:20]
    circuit_days = sum(1 for d in last20 if d in hits) if "circuit_hits" in views else None
    streak = None
    if "circuit_hits" in views:
        streak = 0
        for d in sessions:
            if d not in hits:
                break
            streak += 1

    vol = engine.query(
        "WITH r AS (SELECT volume, row_number() OVER (ORDER BY date DESC) AS rn FROM stock_daily "
        "WHERE symbol = ? AND date <= ?) SELECT avg(volume) FILTER (WHERE rn <= 30), count(*) FILTER (WHERE rn <= 30), "
        "avg(volume) FILTER (WHERE rn <= 50), count(*) FILTER (WHERE rn <= 50) FROM r",
        [symbol, state],
    ).rows[0]
    vol30 = vol[0] if vol[1] >= 30 else None
    vol50 = vol[2] if vol[3] >= 50 else None

    band = None
    if "price_bands" in views:
        rows = engine.query(
            "SELECT band_pct FROM price_bands WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            [symbol, state],
        ).rows
        band = rows[0][0] if rows else None
    band_known = "price_bands" in views and band is not None

    checks = [
        _check(
            "Circuit days (20d)",
            circuit_days,
            f"< {cfg['circuit_days_20d_max']}",
            None if circuit_days is None else circuit_days < cfg["circuit_days_20d_max"],
        ),
        _check(
            "Circuit streak",
            streak,
            f"< {cfg['circuit_streak_max']}",
            None if streak is None else streak < cfg["circuit_streak_max"],
        ),
        _check(
            "Liquidity 30d (avg volume)",
            None if vol30 is None else round(vol30),
            f"> {cfg['min_avg_volume_30d']}",
            None if vol30 is None else vol30 > cfg["min_avg_volume_30d"],
        ),
        _check(
            "Liquidity 50d (avg volume)",
            None if vol50 is None else round(vol50),
            f"> {cfg['min_avg_volume_50d']}",
            None if vol50 is None else vol50 > cfg["min_avg_volume_50d"],
        ),
        _check("Minimum price", close, f"> {cfg['min_price']}", None if close is None else close > cfg["min_price"]),
        _check(
            "Price band",
            band,
            f"not in {cfg['blocked_price_bands']}",
            None if not band_known else band not in cfg["blocked_price_bands"],
        ),
    ]
    statuses = {c["status"] for c in checks}
    overall = "FAIL" if "FAIL" in statuses else ("PASS" if statuses == {"PASS"} else "INCOMPLETE")
    return {"checks": checks, "overall": overall}


@router.get("/{symbol}/panel", response_model=Envelope)
def stock_panel(symbol: str, as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    engine = get_engine()
    require_view(engine, "stock_features")
    sym = symbol.upper()
    state = latest_date(engine, "stock_features", as_of, "symbol = ?", [sym])
    if state is None:
        raise HTTPException(status_code=404, detail=f"no data for symbol {sym}")
    feats = engine.query("SELECT * FROM stock_features WHERE symbol = ? AND date = ?", [sym, state]).records()[0]
    benchmark = _cfg().get("benchmark_index", "Nifty 500")
    rs_rank, rs_500 = _rs(engine, sym, state, feats.get("ret_63d"), benchmark)

    views = set(engine.views())
    meta: dict[str, Any] = {"symbol": sym}
    for view, wanted in (
        ("sector_map", ("sector", "nse_industry", "company")),
        ("security_master", ("company", "isin", "listing_date")),
    ):
        if view not in views:
            continue
        rows = engine.query(f'SELECT * FROM "{view}" WHERE symbol = ?', [sym]).records()
        if rows:
            for key in wanted:
                if key in rows[0] and not meta.get(key):
                    meta[key] = rows[0][key]
    meta.setdefault("sector", None)

    events: dict[str, Any] = {}
    if "circuit_hits" in views:
        events["circuit_hits"] = engine.query(
            "SELECT date, side FROM circuit_hits WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT 10",
            [sym, state],
        ).records()
    if "corporate_actions" in views:
        events["corporate_actions"] = engine.query(
            "SELECT ex_date, purpose, price_factor FROM corporate_actions WHERE symbol = ? "
            "ORDER BY ex_date DESC LIMIT 5",
            [sym],
        ).records()

    if "stock_events" in views:
        events["news"] = engine.query(
            "SELECT event_type, direction, published_at, summary, value_cr, confidence, url FROM stock_events "
            "WHERE symbol = ? AND available_from <= ? ORDER BY published_at DESC LIMIT 10",
            [sym, state],
        ).records()

    data = {
        "meta": meta,
        "price": {"close": feats["close"], "ret_1d": feats.get("ret_1d"), "ret_21d": feats.get("ret_21d")},
        "features": feats,
        "lifecycle": _lifecycle(engine, sym, state),
        "gauges": _gauges(engine, sym, state, rs_rank, rs_500),
        "relative_strength": {"rs_rank": rs_rank, "rs_500": rs_500, "benchmark": benchmark},
        "safety_stage1": _safety(engine, sym, state, feats["close"]),
        "events": events,
    }
    return envelope(
        data,
        as_of=state,
        engine=engine,
        source="stock_features,stock_daily,circuit_hits,price_bands",
        calculation="rules and formulas from context/stock_panel.yaml",
    )
