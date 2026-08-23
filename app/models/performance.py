"""Equity-curve schemas built from the daily EquitySnapshot series.

Snapshots are captured lazily - one per UTC day, on the first execution or
kill-switch check that day. A user who does not trade for a week leaves no
rows for that week, so the series is deliberately sparse and the report says
so (`sparse`, `covered_days`) instead of interpolating a flat line that would
read as "held steady" when the truth is "not observed".
"""
from typing import Optional

from pydantic import BaseModel


class EquityCurvePoint(BaseModel):
    date: str  # ISO date (UTC)
    equity: float


class PerformanceReport(BaseModel):
    points: list[EquityCurvePoint] = []
    covered_days: int  # days that actually have a snapshot
    span_days: int  # calendar days from first to last snapshot
    sparse: bool  # covered_days < span_days -> gaps are unobserved, not flat
    start_equity: float = 0.0
    current_equity: float = 0.0
    absolute_return: float = 0.0
    return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    best_day_pct: Optional[float] = None
    worst_day_pct: Optional[float] = None
    basis: str = "daily opening equity: marked positions + realized P&L"
