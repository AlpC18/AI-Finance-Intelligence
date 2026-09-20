"""Broker trade-execution schemas + encrypted-at-rest credential model."""
from decimal import Decimal
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField, model_validator
from sqlmodel import Field, SQLModel
from app.core.money import Money

BrokerName = Literal["alpaca", "binance", "ibkr"]
OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
TimeInForce = Literal["day", "gtc", "ioc", "fok"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BrokerCredential(SQLModel, table=True):
    """Per-user broker API keys, stored ENCRYPTED at rest (Fernet ciphertext)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    broker: str = Field(index=True)  # e.g. "alpaca"
    api_key_enc: str  # Fernet ciphertext — plaintext is NEVER persisted
    api_secret_enc: str
    paper: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class CredentialCreate(BaseModel):
    broker: BrokerName = "alpaca"
    api_key: str = PField(min_length=1, max_length=256)
    api_secret: str = PField(min_length=1, max_length=256)
    paper: bool = True


class CredentialRead(BaseModel):
    """Safe projection — NEVER exposes secrets, only a last-4 fingerprint."""

    broker: str
    paper: bool
    created_at: datetime
    api_key_last4: str


class TradeRequest(BaseModel):
    """An execution request derived from an AI signal."""

    symbol: str = PField(min_length=1, max_length=20)
    broker: BrokerName = "alpaca"
    action: Literal["BUY", "SELL", "HOLD"]
    quantity: Money = PField(gt=0)
    confidence: Optional[float] = PField(default=None, ge=0.0, le=1.0)
    order_type: OrderType = "market"
    limit_price: Optional[Money] = PField(default=None, gt=0)
    # Protective exits are attached to the entry as a broker-side bracket.
    # They are persisted on the local order too, so reconciliation/audit can
    # explain the risk the trader actually accepted.
    stop_loss: Optional[Money] = PField(default=None, gt=0)
    take_profit: Optional[Money] = PField(default=None, gt=0)
    time_in_force: TimeInForce = "day"
    ai_context: Optional[str] = PField(default=None, max_length=4000)  # for audit log
    # Client-supplied de-duplication token. Two submissions carrying the same
    # key are the SAME intent: the second returns the first order instead of
    # opening a second position. Constrained to id-safe characters because it
    # is forwarded verbatim to the broker as client_order_id.
    idempotency_key: Optional[str] = PField(
        default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )

    @model_validator(mode="after")
    def _bracket_is_complete(self) -> "TradeRequest":
        if (self.stop_loss is None) != (self.take_profit is None):
            raise ValueError("stop_loss and take_profit must be provided together")
        return self


class OrderRequest(BaseModel):
    """Normalized order handed to a BrokerProvider (internal boundary type)."""

    symbol: str
    side: OrderSide
    quantity: Money
    order_type: OrderType = "market"
    limit_price: Optional[Money] = None
    stop_loss: Optional[Money] = None
    take_profit: Optional[Money] = None
    time_in_force: TimeInForce = "day"
    # Sent to the venue so the BROKER rejects a duplicate even if our own
    # pre-check loses a race. Always populated by TradeService.
    client_order_id: Optional[str] = None


class OrderAmendment(BaseModel):
    """A requested change to a working order.

    Both fields are optional but at least one must be present - an amendment
    that changes nothing is a round-trip to the venue that can only lose queue
    position, so it is rejected rather than forwarded.
    """

    quantity: Optional[Money] = PField(default=None, gt=0)
    limit_price: Optional[Money] = PField(default=None, gt=0)

    @model_validator(mode="after")
    def _at_least_one_change(self) -> "OrderAmendment":
        if self.quantity is None and self.limit_price is None:
            raise ValueError("quantity or limit_price must be provided")
        return self


class BrokerOrder(BaseModel):
    """Normalized broker order response."""

    id: str
    symbol: str
    side: OrderSide
    quantity: Money
    order_type: str
    status: str
    filled_quantity: Money = Decimal(0)
    filled_avg_price: Optional[Money] = None
    submitted_at: Optional[str] = None


class BrokerPosition(BaseModel):
    """Normalized broker-held position (for ledger drift reconciliation)."""

    symbol: str
    quantity: Money
    avg_entry_price: Money = Decimal(0)
    market_value: Money = Decimal(0)
    side: str = "long"


class BrokerAccount(BaseModel):
    account_number: str
    status: str
    currency: str = "USD"
    cash: Money = Decimal(0)
    buying_power: Money = Decimal(0)


class TradeResult(BaseModel):
    accepted: bool
    order: Optional[BrokerOrder] = None
    # True when the idempotency key matched an existing order: nothing new was
    # sent to the venue and the original order is echoed back.
    duplicate: bool = False
    message: str = ""
    disclaimer: str = "Paper trading — bu bir yatirim tavsiyesi degildir."
