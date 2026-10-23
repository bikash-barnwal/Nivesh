"""Strategy lab API end to end on the synthetic demo market."""

from __future__ import annotations

import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from nivesh.config import settings
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from nivesh.demo import make_demo_dataset


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    base = tmp_path_factory.mktemp("labapi")
    parquet = base / "parquet"
    make_demo_dataset(parquet, sessions=700, symbols=90)
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = base
    engine_mod._engine = Engine(parquet)
    from nivesh.api.main import app

    with TestClient(app) as c:
        yield c
    engine_mod._engine, settings.data_dir = old_engine, old_dir


def _run(client: TestClient, strategy_id: str, gates: bool = False) -> str:
    started = client.post("/api/backtests", json={"strategy_id": strategy_id, "gates": gates})
    assert started.status_code == 200, started.text
    job_id = started.json()["data"]["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        job = client.get(f"/api/backtests/jobs/{job_id}").json()["data"]
        if job["status"] != "running":
            assert job["status"] == "done", job
            return job["run_id"]
        time.sleep(0.3)
    raise AssertionError("backtest did not finish")


@pytest.fixture(scope="module")
def run_id(client):
    return _run(client, "trend_stack_v1", gates=True)


def test_strategies_list_before_and_after_a_run(client, run_id):
    rows = client.get("/api/strategies").json()["data"]
    assert {r["id"] for r in rows} >= {"trend_stack_v1", "breakout_volume_v1", "leaders_pullback_v1"}
    trend = next(r for r in rows if r["id"] == "trend_stack_v1")
    assert trend["latest_run"]["run_id"] == run_id and trend["entry"] and trend["rank_by"]


def test_strategy_detail_returns_spec_and_yaml(client):
    data = client.get("/api/strategies/trend_stack_v1").json()["data"]
    assert data["spec"]["name"] == "trend_stack" and "entry:" in data["yaml"]
    assert client.get("/api/strategies/nope_v9").status_code == 404


def test_unknown_strategy_cannot_be_run(client):
    assert client.post("/api/backtests", json={"strategy_id": "nope_v9"}).status_code == 404
    assert client.post("/api/backtests", json={}).status_code == 422
    assert client.get("/api/backtests/jobs/doesnotexist").status_code == 404


def test_run_summary_has_metrics_and_gates(client, run_id):
    body = client.get(f"/api/backtests/{run_id}").json()
    assert body["data"]["metrics"]["trades"] > 0 and "cagr" in body["data"]["metrics"]
    assert [g["name"] for g in body["data"]["gates"]["gates"]][0] == "minimum_trades"
    assert body["as_of"] == body["data"]["run"]["end"]


def test_equity_curve_has_drawdown_and_benchmark_overlay(client, run_id):
    data = client.get(f"/api/backtests/{run_id}/equity").json()["data"]
    assert data["curve"] and all(p["drawdown"] <= 0 for p in data["curve"])
    assert data["benchmark"] and data["benchmark"][0]["value"] == pytest.approx(10_000_000)


def test_trades_endpoint_filters_sorts_and_pages(client, run_id):
    full = client.get(f"/api/backtests/{run_id}/trades?limit=2000").json()
    total = full["total"]
    assert total == len(full["data"]) > 20
    dates = [t["entry_date"] for t in full["data"]]
    assert dates == sorted(dates, reverse=True)

    by_pnl = client.get(f"/api/backtests/{run_id}/trades?sort=pnl&order=desc&limit=5").json()["data"]
    assert [t["pnl"] for t in by_pnl] == sorted((t["pnl"] for t in by_pnl), reverse=True)
    assert max(t["pnl"] for t in full["data"]) == by_pnl[0]["pnl"]

    year = int(dates[0][:4])
    only_year = client.get(f"/api/backtests/{run_id}/trades?year={year}&limit=2000").json()
    assert all(t["entry_date"].startswith(str(year)) for t in only_year["data"])
    reason = full["data"][0]["exit_reason"]
    only_reason = client.get(f"/api/backtests/{run_id}/trades?exit_reason={reason}&limit=2000").json()["data"]
    assert only_reason and {t["exit_reason"] for t in only_reason} == {reason}

    page2 = client.get(f"/api/backtests/{run_id}/trades?limit=5&offset=5").json()
    assert page2["offset"] == 5 and [t["trade_id"] for t in page2["data"]] == [
        t["trade_id"] for t in full["data"][5:10]
    ]
    assert client.get(f"/api/backtests/{run_id}/trades?sort=bogus").status_code == 422


def test_trade_detail_has_window_regimes_and_templated_explanation(client, run_id):
    trades = client.get(f"/api/backtests/{run_id}/trades?limit=50").json()["data"]
    pick = next(t for t in trades if t["exit_reason"] != "end-of-test")
    detail = client.get(f"/api/backtests/{run_id}/trades/{pick['trade_id']}").json()["data"]
    window_dates = [w["date"] for w in detail["window"]]
    assert window_dates == sorted(window_dates)
    assert window_dates[0] < pick["entry_date"] and window_dates[-1] >= pick["exit_date"]
    assert detail["regimes"] and detail["trade"]["symbol"] == pick["symbol"]
    text = detail["explanation"]
    assert "ranked #" in text["radar"] and pick["sector"] in text["radar"]
    assert "Final P&L" in text["exit"]
    assert client.get(f"/api/backtests/{run_id}/trades/999999").status_code == 404


def test_leaderboard_is_ranked_by_calmar_one_row_per_strategy(client, run_id):
    _run(client, "leaders_pullback_v1")
    rows = client.get("/api/leaderboard").json()["data"]
    assert len({r["strategy_id"] for r in rows}) == len(rows) >= 2
    calmars = [r["calmar"] for r in rows if r["calmar"] is not None]
    assert calmars == sorted(calmars, reverse=True)
    assert all({"cagr", "max_drawdown", "sharpe", "trades", "win_rate", "gates_total"} <= r.keys() for r in rows)
    assert client.get("/api/backtests").json()["data"]


@pytest.mark.parametrize("bad", ["%2E%2E", "does-not-exist", "a.b"])
def test_unknown_or_malicious_run_ids_are_404(client, bad):
    assert client.get(f"/api/backtests/{bad}").status_code == 404
    assert client.get(f"/api/backtests/{bad}/equity").status_code == 404
    assert client.get(f"/api/backtests/{bad}/trades").status_code == 404


def test_date_range_is_respected(client):
    rid = _run_with_range(client)
    run = client.get(f"/api/backtests/{rid}").json()["data"]["run"]
    assert date.fromisoformat(run["start"]) >= date(2021, 1, 1) and date.fromisoformat(run["end"]) <= date(2021, 12, 31)


def _run_with_range(client: TestClient) -> str:
    started = client.post(
        "/api/backtests",
        json={"strategy_id": "trend_stack_v1", "start": "2021-01-01", "end": "2021-12-31", "gates": False},
    ).json()["data"]["job_id"]
    for _ in range(400):
        job = client.get(f"/api/backtests/jobs/{started}").json()["data"]
        if job["status"] != "running":
            assert job["status"] == "done", job
            return job["run_id"]
        time.sleep(0.3)
    raise AssertionError("range backtest did not finish")
