"""Alert rules, the alert inbox, a manual "run now", and the templated daily brief."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from nivesh import appdb
from nivesh.alerts import DEFAULT_RULES, KINDS, SEVERITY_RANK, build_brief, configured_channels, run_alerts
from nivesh.api.common import Envelope, envelope
from nivesh.backtest.spec import list_strategies
from nivesh.data.engine import get_engine

router = APIRouter(prefix="/api", tags=["alerts"])

Kind = Literal["regime_change", "market_event", "scan_entry", "watchlist", "strategy_signal"]


class AlertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Kind = "scan_entry"
    scan_id: str | None = None
    min_score: float = Field(default=0, ge=0, le=100)
    severity_min: str | None = None
    strategy_id: str | None = None

    @model_validator(mode="after")
    def _kind_requirements(self) -> AlertRequest:
        if self.kind == "scan_entry" and not self.scan_id:
            raise ValueError("scan_entry rules need a scan_id")
        if self.kind == "strategy_signal" and not self.strategy_id:
            raise ValueError("strategy_signal rules need a strategy_id")
        if self.severity_min is not None and self.severity_min.upper() not in SEVERITY_RANK:
            raise ValueError(f"severity_min must be one of {sorted(SEVERITY_RANK)}")
        return self


def _env(data: Any, calculation: str, **extra: Any) -> dict[str, Any]:
    engine = get_engine()
    return envelope(
        data, as_of=engine.data_through(), engine=engine, source="appdb.alerts", calculation=calculation, **extra
    )


def _public(rule: dict[str, Any]) -> dict[str, Any]:
    return {**rule, "params": json.loads(rule.get("params") or "{}")}


@router.get("/alerts", response_model=Envelope)
def rules() -> dict[str, Any]:
    channels = ["in-app inbox", *[c.name for c in configured_channels()]]
    return _env([_public(r) for r in appdb.list_alerts()], "local alert rules", kinds=list(KINDS), channels=channels)


@router.post("/alerts", response_model=Envelope)
def create_rule(request: AlertRequest) -> dict[str, Any]:
    if request.kind == "strategy_signal" and request.strategy_id not in {s.id for s in list_strategies()}:
        raise HTTPException(status_code=404, detail=f"unknown strategy '{request.strategy_id}'")
    params: dict[str, Any] = {}
    if request.kind == "market_event":
        params["severity_min"] = (request.severity_min or "WARNING").upper()
    if request.kind == "strategy_signal":
        params["strategy_id"] = request.strategy_id
    row = appdb.add_alert(request.name, request.scan_id, request.min_score, kind=request.kind, params=params)
    return _env(_public(row), "alert rule created")


@router.post("/alerts/defaults", response_model=Envelope)
def add_default_rules() -> dict[str, Any]:
    existing = {(r["kind"], r["name"]) for r in appdb.list_alerts()}
    created = [
        _public(appdb.add_alert(d["name"], None, 0, kind=d["kind"], params=d["params"]))
        for d in DEFAULT_RULES
        if (d["kind"], d["name"]) not in existing
    ]
    return _env(created, "default alert rules added (existing ones are left alone)")


@router.delete("/alerts/{rule_id}")
def delete_rule(rule_id: int) -> dict[str, Any]:
    if not appdb.delete_alert(rule_id):
        raise HTTPException(status_code=404, detail=f"unknown alert rule {rule_id}")
    return {"status": "deleted", "id": rule_id}


@router.post("/alerts/run", response_model=Envelope)
def run_now() -> dict[str, Any]:
    """Evaluate every rule against the latest session now (the daily pipeline does this automatically)."""
    return _env(run_alerts(get_engine()), "rules evaluated; duplicates are never re-sent")


@router.get("/alerts/inbox", response_model=Envelope)
def inbox(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    rows = appdb.list_alert_log(limit)
    for row in rows:
        row["delivered_to"] = json.loads(row["delivered_to"]) if row["delivered_to"] else []
        row["errors"] = json.loads(row["errors"]) if row["errors"] else []
    return _env(rows, "alerts delivered to the in-app inbox", unread=sum(1 for r in rows if not r["read"]))


@router.post("/alerts/inbox/read")
def mark_read() -> dict[str, Any]:
    return {"marked": appdb.mark_alerts_read()}


@router.get("/brief/text", response_model=Envelope)
def brief_text(as_of: Annotated[date | None, Query()] = None) -> dict[str, Any]:
    brief = build_brief(get_engine(), as_of)
    return envelope(
        brief, as_of=brief["as_of"], engine=get_engine(), source="analytics tables", calculation="templated daily brief"
    )
