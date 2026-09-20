"""Trading as Git schemas (OpenAlice).

Provides git-like staging, diffing, cryptographic commit bundling, and
human-in-the-loop review guardrails for algorithmic executions.
"""
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from sqlmodel import Field as SField, SQLModel


class StagedTradeIntent(SQLModel, table=True):
    id: Optional[int] = SField(default=None, primary_key=True)
    user_id: int = SField(foreign_key="user.id", index=True, nullable=False)
    symbol: str = SField(index=True)
    action: str  # BUY, SELL
    quantity: float
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    ai_thesis_provenance: str
    status: str = SField(default="STAGED", index=True)  # STAGED, COMMITTED, PUSHED, DISCARDED
    staged_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


class TradeCommitBundle(SQLModel, table=True):
    id: Optional[int] = SField(default=None, primary_key=True)
    user_id: int = SField(foreign_key="user.id", index=True, nullable=False)
    commit_hash: str = SField(index=True)  # SHA-256
    commit_message: str
    orders_count: int
    portfolio_delta_summary: str
    status: str = SField(default="COMMITTED", index=True)  # COMMITTED, PUSHED_TO_BROKER, REVERTED
    committed_at: datetime = SField(default_factory=lambda: datetime.now(timezone.utc))


class StageTradeRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    action: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    ai_thesis_provenance: str = Field(min_length=5)


class PortfolioDiffView(BaseModel):
    staged_intents: List[StagedTradeIntent]
    current_holdings: Dict[str, float]
    target_holdings_after_push: Dict[str, float]
    estimated_capital_outlay_usd: float
    risk_impact_summary: str


class CommitTradeRequest(BaseModel):
    commit_message: str = Field(min_length=3, max_length=200)
    author_signature: Optional[str] = None


class PushCommitResponse(BaseModel):
    commit_hash: str
    executed_orders: int
    broker_mode: str
    execution_status: str
    push_timestamp: str
