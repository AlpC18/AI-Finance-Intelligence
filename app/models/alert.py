"""Alert schema: user-defined threshold monitored by the background engine."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField
from sqlmodel import Field, SQLModel

ConditionType = Literal[
    "RSI_BELOW", "PRICE_ABOVE", "PRICE_BELOW", "AI_SIGNAL_STRONG_BUY"
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Alert(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    symbol: str = Field(index=True)
    condition_type: str
    threshold_value: Optional[float] = None  # unused for AI_SIGNAL_STRONG_BUY
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    last_triggered_at: Optional[datetime] = None


class AlertCreate(BaseModel):
    symbol: str = PField(min_length=1, max_length=20)
    condition_type: ConditionType
    threshold_value: Optional[float] = None
    is_active: bool = True


class AlertRead(BaseModel):
    id: int
    symbol: str
    condition_type: ConditionType
    threshold_value: Optional[float]
    is_active: bool
    created_at: datetime
    last_triggered_at: Optional[datetime]
