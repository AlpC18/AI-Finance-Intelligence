"""Persisted broker orders + append-only trade audit log (compliance)."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel
from app.core.money import Money, MONEY_DIGITS, MONEY_PLACES

OrderStatus = Literal[
    "pending", "new", "accepted", "partially_filled",
    "pending_cancel", "pending_replace", "filled",
    "canceled", "expired", "rejected", "replaced", "done_for_day",
]
# "replaced" is terminal for THIS order id: the venue closed it and issued a
# new one in its place, so nothing further will ever happen under this id.
_TERMINAL = {
    "filled", "canceled", "expired", "rejected", "replaced", "done_for_day",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_terminal(status: str) -> bool:
    return status in _TERMINAL


def is_cancelable(status: str) -> bool:
    """Whether a cancel request against this order could still do anything.

    ``pending_cancel`` counts as cancelable: it records that we ASKED, not that
    the venue agreed, so a request that was dropped upstream must be retryable.
    Only a terminal order is genuinely past recall.
    """
    return not is_terminal(status)


class TradeOrder(SQLModel, table=True):
    """A submitted broker order tracked until it reconciles into the ledger.

    Named TradeOrder (table `tradeorder`) to avoid the reserved SQL word `order`.
    """

    # Declared here as well as in migration 0006 so a metadata-created schema
    # (tests, dev bootstrap) enforces the same duplicate-order guard as a
    # migrated one. A constraint that exists in only one of the two is worse
    # than none: it makes the tests pass in a shape production never has.
    __table_args__ = (
        UniqueConstraint(
            "user_id", "client_order_id", name="uq_tradeorder_user_client_order_id"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    broker: str = Field(default="alpaca", index=True)
    broker_order_id: str = Field(index=True)
    symbol: str = Field(index=True)
    side: str  # buy | sell
    quantity: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    order_type: str = "market"
    status: str = Field(default="pending", index=True)
    filled_quantity: Money = Field(default=Decimal(0), max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    filled_avg_price: Optional[Money] = Field(default=None, max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    stop_loss: Optional[Money] = Field(default=None, max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    take_profit: Optional[Money] = Field(default=None, max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    reconciled: bool = Field(default=False, index=True)  # written into the ledger?
    # De-duplication token, unique per user (see migration 0006). Nullable for
    # rows written before idempotency existed - those genuinely have no key.
    client_order_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class OrderRead(BaseModel):
    id: int
    symbol: str
    side: str
    quantity: Money
    status: str
    filled_quantity: Money
    filled_avg_price: Optional[Money] = None
    stop_loss: Optional[Money] = None
    take_profit: Optional[Money] = None
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
    # None for rows written before the column existed - a missing reading, not 0.0
    confidence: Optional[float] = Field(default=None)


class AuditLogRead(BaseModel):
    """One audit row, joined to the order it produced.

    The order-side fields are Optional because the audit log outlives the order:
    the trail is append-only, so a row must still render if its order is gone.
    """

    id: int
    order_id: str
    signal_type: str
    confidence: Optional[float] = None
    execution_timestamp: datetime
    raw_ai_context: str
    symbol: Optional[str] = None
    status: Optional[str] = None
    filled_quantity: Optional[Money] = None
    filled_avg_price: Optional[Money] = None


class AuditLogPage(BaseModel):
    """A page of the audit trail. `total` is the unpaginated match count."""

    total: int
    limit: int
    offset: int
    items: list[AuditLogRead] = []
