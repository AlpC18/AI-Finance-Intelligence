"""Social Copy-Trading & Strategy Marketplace schemas.

Allows algorithmic strategy creators to publish verified track-record strategies
and subscribers to mirror trades with personalized risk allocation and paper/live gating.
"""
from datetime import datetime, timezone
from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from sqlmodel import Field as SField, SQLModel

StrategyCategory = Literal[
    "momentum",
    "mean_reversion",
    "macro_trend",
    "options_income",
    "dca_value",
    "ai_ensemble",
]


class StrategyListingBase(SQLModel):
    name: str = SField(index=True)
    description: str
    category: str = SField(default="momentum", index=True)
    is_public: bool = SField(default=True, index=True)
    monthly_fee_usd: float = SField(default=0.0)
    sharpe_ratio: float = SField(default=1.5)
    sortino_ratio: float = SField(default=1.8)
    win_rate_pct: float = SField(default=62.5)
    max_drawdown_pct: float = SField(default=8.2)
    cagr_pct: float = SField(default=28.4)
    total_trades: int = SField(default=120)
    is_verified: bool = SField(default=True)


class StrategyListing(StrategyListingBase, table=True):
    id: Optional[int] = SField(default=None, primary_key=True)
    publisher_id: int = SField(foreign_key="user.id", index=True, nullable=False)
    subscribers_count: int = SField(default=0)
    created_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


class StrategySubscription(SQLModel, table=True):
    id: Optional[int] = SField(default=None, primary_key=True)
    subscriber_id: int = SField(foreign_key="user.id", index=True, nullable=False)
    strategy_id: int = SField(foreign_key="strategylisting.id", index=True, nullable=False)
    allocated_capital: float = SField(default=1000.0, gt=0.0)
    copy_mode: str = SField(default="paper")  # strictly paper or live
    is_active: bool = SField(default=True, index=True)
    created_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


class CopyTradeRecord(SQLModel, table=True):
    id: Optional[int] = SField(default=None, primary_key=True)
    subscription_id: int = SField(foreign_key="strategysubscription.id", index=True, nullable=False)
    subscriber_id: int = SField(foreign_key="user.id", index=True, nullable=False)
    strategy_id: int = SField(foreign_key="strategylisting.id", index=True, nullable=False)
    symbol: str = SField(index=True)
    action: str  # BUY, SELL
    quantity: float
    execution_price: float
    status: str = SField(default="EXECUTED")
    created_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


class CreateStrategyListingRequest(BaseModel):
    name: str = Field(min_length=3, max_length=50)
    description: str = Field(min_length=10, max_length=1000)
    category: StrategyCategory = "momentum"
    monthly_fee_usd: float = Field(default=0.0, ge=0.0)
    is_public: bool = True


class SubscribeStrategyRequest(BaseModel):
    strategy_id: int
    allocated_capital: float = Field(default=1000.0, gt=0.0)
    copy_mode: Literal["paper", "live"] = "paper"


class BroadcastSignalRequest(BaseModel):
    strategy_id: int
    symbol: str
    action: Literal["BUY", "SELL"]
    target_allocation_pct: float = Field(default=5.0, ge=0.5, le=100.0)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class LeaderboardItem(BaseModel):
    strategy_id: int
    name: str
    category: str
    publisher_name: str
    sharpe_ratio: float
    win_rate_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    subscribers_count: int
    is_verified: bool
