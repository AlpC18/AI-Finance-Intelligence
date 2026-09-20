"""Per-user symbols under active research and alerting."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field as PField
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.core.money import MONEY_DIGITS, MONEY_PLACES, Money


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WatchlistItem(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    symbol: str = Field(index=True)
    note: str = ""
    target_price: Optional[Money] = Field(
        default=None, max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES
    )
    created_at: datetime = Field(default_factory=_utcnow)


class WatchlistCreate(BaseModel):
    symbol: str = PField(min_length=1, max_length=20)
    note: str = PField(default="", max_length=500)
    target_price: Optional[Money] = PField(default=None, gt=Decimal("0"))


class WatchlistUpdate(BaseModel):
    """Only explicitly supplied fields are changed."""

    note: Optional[str] = PField(default=None, max_length=500)
    target_price: Optional[Money] = PField(default=None, gt=Decimal("0"))


class WatchlistRead(BaseModel):
    id: int
    symbol: str
    note: str
    target_price: Optional[Money]
    created_at: datetime
