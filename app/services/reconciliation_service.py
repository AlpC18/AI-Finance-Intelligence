"""Order-fill reconciliation into the ledger + broker/ledger position drift."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlmodel import Session, select

from app.core.money import ZERO, to_decimal
from app.core.metrics import record_fill_reconciled
from app.core.tracing import span
from app.models.order import TradeOrder, is_terminal
from app.models.risk import DriftItem, DriftReport
from app.models.transaction import Transaction
from app.models.webhook import AlpacaTradeUpdate
from app.providers.broker_base import BrokerProvider
from app.services.portfolio_service import PortfolioService

logger = logging.getLogger("reconciliation")
_EPS = 1e-6

# (user_id) -> provider or None (no creds).
BrokerResolver = Callable[[int], Optional[BrokerProvider]]


class TradeReconciliationService:
    def __init__(self, portfolio: PortfolioService) -> None:
        self._portfolio = portfolio

    async def reconcile_order(
        self, session: Session, order: TradeOrder, provider: BrokerProvider
    ) -> bool:
        """Poll one order; on fill, write the executed fill into the ledger.

        Returns True iff a Transaction was appended. Defensive: a poll failure
        leaves the order open for the next sweep rather than raising.
        """
        try:
            remote = await provider.get_order(order.broker_order_id)
        except Exception as exc:  # noqa: BLE001 - transient poll failure
            logger.warning("Order %s poll failed: %s", order.broker_order_id, exc)
            return False

        wrote = self._apply_remote_state(
            session,
            order,
            status=remote.status,
            filled_quantity=remote.filled_quantity,
            filled_avg_price=remote.filled_avg_price,
            source="poll",
        )
        session.add(order)
        return wrote

    def _apply_remote_state(
        self,
        session: Session,
        order: TradeOrder,
        *,
        status: str,
        filled_quantity: float,
        filled_avg_price: Optional[float],
        source: str = "poll",
    ) -> bool:
        """Apply an authoritative order state (from poll OR webhook) to the ledger.

        Idempotent: a fill is written exactly once (guarded by ``order.reconciled``),
        so the polling backstop and the webhook can never double-book a transaction.
        Returns True iff a Transaction was appended.
        """
        order.status = status
        order.filled_quantity = filled_quantity
        order.filled_avg_price = filled_avg_price
        order.updated_at = datetime.now(timezone.utc)

        wrote = False
        if (
            status == "filled"
            and not order.reconciled
            and filled_quantity > _EPS
            and filled_avg_price is not None
            and filled_avg_price > 0
        ):
            session.add(
                Transaction(
                    user_id=order.user_id,
                    symbol=order.symbol.upper(),
                    action="BUY" if order.side == "buy" else "SELL",
                    quantity=filled_quantity,
                    price=filled_avg_price,
                )
            )
            order.reconciled = True
            wrote = True
            record_fill_reconciled(source)
            logger.info(
                "Reconciled fill: user=%s order=%s %s %s @ %s",
                order.user_id, order.broker_order_id, order.side,
                filled_quantity, filled_avg_price,
            )
        elif is_terminal(status) and status != "filled":
            order.reconciled = True  # closed with no fill; nothing to write
        return wrote

    async def ingest_fill_event(
        self, session: Session, update: AlpacaTradeUpdate
    ) -> bool:
        """Apply an inbound Alpaca webhook trade-update straight into the ledger.

        This is the fast path: the fill lands the instant the broker reports it,
        rather than on the next poll. Idempotent with the polling backstop via the
        same ``reconciled`` guard. Returns True iff a fill was written.
        """
        with span(
            "fill.reconcile",
            source="webhook",
            event=update.event,
            **{"order.id": update.order.id},
        ) as sp:
            order = session.exec(
                select(TradeOrder).where(TradeOrder.broker_order_id == update.order.id)
            ).first()
            if order is None:
                sp.set_attribute("fill.unknown_order", True)
                return False  # unknown order (foreign/stale) — ignore, never raise
            wrote = self._apply_remote_state(
                session,
                order,
                status=update.order.status,
                filled_quantity=update.order.filled_qty,
                filled_avg_price=update.order.filled_avg_price,
                source="webhook",
            )
            session.add(order)
            session.commit()
            sp.set_attribute("fill.written", wrote)
            return wrote

    async def reconcile_open_orders(
        self, session: Session, broker_for: BrokerResolver
    ) -> int:
        """Poll every un-reconciled order. Returns the number of fills written."""
        open_orders = list(
            session.exec(select(TradeOrder).where(TradeOrder.reconciled == False)).all()  # noqa: E712
        )
        providers: dict[int, Optional[BrokerProvider]] = {}
        written = 0
        for order in open_orders:
            if order.user_id not in providers:
                providers[order.user_id] = broker_for(order.user_id)
            provider = providers[order.user_id]
            if provider is None:
                continue  # user removed creds; leave order for a later sweep
            try:
                if await self.reconcile_order(session, order, provider):
                    written += 1
            except Exception as exc:  # noqa: BLE001 - one bad order must not kill sweep
                logger.warning("Reconcile order %s failed: %s", order.id, exc)
        session.commit()
        return written

    async def position_drift(
        self, session: Session, user_id: int, provider: BrokerProvider
    ) -> DriftReport:
        """Compare broker-held positions against ledger-reconstructed holdings."""
        try:
            remote = await provider.get_positions()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Position fetch failed for user %s: %s", user_id, exc)
            return DriftReport(in_sync=True, items=[])

        broker_qty = {p.symbol.upper(): p.quantity for p in remote}
        ledger_qty = {
            h.symbol.upper(): h.quantity
            for h in self._portfolio.active_holdings(session, user_id)
        }
        items: list[DriftItem] = []
        for symbol in sorted(set(broker_qty) | set(ledger_qty)):
            b = broker_qty.get(symbol, ZERO)
            local = ledger_qty.get(symbol, ZERO)
            if abs(b - local) > _EPS:
                items.append(
                    DriftItem(
                        symbol=symbol,
                        broker_quantity=b,
                        ledger_quantity=local,
                        drift=round(b - local, 6),
                    )
                )
        if items:
            logger.warning(
                "Position drift for user %s: %s", user_id, [i.symbol for i in items]
            )
        return DriftReport(in_sync=not items, items=items)
