"""Isolated paper-trading cash account and immutable simulated fills."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.core.money import Money, MONEY_DIGITS, MONEY_PLACES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaperAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, unique=True, nullable=False)
    currency: str = "USD"
    cash: Money = Field(default=Decimal("100000"), max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    starting_cash: Money = Field(default=Decimal("100000"), max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class PaperFill(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    symbol: str = Field(index=True)
    side: str
    quantity: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    reference_price: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    fill_price: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    fee: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    slippage_bps: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class PaperAccountCreate(BaseModel):
    starting_cash: Money = PField(default=Decimal("100000"), gt=0)


class PaperOrderRequest(BaseModel):
    symbol: str = PField(min_length=1, max_length=20)
    side: Literal["buy", "sell"]
    quantity: Money = PField(gt=0)


class PaperFillRead(BaseModel):
    id: int
    symbol: str
    side: str
    quantity: Money
    reference_price: Money
    fill_price: Money
    fee: Money
    slippage_bps: Money
    created_at: datetime


class PaperAccountRead(BaseModel):
    currency: str
    starting_cash: Money
    cash: Money
    buying_power: Money
    realized_pnl: Money
    fills: list[PaperFillRead] = []
