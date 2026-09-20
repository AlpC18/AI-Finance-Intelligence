"""Append-only account activity events for the live timeline."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField
from sqlmodel import Field, SQLModel


ActivityKind = Literal[
    "order", "fill", "risk", "automation", "watchlist", "security", "configuration",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActivityEvent(SQLModel, table=True):
    """Immutable user-visible event; payload is JSON to stay database portable."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    kind: str = Field(index=True)
    severity: int = Field(default=1, ge=1, le=5)
    summary: str
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class ActivityEventRead(BaseModel):
    id: int
    kind: ActivityKind
    severity: int
    summary: str
    payload: dict = PField(default_factory=dict)
    created_at: datetime


class ActivityPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ActivityEventRead] = PField(default_factory=list)
