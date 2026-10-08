"""Forward tests ("paper accounts") and live signals.

A paper account runs a strategy from a start date with the same engine as backtests, on point-in-time data, and keeps
its positions open. There is no second code path to drift from the backtester: each account is simply a strategy
replayed from its start date to the latest session, plus the orders it would place at the next open.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from nivesh.api.common import Envelope, envelope
from nivesh.appdb import create_paper_account, delete_paper_account, get_paper_account, list_paper_accounts
from nivesh.backtest.engine import BacktestError, Result, load_panel, simulate
from nivesh.backtest.spec import Strategy, get_strategy
from nivesh.data.engine import get_engine

router = APIRouter(prefix="/api", tags=["paper"])

_CACHE: dict[tuple, Result] = {}
_CACHE_MAX = 32
MAX_TRADES_RETURNED = 200


class PaperCreate(BaseModel):
    strategy_id: str
    name: str | None = Field(default=None, max_length=80)
    start_date: date | None = None
    capital: float | None = Field(default=None, gt=0)


def _env(data: Any, source: str, calculation: str, as_of: date | None = None) -> dict[str, Any]:
    engine = get_engine()
    return envelope(data, as_of=as_of or engine.data_through(), engine=engine, source=source, calculation=calculation)


def _strategy_or_404(strategy_id: str) -> Strategy:
    try:
        return get_strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown strategy '{strategy_id}'") from exc


def _forward(account: dict[str, Any]) -> Result:
    """Replay the account's strategy from its start date; cached per (account, latest data date)."""
    engine = get_engine()
    key = (account["id"], account["start_date"], account["capital"], account["strategy_id"], engine.data_through())
    if key in _CACHE:
        return _CACHE[key]
    strategy = _strategy_or_404(account["strategy_id"]).with_overrides(
        {"portfolio.initial_capital": account["capital"]}
    )
    try:
        panel = load_panel(strategy, engine.parquet_dir, date.fromisoformat(account["start_date"]), None)
    except BacktestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = simulate(strategy, panel, close_open=False)
    result.data_through = panel.dates[-1]
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result


def _summary(account: dict[str, Any], result: Result) -> dict[str, Any]:
    final = float(result.equity[-1])
    return {
        **account,
        "as_of": result.dates[-1],
        "equity": final,
        "pnl": final - account["capital"],
        "return": final / account["capital"] - 1,
        "open_positions": len(result.open_positions),
        "closed_trades": result.trades.height,
        "max_drawdown": result.metrics.get("max_drawdown"),
    }


def _account_or_404(account_id: int) -> dict[str, Any]:
    account = get_paper_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"unknown paper account {account_id}")
    return account


@router.post("/paper", response_model=Envelope)
def create_account(request: PaperCreate) -> dict[str, Any]:
    strategy = _strategy_or_404(request.strategy_id)
    through = get_engine().data_through()
    if through is None:
        raise HTTPException(status_code=409, detail="no market data is loaded")
    start = request.start_date or through
    if start > through:
        raise HTTPException(status_code=422, detail=f"start_date is after the last loaded session ({through})")
    capital = request.capital or strategy.portfolio.initial_capital
    name = request.name or f"{strategy.label or strategy.id} from {start}"
    account = create_paper_account(name, strategy.id, start.isoformat(), capital)
    return _env(account, "appdb.paper_accounts", "paper account created")


@router.get("/paper", response_model=Envelope)
def accounts() -> dict[str, Any]:
    rows = []
    for account in list_paper_accounts():
        try:
            rows.append(_summary(account, _forward(account)))
        except HTTPException as exc:
            rows.append({**account, "error": exc.detail})
    return _env(rows, "appdb.paper_accounts", "forward tests replayed on point-in-time data")


@router.get("/paper/{account_id}", response_model=Envelope)
def account_detail(account_id: int) -> dict[str, Any]:
    account = _account_or_404(account_id)
    result = _forward(account)
    curve = []
    running_peak = result.equity[0]
    for d, e, c, inv in zip(result.dates, result.equity, result.cash, result.invested, strict=True):
        running_peak = max(running_peak, e)
        curve.append(
            {
                "date": d,
                "equity": float(e),
                "cash": float(c),
                "invested": float(inv),
                "drawdown": float(e / running_peak - 1),
            }
        )
    trades = result.trades.sort("exit_date", descending=True).head(MAX_TRADES_RETURNED)
    data = {
        "account": _summary(account, result),
        "metrics": result.metrics,
        "curve": curve,
        "open_positions": result.open_positions,
        "next_orders": result.next_orders,
        "trades": trades.to_dicts(),
        "strategy": result.strategy.model_dump(),
    }
    return _env(data, "appdb.paper_accounts", "strategy replayed from the account start date", as_of=result.dates[-1])


@router.delete("/paper/{account_id}")
def remove_account(account_id: int) -> dict[str, Any]:
    _account_or_404(account_id)
    delete_paper_account(account_id)
    for key in [k for k in _CACHE if k[0] == account_id]:
        _CACHE.pop(key, None)
    return {"status": "deleted", "id": account_id}


@router.get("/signals/live", response_model=Envelope)
def live_signals(
    strategy_id: Annotated[str, Query()], limit: Annotated[int, Query(ge=1, le=100)] = 25
) -> dict[str, Any]:
    """Today's ranked candidates for a strategy, using only data up to the latest session."""
    strategy = _strategy_or_404(strategy_id)
    engine = get_engine()
    through = engine.data_through()
    if through is None or "stock_features" not in engine.views():
        raise HTTPException(status_code=409, detail="analytics are not built")
    try:
        panel = load_panel(strategy, engine.parquet_dir, through, through)
    except BacktestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    regime = None
    if "market_regime" in engine.views():
        rows = engine.query("SELECT regime FROM market_regime WHERE date = ?", [through]).rows
        regime = rows[0][0] if rows else None
    blocked = regime in strategy.regime_block if regime else False
    candidates = panel.signals.get(0, [])[:limit]
    if candidates:
        symbols = [c["symbol"] for c in candidates]
        feats = {
            r["symbol"]: r
            for r in engine.query(
                "SELECT symbol, close, ret_21d, ret_63d, rvol20, rsi14, stage, dist_52w_high_pct FROM stock_features "
                "WHERE date = ? AND symbol IN (SELECT unnest(?))",
                [through, symbols],
            ).records()
        }
        candidates = [
            {**c, **{k: v for k, v in feats.get(c["symbol"], {}).items() if k != "symbol"}} for c in candidates
        ]
    data = {
        "strategy_id": strategy.id,
        "rule": strategy.entry.when,
        "rank_by": strategy.entry.rank_by,
        "regime": regime,
        "blocked_by_regime": blocked,
        "candidates": candidates,
        "note": (
            f"The strategy does not enter in a {regime} regime."
            if blocked
            else "Ranked by the strategy's rank_by order."
        ),
    }
    return _env(data, "stock_features", "entry rule evaluated on the latest session", as_of=through)
