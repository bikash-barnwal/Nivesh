"""Stock panel: safety filter, lifecycle, gauges, events and as-of behaviour."""

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
from tests.synth import make_dataset


def _write(root, name, frame: pl.DataFrame) -> None:
    out = root / name
    out.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(out / f"{name}.parquet")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("panel")
    days = make_dataset(root)
    last = days[-1]
    # S002: upper circuit on the last three sessions; S003: 5% price band; everything else unrestricted.
    _write(
        root,
        "circuit_hits",
        pl.DataFrame(
            {
                "symbol": ["S002"] * 3,
                "series": ["EQ"] * 3,
                "date": days[-3:],
                "side": ["H"] * 3,
            }
        ),
    )
    _write(
        root,
        "price_bands",
        pl.DataFrame(
            {
                "symbol": ["S002", "S003", "S001"],
                "series": ["EQ"] * 3,
                "date": [last, last, last],
                "band_pct": [20.0, 5.0, 20.0],
                "band_raw": ["20", "5", "20"],
            }
        ),
    )
    _write(
        root,
        "corporate_actions",
        pl.DataFrame(
            {
                "symbol": ["S001"],
                "ex_date": [days[100]],
                "record_date": [days[100]],
                "purpose": ["BONUS 1:1"],
                "price_factor": [0.5],
                "factor_method": ["purpose"],
            }
        ),
    )
    _write(
        root,
        "security_master",
        pl.DataFrame(
            {"symbol": ["S001"], "company": ["Sample Ltd"], "isin": ["INE000000001"], "listing_date": [days[0]]}
        ),
    )
    analytics.build_analytics(parquet_dir=root)
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = root.parent
    engine_mod._engine = Engine(root)
    from nivesh.api.main import app

    with TestClient(app) as c:
        c.days = days
        yield c
    engine_mod._engine, settings.data_dir = old_engine, old_dir


def _panel(client, symbol="S001", **params):
    return client.get(f"/api/stocks/{symbol}/panel", params=params)


def test_panel_structure_and_metadata(client):
    body = _panel(client).json()
    data = body["data"]
    assert {
        "meta",
        "price",
        "features",
        "lifecycle",
        "gauges",
        "relative_strength",
        "safety_stage1",
        "events",
    } <= data.keys()
    assert data["meta"]["company"] == "Sample Ltd" and data["meta"]["sector"] == "Healthcare"
    assert data["events"]["corporate_actions"][0]["purpose"] == "BONUS 1:1"
    assert body["as_of"] == str(client.days[-1])


def test_gauges_are_bounded_and_named_from_yaml(client):
    cfg = yaml.safe_load((settings.context_dir / "stock_panel.yaml").read_text(encoding="utf-8"))
    gauges = _panel(client).json()["data"]["gauges"]
    assert [g["name"] for g in gauges] == [g["name"] for g in cfg["gauges"]]
    assert all(g["value"] is None or 0 <= g["value"] <= 100 for g in gauges)
    assert any(g["value"] is not None for g in gauges)


def test_relative_strength_is_a_percentile(client):
    rs = _panel(client).json()["data"]["relative_strength"]
    assert 0 <= rs["rs_rank"] <= 100
    assert rs["rs_500"] > 0


def test_lifecycle_state_matches_stage_and_dwell_counts_sessions(client):
    data = _panel(client).json()["data"]
    life, stage = data["lifecycle"], data["features"]["stage"]
    assert life["state"] in {"DECLINE", "TOPPING", "LEAD", "ADVANCE", "ACCUMULATION", "BASE"}
    assert life["dwell_sessions"] >= 1
    stage_to_states = {4: {"DECLINE"}, 3: {"TOPPING"}, 2: {"LEAD", "ADVANCE"}, 1: {"ACCUMULATION", "BASE"}}
    assert life["state"] in stage_to_states[stage]
    # a symbol with fewer sessions than the stage needs has no state yet
    early = _panel(client, as_of=str(client.days[100])).json()["data"]["lifecycle"]
    assert early["state"] == "UNKNOWN"


def test_clean_symbol_passes_every_safety_check(client):
    safety = _panel(client, "S001").json()["data"]["safety_stage1"]
    assert safety["overall"] == "PASS"
    assert {c["status"] for c in safety["checks"]} == {"PASS"}


def test_circuit_streak_and_days_fail_the_filter(client):
    checks = {c["check"]: c for c in _panel(client, "S002").json()["data"]["safety_stage1"]["checks"]}
    assert checks["Circuit streak"]["value"] == 3 and checks["Circuit streak"]["status"] == "FAIL"
    assert checks["Circuit days (20d)"]["value"] == 3 and checks["Circuit days (20d)"]["status"] == "PASS"
    assert _panel(client, "S002").json()["data"]["safety_stage1"]["overall"] == "FAIL"
    assert len(_panel(client, "S002").json()["data"]["events"]["circuit_hits"]) == 3


def test_narrow_price_band_fails(client):
    checks = {c["check"]: c for c in _panel(client, "S003").json()["data"]["safety_stage1"]["checks"]}
    assert checks["Price band"]["value"] == 5.0 and checks["Price band"]["status"] == "FAIL"


def test_missing_band_data_is_unknown_not_pass(client, monkeypatch):
    engine = engine_mod.get_engine()
    original = Engine.views
    monkeypatch.setattr(engine, "views", lambda: [v for v in original(engine) if v != "price_bands"])
    safety = _panel(client, "S001").json()["data"]["safety_stage1"]
    band = next(c for c in safety["checks"] if c["check"] == "Price band")
    assert band["status"] == "UNKNOWN"
    assert safety["overall"] == "INCOMPLETE"


def test_as_of_returns_that_session_and_unknown_symbol_404s(client):
    target = client.days[300]
    assert _panel(client, as_of=str(target)).json()["as_of"] == str(target)
    assert date.fromisoformat(_panel(client, as_of="2030-01-01").json()["as_of"]) == client.days[-1]
    assert _panel(client, "NOPE").status_code == 404
