"""Paper accounts (forward tests) and live signals on the synthetic demo market."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nivesh.api import paper as paper_api
from nivesh.backtest.engine import load_panel, simulate
from nivesh.backtest.spec import get_strategy
from nivesh.config import settings
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from nivesh.demo import make_demo_dataset


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("paper")
    parquet = base / "parquet"
    days = make_demo_dataset(parquet, sessions=520, symbols=60)
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = base
    engine_mod._engine = Engine(parquet)
    from nivesh.api.main import app

    with TestClient(app) as client:
        yield client, days, parquet
    engine_mod._engine, settings.data_dir = old_engine, old_dir


def create(client, **body):
    return client.post("/api/paper", json={"strategy_id": "trend_stack_v1", **body})


def test_account_created_today_starts_flat_and_lists_the_orders_for_tomorrow(world):
    client, days, _ = world
    response = create(client, name="today")
    assert response.status_code == 200
    account = response.json()["data"]
    assert account["start_date"] == str(days[-1]) and account["capital"] == 10_000_000
    detail = client.get(f"/api/paper/{account['id']}").json()["data"]
    assert detail["account"]["equity"] == pytest.approx(10_000_000) and detail["open_positions"] == []
    assert detail["next_orders"]["as_of"] == str(days[-1])
    assert set(detail["next_orders"]) >= {"entries", "exits", "stops", "slots_free"}
    assert [p["date"] for p in detail["curve"]] == [str(days[-1])]


def test_replaying_from_the_past_matches_the_standard_backtest_for_closed_trades(world):
    client, days, parquet = world
    start = days[380]
    account = create(client, start_date=str(start), capital=5_000_000).json()["data"]
    detail = client.get(f"/api/paper/{account['id']}").json()["data"]

    strategy = get_strategy("trend_stack_v1").with_overrides({"portfolio.initial_capital": 5_000_000})
    reference = simulate(strategy, load_panel(strategy, parquet, start, None))
    closed = reference.trades.filter(reference.trades["exit_reason"] != "end-of-test")
    api_closed = [t for t in detail["trades"]]
    assert sorted((t["symbol"], t["entry_date"], t["exit_date"]) for t in api_closed) == sorted(
        (r["symbol"], str(r["entry_date"]), str(r["exit_date"])) for r in closed.iter_rows(named=True)
    )
    assert detail["curve"][0]["date"] == str(start) and detail["curve"][-1]["date"] == str(days[-1])
    still_open = {
        t["symbol"]
        for t in reference.trades.filter(reference.trades["exit_reason"] == "end-of-test").iter_rows(named=True)
    }
    assert {p["symbol"] for p in detail["open_positions"]} == still_open


def test_open_positions_carry_stops_and_unrealised_pnl(world):
    client, days, _ = world
    account = create(client, start_date=str(days[380])).json()["data"]
    detail = client.get(f"/api/paper/{account['id']}").json()["data"]
    assert detail["open_positions"], "the demo market should leave at least one position open"
    for pos in detail["open_positions"]:
        assert pos["stop"] < pos["last_close"] or pos["breakeven_active"] or pos["pending_exit"]
        assert pos["unrealized_pnl"] == pytest.approx(
            pos["market_value"] - pos["shares"] * pos["entry_price"] * (1 + 0.00135)
        )
    equity = detail["account"]["equity"]
    cash = detail["curve"][-1]["cash"]
    assert equity == pytest.approx(cash + sum(p["market_value"] for p in detail["open_positions"]))


def test_list_returns_a_summary_per_account_and_delete_removes_it(world):
    client, days, _ = world
    account = create(client, name="to delete", start_date=str(days[400])).json()["data"]
    rows = client.get("/api/paper").json()["data"]
    mine = next(r for r in rows if r["id"] == account["id"])
    assert {"equity", "pnl", "return", "open_positions", "closed_trades", "as_of"} <= mine.keys()
    assert client.delete(f"/api/paper/{account['id']}").json()["status"] == "deleted"
    assert client.get(f"/api/paper/{account['id']}").status_code == 404
    assert client.delete(f"/api/paper/{account['id']}").status_code == 404


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"strategy_id": "nope_v9"}, 404),
        ({"strategy_id": "trend_stack_v1", "capital": 0}, 422),
        ({"strategy_id": "trend_stack_v1", "capital": -5}, 422),
        ({"strategy_id": "trend_stack_v1", "start_date": "2099-01-01"}, 422),
        ({"strategy_id": "trend_stack_v1", "name": "x" * 81}, 422),
        ({}, 422),
    ],
)
def test_invalid_accounts_are_rejected(world, body, status):
    client, _, _ = world
    assert client.post("/api/paper", json=body).status_code == status


def test_live_signals_match_the_engine_for_the_latest_session(world):
    client, days, parquet = world
    body = client.get("/api/signals/live?strategy_id=trend_stack_v1&limit=50").json()
    data = body["data"]
    assert body["as_of"] == str(days[-1]) and data["rule"] and data["regime"]
    strategy = get_strategy("trend_stack_v1")
    expected = load_panel(strategy, parquet, days[-1], days[-1]).signals.get(0, [])[:50]
    assert [c["symbol"] for c in data["candidates"]] == [c["symbol"] for c in expected]
    ranks = [c["rank"] for c in data["candidates"]]
    assert ranks == sorted(ranks) and (not ranks or ranks[0] == 1)
    for c in data["candidates"]:
        assert c["close"] > 0 and c["stage"] == 2  # trend_stack requires stage 2


def test_live_signals_are_empty_and_explained_when_the_regime_is_blocked(world, monkeypatch):
    client, _, _ = world
    regime = client.get("/api/signals/live?strategy_id=trend_stack_v1").json()["data"]["regime"]
    strategy = get_strategy("trend_stack_v1").model_copy(update={"regime_block": [regime]})
    monkeypatch.setattr(paper_api, "_strategy_or_404", lambda _id: strategy)
    data = client.get("/api/signals/live?strategy_id=trend_stack_v1").json()["data"]
    assert data["blocked_by_regime"] is True and data["candidates"] == [] and regime in data["note"]


def test_live_signals_unknown_strategy_is_404(world):
    client, _, _ = world
    assert client.get("/api/signals/live?strategy_id=nope_v9").status_code == 404
    assert client.get("/api/signals/live").status_code == 422
