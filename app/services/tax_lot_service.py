"""Tax-lot engine with FIFO/LIFO selection behind one report interface."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlmodel import Session

from app.core.money import ZERO
from app.models.tax import LotMethod, OpenTaxLot, RealizedTaxLot, TaxLotReport
from app.models.transaction import Transaction
from app.services.portfolio_service import PortfolioService


@dataclass
class _Lot:
    symbol: str
    acquired_at: datetime
    quantity: Decimal
    cost_per_unit: Decimal


class TaxLotService:
    """Rebuild tax lots deterministically; the ledger remains the source of truth."""

    def __init__(self, portfolio: PortfolioService) -> None:
        self._portfolio = portfolio

    def report(self, session: Session, user_id: int, year: int, method: LotMethod) -> TaxLotReport:
        lots: dict[str, list[_Lot]] = {}
        realized: list[RealizedTaxLot] = []
        for tx in self._portfolio.list_transactions(session, user_id):
            if tx.action == "BUY":
                lots.setdefault(tx.symbol, []).append(
                    _Lot(tx.symbol, tx.timestamp, tx.quantity, tx.price)
                )
                continue
            self._consume(lots.setdefault(tx.symbol, []), tx, method, year, realized)

        open_lots = [
            OpenTaxLot(
                symbol=lot.symbol, acquired_at=lot.acquired_at,
                quantity_remaining=round(lot.quantity, 8), cost_per_unit=round(lot.cost_per_unit, 8),
            )
            for symbol_lots in lots.values() for lot in symbol_lots if lot.quantity > ZERO
        ]
        short = sum((r.gain_loss for r in realized if r.term == "short"), ZERO)
        long = sum((r.gain_loss for r in realized if r.term == "long"), ZERO)
        return TaxLotReport(
            year=year, method=method, realized=realized, open_lots=open_lots,
            short_term_gain_loss=round(short, 2), long_term_gain_loss=round(long, 2),
            total_gain_loss=round(short + long, 2),
        )

    @staticmethod
    def _consume(lots: list[_Lot], sale: Transaction, method: LotMethod, year: int,
                 realized: list[RealizedTaxLot]) -> None:
        remaining = sale.quantity
        while remaining > ZERO:
            index = 0 if method == "FIFO" else -1
            lot = lots[index]
            used = min(remaining, lot.quantity)
            if sale.timestamp.year == year:
                cost = used * lot.cost_per_unit
                proceeds = used * sale.price
                term = "long" if (sale.timestamp.date() - lot.acquired_at.date()).days > 365 else "short"
                realized.append(RealizedTaxLot(
                    symbol=sale.symbol, acquired_at=lot.acquired_at, sold_at=sale.timestamp,
                    quantity=round(used, 8), cost_basis=round(cost, 2), proceeds=round(proceeds, 2),
                    gain_loss=round(proceeds - cost, 2), term=term,
                ))
            lot.quantity -= used
            remaining -= used
            if lot.quantity == ZERO:
                lots.pop(index)
