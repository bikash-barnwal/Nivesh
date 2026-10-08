"""Sector intelligence: builders, rules from YAML, as-of safety and the API."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
import yaml
from fastapi.testclient import TestClient

from nivesh import analytics
from nivesh.config import settings
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from tests.synth import make_dataset, truncate_dataset


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("sectors")
    days = make_dataset(root)
    analytics.build_analytics(parquet_dir=root)
    return root, days


def _read(root, name) -> pl.DataFrame:
    return pl.read_parquet(root / name / f"{name}.parquet")


def _cfg() -> dict:
    return yaml.safe_load((settings.context_dir / "sectors.yaml").read_text(encoding="utf-8"))


def test_every_date_ranks_each_sector_once_and_reports_unclassified(built):
    root, _ = built
    daily = _read(root, "sector_daily")
    ranked = daily.filter(pl.col("rank").is_not_null())
    for _, group in ranked.group_by("date"):
        assert sorted(group["rank"].to_list()) == list(range(1, group.height + 1))
        assert (group["n_sectors"] == group.height).all()
    unclassified = daily.filter(pl.col("sector") == "Unclassified")
    assert unclassified.height > 0, "unmapped symbols must be reported, not dropped"
    assert unclassified["rank"].null_count() == unclassified.height


def test_trend_and_verdict_values_come_from_yaml(built):
    root, _ = built
    cfg = _cfg()
    daily = _read(root, "sector_daily").filter(pl.col("rank").is_not_null())
    trends = {r["trend"] for r in cfg["trend_rules"]} | {cfg["trend_default"]}
    verdicts = {r["verdict"] for r in cfg["verdict_rules"]} | {cfg["verdict_default"]}
    assert set(daily["rs_trend"].unique()) <= trends
    assert set(daily["verdict"].unique()) <= verdicts
    # d1q is the rank improvement over 63 sessions
    sample = daily.filter(pl.col("rank_1q").is_not_null())
    assert (sample["d1q"] == sample["rank_1q"] - sample["rank"]).all()
    # trend rule check: strongly improving ranks are ACCELERATING/IMPROVING
    assert (daily.filter(pl.col("d1q") >= 4)["rs_trend"].is_in(["ACCELERATING", "IMPROVING"])).all()


def test_scores_are_bounded_and_days_in_verdict_counts_runs(built):
    root, _ = built
    daily = _read(root, "sector_daily").filter(pl.col("rank").is_not_null())
    for col in ("rs", "participation", "flow", "leadership", "exhaustion", "strength"):
        values = daily[col].drop_nulls()
        assert values.min() >= 0 and values.max() <= 100, col
    one = daily.filter(pl.col("sector") == "Energy").sort("date")
    previous, run = None, 0
    for verdict, days in zip(one["verdict"], one["days_in_verdict"], strict=True):
        run = run + 1 if verdict == previous else 1
        assert days == run
        previous = verdict


def test_sector_events_fire_on_transitions_and_mask_forward_returns(built):
    root, days = built
    events = _read(root, "sector_events")
    daily = _read(root, "sector_daily")
    assert events.height > 0
    assert set(events["event"]) <= {e["name"] for e in _cfg()["events"]}
    # an event never fires on two consecutive sessions for the same sector (it fires on the transition only)
    session_index = {d: i for i, d in enumerate(sorted(daily["date"].unique()))}
    for _, group in events.group_by(["sector", "event"]):
        idx = sorted(session_index[d] for d in group["date"])
        assert all(b - a > 1 for a, b in zip(idx, idx[1:], strict=False))
    with_fwd = events.filter(pl.col("fwd_3m").is_not_null())
    assert (with_fwd["fwd_3m_date"] > with_fwd["date"]).all()
    assert events["fwd_3m_date"].drop_nulls().max() <= days[-1]


def test_sector_tables_do_not_look_ahead(built, tmp_path):
    root, days = built
    cutoff = days[450]
    truncate_dataset(root, tmp_path, cutoff)
    analytics.run_analytics(parquet_dir=tmp_path)
    keys = ["sector", "date"]
    full = _read(root, "sector_daily").filter(pl.col("date") <= cutoff).sort(keys)
    short = _read(tmp_path, "sector_daily").sort(keys)
    assert full.height == short.height
    for col in ("rank", "rs", "participation", "flow", "strength", "rs_trend", "verdict", "rs_line", "excess_3m"):
        a, b = full[col], short[col]
        if a.dtype == pl.Float64:
            diff = (a.fill_null(0.0) - b.fill_null(0.0)).abs().max()
            assert diff < 1e-9, col
        else:
            assert a.equals(b), col


@pytest.fixture(scope="module")
def client(built):
    root, days = built
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = root.parent
    engine_mod._engine = Engine(root)
    from nivesh.api.main import app

    with TestClient(app) as c:
        c.days = days
        yield c
    engine_mod._engine, settings.data_dir = old_engine, old_dir


def test_sectors_endpoint_has_header_trajectories_and_narrative(client):
    body = client.get("/api/sectors").json()
    assert {"header", "trajectories"} <= body.keys()
    assert body["header"]["risk_posture"] in ("risk-on", "risk-off")
    assert 0 <= body["header"]["dispersion_percentile"] <= 100
    assert "Leadership:" in body["header"]["narrative"]
    assert any(r["sector"] == "Unclassified" for r in body["data"])
    ranks = [r["rank"] for r in body["data"] if r["rank"] is not None]
    assert ranks == sorted(ranks)
    for card in body["trajectories"]["improving"] + body["trajectories"]["deteriorating"]:
        assert len(card["rank_path"]) == 4 and card["rank_path"][-1] is not None


def test_sectors_as_of_is_a_single_state(client):
    target = client.days[300]
    body = client.get(f"/api/sectors?as_of={target}").json()
    assert {r["date"] for r in body["data"]} == {str(target)}
    assert date.fromisoformat(body["as_of"]) == target


def test_sector_events_endpoint_returns_historical_stats_observed_by_as_of(client):
    body = client.get("/api/sectors/events?sessions=250").json()
    assert body["data"]
    for row in body["data"]:
        hist = row["historical"]
        assert hist["occurrences"] >= 1
        assert hist["n_1w"] >= hist["n_1m"] >= hist["n_3m"]
    early = client.days[200]
    body_early = client.get(f"/api/sectors/events?as_of={early}&sessions=250").json()
    assert all(r["date"] <= str(early) for r in body_early["data"])
