"""Strategy definition (YAML) and validation.

A strategy is plain data: which stocks may be traded, when to enter, how to rank candidates, how to size positions and
when to exit. Expressions are compiled through `nivesh.rules` so a strategy file can never run arbitrary SQL.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nivesh.config import settings
from nivesh.rules import compile_expr

_RANK = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(asc|desc)\s*$", re.IGNORECASE)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Universe(_Model):
    filters: str = "close >= 30 and avg_turnover20_lacs >= 100 and history_sessions >= 200"


class Entry(_Model):
    when: str
    rank_by: list[str] = Field(default_factory=lambda: ["ret_63d desc"])
    max_new_per_day: int = Field(3, ge=1)

    @field_validator("rank_by")
    @classmethod
    def _rank_syntax(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _RANK.match(item):
                raise ValueError(f"rank_by item must look like '<column> asc|desc', got {item!r}")
        return value


class Portfolio(_Model):
    initial_capital: float = Field(10_000_000.0, gt=0)
    max_positions: int = Field(10, ge=1)
    risk_per_trade_pct: float = Field(1.0, gt=0, le=10)
    max_position_pct: float = Field(15.0, gt=0, le=100)
    max_sector_pct: float = Field(40.0, gt=0, le=100)


class Exit(_Model):
    hard_stop_pct: float = Field(3.0, gt=0, lt=50)
    breakeven_trigger_pct: float | None = Field(8.0, gt=0)
    trail_sma: Literal[20, 50] | None = 20
    time_stop_days: int | None = Field(40, ge=1)


class Costs(_Model):
    """Per-side costs in basis points of traded value. STT applies to delivery trades on both sides."""

    brokerage_bps: float = Field(3.0, ge=0)
    stt_bps: float = Field(10.0, ge=0)
    exchange_gst_bps: float = Field(0.5, ge=0)
    slippage_bps: float = Field(10.0, ge=0)

    @property
    def fee_rate(self) -> float:
        return (self.brokerage_bps + self.stt_bps + self.exchange_gst_bps) / 1e4

    @property
    def slippage(self) -> float:
        return self.slippage_bps / 1e4


class Strategy(_Model):
    name: str
    version: str
    label: str = ""
    family: str = ""
    status: Literal["research", "paper", "live", "retired"] = "research"
    universe: Universe = Field(default_factory=Universe)
    regime_block: list[str] = Field(default_factory=list)
    entry: Entry
    portfolio: Portfolio = Field(default_factory=Portfolio)
    exit: Exit = Field(default_factory=Exit)
    costs: Costs = Field(default_factory=Costs)

    @property
    def id(self) -> str:
        return f"{self.name}_{self.version}"

    def compile(self, columns: list[str]) -> dict[str, str]:
        """SQL fragments for the given feature columns; raises `RuleError` on unknown names."""
        order = []
        for item in self.entry.rank_by:
            m = _RANK.match(item)
            assert m is not None
            col, direction = m.group(1), m.group(2).upper()
            order.append(f"{compile_expr(col, columns)} {direction} NULLS LAST")
        return {
            "universe": compile_expr(self.universe.filters, columns),
            "entry": compile_expr(self.entry.when, columns),
            "order": ", ".join(order) + ", symbol",
        }

    def with_overrides(self, overrides: dict[str, Any]) -> Strategy:
        """Copy with dotted-path overrides, e.g. {'exit.hard_stop_pct': 4, 'costs.slippage_bps': 20}."""
        data = self.model_dump()
        for dotted, value in overrides.items():
            node = data
            *parents, leaf = dotted.split(".")
            for part in parents:
                node = node[part]
            if leaf not in node:
                raise KeyError(dotted)
            node[leaf] = value
        return Strategy.model_validate(data)


def strategies_dir() -> Path:
    return settings.context_dir / "strategies"


def load_strategy(path: Path) -> Strategy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return Strategy.model_validate(raw)
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc


def list_strategies(directory: Path | None = None) -> list[Strategy]:
    root = directory or strategies_dir()
    found = [load_strategy(p) for p in sorted(root.glob("*.yaml"))] if root.exists() else []
    ids = [s.id for s in found]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate strategy ids in " + str(root))
    return found


def get_strategy(strategy_id: str, directory: Path | None = None) -> Strategy:
    for strategy in list_strategies(directory):
        if strategy.id == strategy_id:
            return strategy
    raise KeyError(strategy_id)
