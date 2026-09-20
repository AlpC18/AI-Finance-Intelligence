"""Signal scorecard schemas — did following the AI signals actually make money?

Outcomes are marked to market against the latest quote, not round-tripped
against a closing sell. Most signals never close, so waiting for a realized
round trip would score almost nothing; marking to market scores every filled
signal at the cost of being a *current* reading rather than a settled one.
That tradeoff is stated on the report itself (`basis`) so nobody mistakes an
unrealized number for booked P&L.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from app.core.money import Money


class SignalOutcome(BaseModel):
    """One filled signal, scored against where the symbol trades now."""

    order_id: str
    symbol: str
    signal_type: str  # BUY | SELL
    confidence: Optional[float] = None
    executed_at: datetime
    fill_price: Money
    quantity: Money
    reference_price: Money  # latest quote used to mark the position
    return_pct: float  # signed FOR the signal: a SELL profits when price falls
    pnl: Money  # return_pct applied to the filled notional
    correct: bool  # did the market move the way the signal said?


class ConfidenceBucket(BaseModel):
    """Aggregate over signals whose confidence fell in [lower, upper)."""

    label: str  # e.g. "0.60-0.70"
    lower: float
    upper: float
    signals: int
    hit_rate_pct: float
    avg_return_pct: float
    total_pnl: Money


class SignalScorecard(BaseModel):
    evaluated: int  # filled signals that could be scored
    skipped_unfilled: int  # signals with no fill yet - not scoreable, not failures
    skipped_unpriced: int  # symbols the market feed could not quote
    hit_rate_pct: float
    avg_return_pct: float
    total_pnl: Money
    buckets: list[ConfidenceBucket] = []
    best: Optional[SignalOutcome] = None
    worst: Optional[SignalOutcome] = None
    calibration_note: str = ""
    degraded: bool = False  # some or all quotes unavailable
    basis: str = "mark-to-market against the latest quote; unrealized"
    disclaimer: str = "Gecmis performans gelecegi garanti etmez."
