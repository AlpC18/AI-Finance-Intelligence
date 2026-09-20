"""Portfolio performance over time, read from the daily equity snapshots.

The snapshots were already being written for the drawdown kill-switch and read
by nothing else. They are the only record of what the portfolio was worth on a
past day - the ledger can reconstruct *positions* historically but not their
marked value, because past quotes are not stored. So this is the honest source
for an equity curve, with one caveat the report carries explicitly: snapshots
are captured lazily, so quiet days leave gaps. Gaps are reported as unobserved
(`sparse`), never interpolated into a flat line that would read as "held".
"""
from __future__ import annotations

from decimal import Decimal

from sqlmodel import Session, select

from app.core.money import HUNDRED, ZERO, to_decimal

from app.models.performance import EquityCurvePoint, PerformanceReport
from app.models.risk import EquitySnapshot


class PerformanceService:
    def __init__(self, portfolio) -> None:
        self._portfolio = portfolio

    async def report(
        self, session: Session, user_id: int, limit: int = 365
    ) -> PerformanceReport:
        points = self._series(session, user_id, limit)
        current = await self._current_equity(session, user_id)

        if not points:
            # No history yet: report today's standing without inventing a curve.
            return PerformanceReport(
                points=[], covered_days=0, span_days=0, sparse=False,
                start_equity=current, current_equity=current,
            )

        start = points[0].equity
        # The live figure is the truest "now"; append it so the curve ends today.
        marked = [p.equity for p in points] + [current]
        daily = _daily_returns(marked)

        return PerformanceReport(
            points=points,
            covered_days=len(points),
            span_days=_span_days(points),
            sparse=len(points) < _span_days(points),
            start_equity=round(start, 2),
            current_equity=round(current, 2),
            absolute_return=round(current - start, 2),
            return_pct=round((current - start) / start * HUNDRED, 4) if start else ZERO,
            max_drawdown_pct=_max_drawdown_pct(marked),
            best_day_pct=round(max(daily), 4) if daily else None,
            worst_day_pct=round(min(daily), 4) if daily else None,
        )

    def _series(self, session: Session, user_id: int, limit: int) -> list[EquityCurvePoint]:
        rows = session.exec(
            select(EquitySnapshot)
            .where(EquitySnapshot.user_id == user_id)
            .order_by(EquitySnapshot.snapshot_date.desc())
            .limit(limit)
        ).all()
        # Query newest-first so `limit` keeps the most RECENT window, then
        # reverse: a curve must read oldest to newest.
        return [
            EquityCurvePoint(date=r.snapshot_date, equity=round(r.opening_equity, 2))
            for r in reversed(list(rows))
        ]

    async def _current_equity(self, session: Session, user_id: int) -> Decimal:
        report = await self._portfolio.risk_report(session, user_id)
        return round(report.total_value + report.total_realized_pnl, 2)


def _span_days(points: list[EquityCurvePoint]) -> int:
    """Calendar days covered, inclusive. Dates are ISO so they sort lexically."""
    from datetime import date

    first, last = date.fromisoformat(points[0].date), date.fromisoformat(points[-1].date)
    return (last - first).days + 1


def _daily_returns(values: list[Decimal]) -> list[Decimal]:
    return [
        (curr - prev) / prev * HUNDRED
        for prev, curr in zip(values, values[1:])
        if prev
    ]


def _max_drawdown_pct(values: list[Decimal]) -> Decimal:
    """Largest peak-to-trough decline, as a positive percentage.

    Exact throughout: this figure is compared against the configured loss
    limit, so an error here is the difference between halting an account and
    not halting it.
    """
    peak, worst = None, ZERO
    for value in values:
        if peak is None or value > peak:
            peak = value
        if peak:
            worst = min(worst, (value - peak) / peak * HUNDRED)
    return round(abs(worst), 4)
