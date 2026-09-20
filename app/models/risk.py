"""Kill-switch config, daily equity snapshots, and position-drift reporting."""
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field as PField
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel
from app.core.money import Money, MONEY_DIGITS, MONEY_PLACES


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskSetting(SQLModel, table=True):
    """Per-user automated-trading risk config."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, unique=True, nullable=False)
    daily_loss_limit_pct: Money = Field(default=Decimal(0), max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)  # 0 -> automatic (drawdown) halt disabled
    max_daily_trades: int = Field(default=0, ge=0)  # 0 -> disabled
    max_position_weight_pct: Money = Field(default=Decimal(0), max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)  # 0 -> disabled
    require_protective_stop: bool = Field(default=False)
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

    __table_args__ = (
        UniqueConstraint(
            "user_id", "snapshot_date", name="uq_equitysnapshot_user_date"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    snapshot_date: str = Field(index=True)  # ISO date (UTC), e.g. "2026-08-21"
    opening_equity: Money = Field(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    created_at: datetime = Field(default_factory=_utcnow)


class RiskConfigUpdate(BaseModel):
    daily_loss_limit_pct: Money = PField(ge=0, le=100)
    max_daily_trades: int = PField(default=0, ge=0, le=10_000)
    max_position_weight_pct: Money = PField(default=Decimal(0), ge=0, le=100)
    require_protective_stop: bool = False


class RiskConfigRead(BaseModel):
    daily_loss_limit_pct: Money
    max_daily_trades: int = 0
    max_position_weight_pct: Money = Decimal(0)
    require_protective_stop: bool = False


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
    daily_loss_limit_pct: Money
    opening_equity: Money
    current_equity: Money
    drawdown_pct: Money
    # How many working orders this request flattened. None on a plain status
    # read, which asked the venue for nothing - distinct from 0, which means we
    # tried and there was nothing left to cancel.
    canceled_orders: Optional[int] = None


class DriftItem(BaseModel):
    symbol: str
    broker_quantity: Money
    ledger_quantity: Money
    drift: Money  # broker - ledger


class DriftReport(BaseModel):
    in_sync: bool
    items: list[DriftItem] = []
