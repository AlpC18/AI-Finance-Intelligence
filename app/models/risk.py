"""Kill-switch config, daily equity snapshots, and position-drift reporting."""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field as PField
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskSetting(SQLModel, table=True):
    """Per-user automated-trading risk config."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, unique=True, nullable=False)
    daily_loss_limit_pct: float = 0.0  # 0 -> automatic (drawdown) halt disabled
    manual_halt: bool = Field(default=False)  # operator kill-switch toggle
    # UTC date (ISO) on which the AUTOMATIC drawdown halt last fired.
    # Drawdown is a daily measure that resets with the opening snapshot, so a
    # date is exactly the right granularity: it makes the trip idempotent
    # within a day (the sweep must not re-flatten every five minutes) while
    # letting tomorrow trip on its own.
    auto_halt_tripped_on: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow)


class EquitySnapshot(SQLModel, table=True):
    """Opening equity captured once per UTC day, for daily-drawdown math."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    snapshot_date: str = Field(index=True)  # ISO date (UTC), e.g. "2026-08-21"
    opening_equity: float
    created_at: datetime = Field(default_factory=_utcnow)


class RiskConfigUpdate(BaseModel):
    daily_loss_limit_pct: float = PField(ge=0.0, le=100.0)


class RiskConfigRead(BaseModel):
    daily_loss_limit_pct: float


class KillSwitchToggle(BaseModel):
    enabled: bool  # True -> manually halt automated trading
    # A halt that only refuses NEW orders leaves resting ones filling into the
    # very drawdown the switch was pulled to stop, so flattening is the default.
    # Opt out when you want submissions frozen but existing working orders left
    # alone. Ignored when enabled=False - resuming never touches orders.
    cancel_open: bool = True


class KillSwitchStatus(BaseModel):
    halted: bool
    manual_halt: bool = False
    reason: str = ""  # "manual" | "daily_loss_limit" | ""
    daily_loss_limit_pct: float
    opening_equity: float
    current_equity: float
    drawdown_pct: float
    # How many working orders this request flattened. None on a plain status
    # read, which asked the venue for nothing - distinct from 0, which means we
    # tried and there was nothing left to cancel.
    canceled_orders: Optional[int] = None


class DriftItem(BaseModel):
    symbol: str
    broker_quantity: float
    ledger_quantity: float
    drift: float  # broker - ledger


class DriftReport(BaseModel):
    in_sync: bool
    items: list[DriftItem] = []
