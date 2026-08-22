"""Broker trade-execution schemas + encrypted-at-rest credential model."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField
from sqlmodel import Field, SQLModel

BrokerName = Literal["alpaca"]
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
    action: Literal["BUY", "SELL", "HOLD"]
    quantity: float = PField(gt=0)
    confidence: Optional[float] = PField(default=None, ge=0.0, le=1.0)
    order_type: OrderType = "market"
    limit_price: Optional[float] = PField(default=None, gt=0)
    time_in_force: TimeInForce = "day"
    ai_context: Optional[str] = PField(default=None, max_length=4000)  # for audit log


class OrderRequest(BaseModel):
    """Normalized order handed to a BrokerProvider (internal boundary type)."""

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = "market"
    limit_price: Optional[float] = None
    time_in_force: TimeInForce = "day"


class BrokerOrder(BaseModel):
    """Normalized broker order response."""

    id: str
    symbol: str
    side: OrderSide
    quantity: float
    order_type: str
    status: str
    filled_quantity: float = 0.0
    filled_avg_price: Optional[float] = None
    submitted_at: Optional[str] = None


class BrokerPosition(BaseModel):
    """Normalized broker-held position (for ledger drift reconciliation)."""

    symbol: str
    quantity: float
    avg_entry_price: float = 0.0
    market_value: float = 0.0
    side: str = "long"


class BrokerAccount(BaseModel):
    account_number: str
    status: str
    currency: str = "USD"
    cash: float = 0.0
    buying_power: float = 0.0


class TradeResult(BaseModel):
    accepted: bool
    order: Optional[BrokerOrder] = None
    message: str = ""
    disclaimer: str = "Paper trading — bu bir yatirim tavsiyesi degildir."
