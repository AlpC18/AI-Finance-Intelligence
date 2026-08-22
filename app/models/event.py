"""Event-driven intelligence schemas: categorized, impact-scored market events."""
from typing import Literal

from pydantic import BaseModel, Field

EventCategory = Literal["LEGAL_RISK", "MACRO_POLICY", "TECH_CATALYST", "EXECUTIVE_SENTIMENT"]
Orientation = Literal["BULLISH", "BEARISH", "HIGH_VOLATILITY", "NEUTRAL"]


class MarketEvent(BaseModel):
    title: str
    source: str = ""
    link: str = ""
    published: str = ""
    category: EventCategory
    severity: int = Field(ge=1, le=10)   # 1 (noise) .. 10 (market-moving)
    orientation: Orientation
    blocking: bool = False               # true -> AI must HOLD / DO NOT TRADE


class EventResponse(BaseModel):
    events: list[MarketEvent]
