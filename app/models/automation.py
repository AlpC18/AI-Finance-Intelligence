"""Guarded rules that may execute only in the isolated paper simulator."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField, model_validator
from sqlmodel import Field, SQLModel

from app.core.money import Money, MONEY_DIGITS, MONEY_PLACES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


RuleTrigger = Literal["RSI_BELOW", "PRICE_BELOW", "DCA"]


class PaperAutomationRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    symbol: str = Field(index=True)
    trigger: str
    quantity: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    threshold: Optional[Money] = Field(default=None, max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    cooldown_minutes: int = Field(default=1440, ge=1)
    enabled: bool = Field(default=True, index=True)
    last_triggered_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)


class AutomationRuleCreate(BaseModel):
    symbol: str = PField(min_length=1, max_length=20)
    trigger: RuleTrigger
    quantity: Money = PField(gt=0)
    threshold: Optional[Money] = PField(default=None, gt=0)
    cooldown_minutes: int = PField(default=1440, ge=1, le=43_200)

    @model_validator(mode="after")
    def _threshold_matches_trigger(self) -> "AutomationRuleCreate":
        if self.trigger == "DCA" and self.threshold is not None:
            raise ValueError("DCA kuralinda threshold kullanilmaz")
        if self.trigger != "DCA" and self.threshold is None:
            raise ValueError("RSI/price kurallari threshold gerektirir")
        return self


class AutomationRuleRead(BaseModel):
    id: int
    symbol: str
    trigger: RuleTrigger
    quantity: Money
    threshold: Optional[Money]
    cooldown_minutes: int
    enabled: bool
    last_triggered_at: Optional[datetime]
    created_at: datetime


class AutomationRunResult(BaseModel):
    evaluated: int
    executed: int
    skipped: int
