"""The strict event schema. Model output that does not fit is rejected, never repaired by guessing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EventType = Literal[
    "results_beat",
    "results_miss",
    "order_win",
    "capex_announcement",
    "promoter_buy",
    "promoter_sell",
    "pledge_increase",
    "pledge_release",
    "rating_upgrade",
    "rating_downgrade",
    "management_change",
    "regulatory_action",
    "block_deal_buy",
    "block_deal_sell",
]

# Direction is fixed by the event type, so a model cannot flip it.
DIRECTION = {
    "results_beat": "positive",
    "results_miss": "negative",
    "order_win": "positive",
    "capex_announcement": "positive",
    "promoter_buy": "positive",
    "promoter_sell": "negative",
    "pledge_increase": "negative",
    "pledge_release": "positive",
    "rating_upgrade": "positive",
    "rating_downgrade": "negative",
    "management_change": "neutral",
    "regulatory_action": "negative",
    "block_deal_buy": "positive",
    "block_deal_sell": "negative",
}
EVENT_TYPES = tuple(DIRECTION)


class Magnitude(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value_cr: float | None = Field(default=None, ge=0)
    pct_of_revenue: float | None = Field(default=None, ge=0, le=1000)


class ExtractedEvent(BaseModel):
    """What the model may say about one announcement."""

    model_config = ConfigDict(extra="ignore")
    event_type: EventType
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=3, max_length=300)
    magnitude: Magnitude = Field(default_factory=Magnitude)

    @field_validator("summary")
    @classmethod
    def _one_line(cls, v: str) -> str:
        return " ".join(v.split())
