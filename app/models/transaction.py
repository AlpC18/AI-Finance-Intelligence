"""Transaction ledger: the single source of truth for holdings and P&L."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField
from sqlmodel import Field, SQLModel
from app.core.money import Money, MONEY_DIGITS, MONEY_PLACES

Action = Literal["BUY", "SELL"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    symbol: str = Field(index=True)
    action: str = Field(index=True)  # BUY | SELL
    quantity: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    price: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    timestamp: datetime = Field(default_factory=_utcnow, index=True)


class TransactionCreate(BaseModel):
    symbol: str = PField(min_length=1, max_length=20)
    action: Action
    quantity: Money = PField(gt=0)
    price: Money = PField(gt=0)


class TransactionRead(BaseModel):
    id: int
    symbol: str
    action: Action
    quantity: Money
    price: Money
    timestamp: datetime


class Holding(BaseModel):
    """Reconstructed net position for one symbol (avg-cost basis)."""

    symbol: str
    quantity: Money
    avg_cost: Money
    realized_pnl: Money
