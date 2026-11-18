"""FastAPI app. Auth is off unless NIVESH_CLERK_JWKS_URL is set; without it keep the API bound to 127.0.0.1."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from nivesh import __version__
from nivesh.api.alerts import router as alerts_router
from nivesh.api.auth import auth_enabled, current_user, validate_auth_settings
from nivesh.api.copilot import router as copilot_router
from nivesh.api.intelligence import router as intelligence_router
from nivesh.api.lab import router as lab_router
from nivesh.api.market import router as market_router
from nivesh.api.me import router as me_router
from nivesh.api.news import router as news_router
from nivesh.api.paper import router as paper_router
from nivesh.api.sectors import router as sectors_router
from nivesh.api.stocks import router as stocks_router
from nivesh.data.engine import get_engine
from nivesh.userkeys import validate_master_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_auth_settings()
    validate_master_key()
    get_engine()  # mount views at startup, not on the first request
    scheduler = None
    if os.environ.get("NIVESH_SCHEDULER", "0") == "1":
        from nivesh.jobs.scheduler import start_scheduler

        scheduler = start_scheduler()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


# With sign-in on, the API map is not published (the docs page cannot send a session token anyway).
_docs = {} if not auth_enabled() else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Nivesh API", version=__version__, lifespan=lifespan, **_docs)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:15001", "http://127.0.0.1:15001"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class Health(BaseModel):
    status: str
    version: str


class Freshness(BaseModel):
    data_through: date | None
    views: dict[str, date | None]


class Column(BaseModel):
    name: str
    type: str


class ViewOut(BaseModel):
    name: str
    columns: list[Column]


@app.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", version=__version__)


@app.get("/api/data/freshness", response_model=Freshness, dependencies=[Depends(current_user)])
def freshness() -> Freshness:
    engine = get_engine()
    return Freshness(data_through=engine.data_through(), views=engine.freshness())


@app.get("/api/data/views", response_model=list[ViewOut], dependencies=[Depends(current_user)])
def views() -> list[ViewOut]:
    engine = get_engine()
    return [ViewOut(name=v, columns=[Column(name=c, type=t) for c, t in engine.schema(v)]) for v in engine.views()]


app.include_router(intelligence_router, dependencies=[Depends(current_user)])
app.include_router(market_router, dependencies=[Depends(current_user)])
app.include_router(lab_router, dependencies=[Depends(current_user)])
app.include_router(paper_router, dependencies=[Depends(current_user)])
app.include_router(alerts_router, dependencies=[Depends(current_user)])
app.include_router(copilot_router, dependencies=[Depends(current_user)])
app.include_router(sectors_router, dependencies=[Depends(current_user)])
app.include_router(stocks_router, dependencies=[Depends(current_user)])
app.include_router(news_router, dependencies=[Depends(current_user)])
app.include_router(me_router, dependencies=[Depends(current_user)])
