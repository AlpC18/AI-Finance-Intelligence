"""Persisted broker orders + append-only trade audit log (compliance)."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel
from sqlmodel import Field, SQLModel

OrderStatus = Literal[
    "pending", "new", "accepted", "partially_filled", "filled",
    "canceled", "expired", "rejected", "done_for_day",
]
_TERMINAL = {"filled", "canceled", "expired", "rejected", "done_for_day"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_terminal(status: str) -> bool:
    return status in _TERMINAL


class TradeOrder(SQLModel, table=True):
    """A submitted broker order tracked until it reconciles into the ledger.

    Named TradeOrder (table `tradeorder`) to avoid the reserved SQL word `order`.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    broker: str = Field(default="alpaca", index=True)
    broker_order_id: str = Field(index=True)
    symbol: str = Field(index=True)
    side: str  # buy | sell
    quantity: float
    order_type: str = "market"
    status: str = Field(default="pending", index=True)
    filled_quantity: float = 0.0
    filled_avg_price: Optional[float] = None
    reconciled: bool = Field(default=False, index=True)  # written into the ledger?
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class OrderRead(BaseModel):
    id: int
    symbol: str
    side: str
    quantity: float
    status: str
    filled_quantity: float
    filled_avg_price: Optional[float] = None
    reconciled: bool
    created_at: datetime
    updated_at: datetime


class TradeAuditLog(SQLModel, table=True):
    """Append-only audit trail. Never updated or deleted in normal operation."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    order_id: str = Field(index=True)  # broker order id
    signal_type: str  # BUY | SELL
    execution_timestamp: datetime = Field(default_factory=_utcnow, index=True)
    raw_ai_context: str = ""


class AuditLogRead(BaseModel):
    order_id: str
    signal_type: str
    execution_timestamp: datetime
    raw_ai_context: str
