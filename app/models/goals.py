"""Persisted portfolio objectives; observations remain explicitly non-predictive."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField
from sqlmodel import Field, SQLModel

from app.core.money import Money, MONEY_DIGITS, MONEY_PLACES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioGoal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True, nullable=False)
    target_annual_return_pct: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    max_drawdown_pct: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    updated_at: datetime = Field(default_factory=_utcnow)


class PortfolioGoalUpdate(BaseModel):
    target_annual_return_pct: Money = PField(ge=-100, le=1000)
    max_drawdown_pct: Money = PField(ge=0, le=100)


class PortfolioGoalProgress(BaseModel):
    target_annual_return_pct: Money
    max_drawdown_pct: Money
    observed_annualized_return_pct: Money
    observed_max_drawdown_pct: Money
    status: Literal["on_track", "behind", "breached", "insufficient_history"]
    note: str
