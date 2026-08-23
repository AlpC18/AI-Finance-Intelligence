"""Scores executed AI signals against what the market actually did next.

The platform's core claim is that its signals are worth acting on. Nothing
measured whether that was true: the backtest engine scores hypotheses, and the
audit log recorded live signals without ever grading them. This closes that
loop using data already persisted - the audit row (signal + confidence) joined
to the order it produced (fill price + quantity).

Scoring rule: a signal is *correct* when the market moved the way it said.
A BUY profits when the price rises; a SELL profits when it falls, so the SELL
return is negated rather than compared against a separate threshold. Signals
that never filled are not failures - they never took a position - so they are
counted separately and excluded from the hit rate rather than scored as wrong.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from app.models.order import TradeAuditLog, TradeOrder
from app.models.signal import ConfidenceBucket, SignalOutcome, SignalScorecard

logger = logging.getLogger("signal_performance")

# Confidence buckets. Signals below 0.5 are rare (the gate usually blocks them)
# so the lowest bucket is deliberately wide.
_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01),
)
_EPS = 1e-9


class SignalPerformanceService:
    def __init__(self, market) -> None:
        self._market = market

    async def scorecard(
        self, session: Session, user_id: int, symbol: Optional[str] = None
    ) -> SignalScorecard:
        filled, unfilled = self._partition(session, user_id, symbol)
        prices, degraded = await self._quote_all({o.symbol for _, o in filled})

        outcomes: list[SignalOutcome] = []
        unpriced = 0
        for entry, order in filled:
            price = prices.get(order.symbol)
            if price is None or price <= 0:
                unpriced += 1
                continue
            outcomes.append(_score(entry, order, price))

        return _aggregate(outcomes, unfilled, unpriced, degraded)

    def _partition(
        self, session: Session, user_id: int, symbol: Optional[str]
    ) -> tuple[list[tuple[TradeAuditLog, TradeOrder]], int]:
        """Split this user's signals into scoreable (filled) and not-yet-filled."""
        conditions = [TradeAuditLog.user_id == user_id]
        if symbol:
            conditions.append(TradeOrder.symbol == symbol.upper().strip())

        rows = session.exec(
            select(TradeAuditLog, TradeOrder)
            .join(
                TradeOrder,
                (TradeOrder.broker_order_id == TradeAuditLog.order_id)
                & (TradeOrder.user_id == TradeAuditLog.user_id),
            )
            .where(*conditions)
        ).all()

        filled, unfilled = [], 0
        for entry, order in rows:
            if (
                order.filled_avg_price is not None
                and order.filled_avg_price > 0
                and order.filled_quantity > _EPS
            ):
                filled.append((entry, order))
            else:
                unfilled += 1
        return filled, unfilled

    async def _quote_all(self, symbols: set[str]) -> tuple[dict[str, float], bool]:
        """Quote each symbol once. A feed outage degrades the report, never fails it."""
        prices: dict[str, float] = {}
        degraded = False
        for sym in sorted(symbols):
            try:
                data = await self._market.get_market_data(sym)
                prices[sym] = data.quote.price
            except Exception as exc:  # noqa: BLE001 - a scorecard must not 500
                degraded = True
                logger.warning("Scorecard could not quote %s (%s)", sym, exc)
        return prices, degraded


def _score(entry: TradeAuditLog, order: TradeOrder, price: float) -> SignalOutcome:
    fill = order.filled_avg_price
    raw_pct = (price - fill) / fill * 100.0
    # Negate for the short side so "return" always means "return to the signal".
    signed = raw_pct if order.side == "buy" else -raw_pct
    notional = fill * order.filled_quantity
    return SignalOutcome(
        order_id=entry.order_id,
        symbol=order.symbol,
        signal_type=entry.signal_type,
        confidence=entry.confidence,
        executed_at=entry.execution_timestamp,
        fill_price=fill,
        quantity=order.filled_quantity,
        reference_price=price,
        return_pct=round(signed, 4),
        pnl=round(notional * signed / 100.0, 2),
        correct=signed > 0,
    )


def _aggregate(
    outcomes: list[SignalOutcome], unfilled: int, unpriced: int, degraded: bool
) -> SignalScorecard:
    if not outcomes:
        return SignalScorecard(
            evaluated=0,
            skipped_unfilled=unfilled,
            skipped_unpriced=unpriced,
            hit_rate_pct=0.0,
            avg_return_pct=0.0,
            total_pnl=0.0,
            degraded=degraded,
            calibration_note=(
                "Henuz puanlanabilir sinyal yok."
                if not unfilled and not unpriced
                else "Puanlanabilir dolmus sinyal yok."
            ),
        )

    hits = sum(1 for o in outcomes if o.correct)
    return SignalScorecard(
        evaluated=len(outcomes),
        skipped_unfilled=unfilled,
        skipped_unpriced=unpriced,
        hit_rate_pct=round(hits / len(outcomes) * 100.0, 2),
        avg_return_pct=round(sum(o.return_pct for o in outcomes) / len(outcomes), 4),
        total_pnl=round(sum(o.pnl for o in outcomes), 2),
        buckets=_bucketize(outcomes),
        best=max(outcomes, key=lambda o: o.return_pct),
        worst=min(outcomes, key=lambda o: o.return_pct),
        calibration_note=_calibration_note(outcomes),
        degraded=degraded,
    )


def _bucketize(outcomes: list[SignalOutcome]) -> list[ConfidenceBucket]:
    """Group by confidence. Signals with no recorded confidence are left out —
    they predate the column, and inventing a bucket for them would skew it."""
    buckets: list[ConfidenceBucket] = []
    for lower, upper in _BUCKETS:
        group = [
            o for o in outcomes
            if o.confidence is not None and lower <= o.confidence < upper
        ]
        if not group:
            continue
        hits = sum(1 for o in group if o.correct)
        buckets.append(
            ConfidenceBucket(
                label=f"{lower:.2f}-{min(upper, 1.0):.2f}",
                lower=lower,
                upper=min(upper, 1.0),
                signals=len(group),
                hit_rate_pct=round(hits / len(group) * 100.0, 2),
                avg_return_pct=round(sum(o.return_pct for o in group) / len(group), 4),
                total_pnl=round(sum(o.pnl for o in group), 2),
            )
        )
    return buckets


def _calibration_note(outcomes: list[SignalOutcome]) -> str:
    """Say whether higher confidence actually bought a higher hit rate.

    Deliberately refuses to answer on a thin sample: a hit rate over five
    signals is noise, and dressing it up as calibration would be the exact
    false confidence this scorecard exists to detect.
    """
    scored = [o for o in outcomes if o.confidence is not None]
    if len(scored) < 10:
        return (
            f"Kalibrasyon icin yetersiz ornek ({len(scored)}); en az 10 sinyal gerekli."
        )
    mid = 0.75
    high = [o for o in scored if o.confidence >= mid]
    low = [o for o in scored if o.confidence < mid]
    if not high or not low:
        return "Kalibrasyon icin guven araligi cok dar."
    high_rate = sum(1 for o in high if o.correct) / len(high) * 100.0
    low_rate = sum(1 for o in low if o.correct) / len(low) * 100.0
    delta = high_rate - low_rate
    if delta > 5:
        return (
            f"Yuksek guven daha isabetli: >={mid:.2f} icin %{high_rate:.1f}, "
            f"altinda %{low_rate:.1f}."
        )
    if delta < -5:
        return (
            f"UYARI: yuksek guven daha isabetsiz (>={mid:.2f} icin %{high_rate:.1f}, "
            f"altinda %{low_rate:.1f}); min_trade_confidence esigi gozden gecirilmeli."
        )
    return (
        f"Guven ile isabet arasinda anlamli fark yok (%{high_rate:.1f} / %{low_rate:.1f})."
    )
