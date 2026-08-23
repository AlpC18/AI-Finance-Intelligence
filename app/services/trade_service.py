"""Trade execution: encrypted credentials, risk validation, kill-switch, orders.

Every order is validated against the user's risk limits (kill-switch, confidence
gate, max notional, live buying power) BEFORE submission, then persisted as a
TradeOrder and recorded in the append-only TradeAuditLog. Broker API keys are
only ever read through the Fernet-encrypted BrokerCredential record.
"""
from __future__ import annotations

from datetime import datetime, timezone

import logging
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import Settings
from app.core.crypto import decrypt, encrypt
from app.core.errors import AppError, NotFoundError
from app.core.metrics import (
    record_order_accepted,
    record_order_canceled,
    record_order_replaced,
    record_order_rejected,
)
from app.core.tracing import span
from app.models.broker import (
    BrokerCredential,
    BrokerOrder,
    CredentialCreate,
    CredentialRead,
    OrderAmendment,
    OrderRequest,
    TradeRequest,
    TradeResult,
)
from app.models.order import (
    AuditLogPage,
    AuditLogRead,
    TradeAuditLog,
    TradeOrder,
    is_cancelable,
)
from app.providers.alpaca_broker_provider import AlpacaBrokerProvider
from app.providers.broker_base import BrokerError, BrokerProvider
from app.services.market_service import MarketService
from app.services.risk_service import RiskService

logger = logging.getLogger("trade")

# (api_key, api_secret, base_url) -> BrokerProvider — injectable for tests.
BrokerFactory = Callable[..., BrokerProvider]


class TradeService:
    def __init__(
        self,
        settings: Settings,
        market: MarketService,
        broker_factory: Optional[BrokerFactory] = None,
        risk: Optional[RiskService] = None,
    ) -> None:
        self._settings = settings
        self._market = market
        self._broker_factory = broker_factory or AlpacaBrokerProvider
        self._risk = risk

    # --- Credentials (encrypted at rest) ---
    def save_credentials(
        self, session: Session, user_id: int, data: CredentialCreate
    ) -> CredentialRead:
        record = self._find_credential(session, user_id, data.broker) or BrokerCredential(
            user_id=user_id, broker=data.broker, api_key_enc="", api_secret_enc=""
        )
        record.api_key_enc = encrypt(data.api_key)  # ciphertext only
        record.api_secret_enc = encrypt(data.api_secret)
        record.paper = data.paper
        session.add(record)
        session.commit()
        session.refresh(record)
        return _credential_read(record, data.api_key)

    def get_credential_read(
        self, session: Session, user_id: int, broker: str = "alpaca"
    ) -> Optional[CredentialRead]:
        record = self._find_credential(session, user_id, broker)
        if record is None:
            return None
        return _credential_read(record, decrypt(record.api_key_enc))

    def _find_credential(
        self, session: Session, user_id: int, broker: str
    ) -> Optional[BrokerCredential]:
        stmt = select(BrokerCredential).where(
            BrokerCredential.user_id == user_id, BrokerCredential.broker == broker
        )
        return session.exec(stmt).first()

    def provider_for_user(
        self, session: Session, user_id: int, broker: str = "alpaca"
    ) -> Optional[BrokerProvider]:
        """Decrypt creds and build a provider, or None if the user has none."""
        record = self._find_credential(session, user_id, broker)
        if record is None:
            return None
        return self._broker_factory(
            api_key=decrypt(record.api_key_enc),
            api_secret=decrypt(record.api_secret_enc),
            base_url=self._settings.alpaca_base_url,
        )

    def _provider_or_error(
        self, session: Session, user_id: int, broker: str = "alpaca"
    ) -> BrokerProvider:
        provider = self.provider_for_user(session, user_id, broker)
        if provider is None:
            raise AppError(
                "Broker kimlik bilgileri bulunamadi. Once /api/trade/credentials ile kaydedin.",
                status_code=400,
                reason="no_credentials",
            )
        return provider

    def list_orders(
        self,
        session: Session,
        user_id: int,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[TradeOrder]:
        """Submitted orders, newest first. `limit=None` returns all of them —
        the trade deck counts open orders and must not miss any."""
        stmt = (
            select(TradeOrder)
            .where(TradeOrder.user_id == user_id)
            .order_by(TradeOrder.created_at.desc())
        )
        if limit is not None:
            stmt = stmt.offset(offset).limit(limit)
        return list(session.exec(stmt).all())

    def count_orders(self, session: Session, user_id: int) -> int:
        return session.exec(
            select(func.count()).select_from(TradeOrder)
            .where(TradeOrder.user_id == user_id)
        ).one()

    def list_audit_log(
        self,
        session: Session,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
        symbol: Optional[str] = None,
    ) -> AuditLogPage:
        """Read the append-only compliance trail, newest first.

        Joined LEFT to the order so a row survives its order being purged - the
        trail is append-only and must render even when the execution it refers
        to is gone. Scoped to `user_id` in the query itself, never filtered
        after the fact.
        """
        conditions = [TradeAuditLog.user_id == user_id]
        if symbol:
            conditions.append(TradeOrder.symbol == symbol.upper().strip())

        joined = (
            select(TradeAuditLog, TradeOrder)
            .join(
                TradeOrder,
                # Both halves matter: the id links the rows, and the user_id
                # keeps a colliding broker id from ever attaching one tenant's
                # fill to another tenant's audit row.
                (TradeOrder.broker_order_id == TradeAuditLog.order_id)
                & (TradeOrder.user_id == TradeAuditLog.user_id),
                isouter=True,
            )
            .where(*conditions)
        )
        # Count in the database. Materialising every row just to call len() on
        # it made each page request cost a full-table read of the trail.
        total = session.exec(
            select(func.count())
            .select_from(TradeAuditLog)
            .join(
                TradeOrder,
                (TradeOrder.broker_order_id == TradeAuditLog.order_id)
                & (TradeOrder.user_id == TradeAuditLog.user_id),
                isouter=True,
            )
            .where(*conditions)
        ).one()
        rows = session.exec(
            joined.order_by(TradeAuditLog.execution_timestamp.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return AuditLogPage(
            total=total,
            limit=limit,
            offset=offset,
            items=[_audit_read(entry, order) for entry, order in rows],
        )

    # --- Cancellation ---
    async def cancel_order(
        self, session: Session, user_id: int, order_id: int
    ) -> TradeOrder:
        """Ask the venue to cancel one working order.

        Scoped to ``user_id`` inside the query, so another tenant's order is
        indistinguishable from one that does not exist - a 404 either way,
        which is what keeps this from being an order-id enumeration oracle.

        On success the order is marked ``pending_cancel``, NOT ``canceled``.
        The venue has only accepted the request at this point; the order can
        still fill on its way down, and writing a terminal state here would
        park a lie in the ledger that the next reconciliation sweep has to
        undo. ``pending_cancel`` is non-terminal, so the existing sweep keeps
        polling until the venue says what actually happened.
        """
        order = self._find_order(session, user_id, order_id)
        if order is None:
            raise NotFoundError("Emir bulunamadi.")
        if not is_cancelable(order.status):
            raise AppError(
                f"Emir '{order.status}' durumunda; iptal edilemez.",
                status_code=409,
                reason="not_cancelable",
            )

        provider = self._provider_or_error(session, user_id, "alpaca")
        try:
            await provider.cancel_order(order.broker_order_id)
        except BrokerError as exc:
            # 422 is the venue saying "too late" - it already filled, expired or
            # was cancelled elsewhere. Our row is then simply stale, so resync it
            # from the venue before answering. Without this the caller gets a
            # bare error next to a local status that still says the order is
            # working, and has no way to tell which of the two to believe.
            if exc.upstream_status == 422:
                await self._resync(session, user_id, order, provider)
                raise AppError(
                    f"Emir artik iptal edilemez (durum: {order.status}).",
                    status_code=409,
                    reason="not_cancelable",
                ) from exc
            raise

        order.status = "pending_cancel"
        order.updated_at = _utcnow()
        session.add(order)
        session.commit()
        session.refresh(order)
        record_order_canceled("manual")
        return order

    async def cancel_open_orders(
        self, session: Session, user_id: int, source: str = "kill_switch"
    ) -> int:
        """Best-effort flatten of every working order. Returns the count accepted.

        Deliberately total: one order that cannot be cancelled must not strand
        the rest, because the caller is usually an operator trying to stop
        everything at once. A user with no broker credentials cancels nothing
        and that is not an error - there is nothing at a venue to cancel.
        """
        provider = self.provider_for_user(session, user_id, "alpaca")
        if provider is None:
            return 0

        canceled = 0
        for order in self._open_orders(session, user_id):
            try:
                await provider.cancel_order(order.broker_order_id)
            except Exception as exc:  # noqa: BLE001 - one refusal must not stop the sweep
                logger.warning(
                    "Cancel failed during flatten: user=%s order=%s: %s",
                    user_id, order.broker_order_id, exc,
                )
                continue
            order.status = "pending_cancel"
            order.updated_at = _utcnow()
            session.add(order)
            canceled += 1
            record_order_canceled(source)
        if canceled:
            session.commit()
        return canceled

    def _find_order(
        self, session: Session, user_id: int, order_id: int
    ) -> Optional[TradeOrder]:
        return session.exec(
            select(TradeOrder).where(
                TradeOrder.id == order_id, TradeOrder.user_id == user_id
            )
        ).first()

    def _open_orders(self, session: Session, user_id: int) -> list[TradeOrder]:
        """Working orders only - terminal ones have nothing left to cancel."""
        rows = session.exec(
            select(TradeOrder).where(
                TradeOrder.user_id == user_id, TradeOrder.reconciled == False  # noqa: E712
            )
        ).all()
        return [o for o in rows if is_cancelable(o.status)]

    async def _resync(
        self, session: Session, user_id: int, order: TradeOrder, provider: BrokerProvider
    ) -> None:
        """Pull authoritative state for one order. Never raises: this runs on an
        error path, and failing to refresh is not worse than not trying."""
        try:
            remote = await provider.get_order(order.broker_order_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Resync failed for order %s: %s", order.broker_order_id, exc)
            return
        order.status = remote.status or order.status
        order.filled_quantity = remote.filled_quantity
        order.filled_avg_price = remote.filled_avg_price
        order.updated_at = _utcnow()
        session.add(order)
        session.commit()
        session.refresh(order)

    async def replace_order(
        self, session: Session, user_id: int, order_id: int, amendment: OrderAmendment
    ) -> TradeOrder:
        """Amend a working order instead of cancel-and-resubmit.

        Cancel-and-resubmit surrenders queue position and leaves a window where
        the intent is not in the market at all. A venue-side replace keeps both.

        The kill-switch is checked FIRST and deliberately: an amendment can
        raise exposure, so a halt that blocked new orders but allowed existing
        ones to be scaled up would be a hole straight through it.

        Alpaca answers a replace with a NEW order id and retires the original,
        so that is modelled honestly here - the old row goes to ``replaced`` and
        a new row is written. Rewriting the original row in place would leave
        the audit trail claiming one order where the venue saw two.
        """
        order = self._find_order(session, user_id, order_id)
        if order is None:
            raise NotFoundError("Emir bulunamadi.")
        if not is_cancelable(order.status):
            raise AppError(
                f"Emir '{order.status}' durumunda; degistirilemez.",
                status_code=409,
                reason="not_amendable",
            )
        if self._risk is not None:
            await self._risk.assert_not_halted(session, user_id)

        quantity = amendment.quantity if amendment.quantity is not None else order.quantity
        price = amendment.limit_price or await self._market_price(order.symbol)
        # Our own size policy still applies to the amended order. Buying power is
        # deliberately NOT re-checked here: the original order already has funds
        # reserved at the venue, so comparing the full new notional against live
        # buying power would double-count them and reject valid amendments. The
        # venue rejects a genuinely unaffordable replace authoritatively.
        self._validate_notional(price * quantity)

        provider = self._provider_or_error(session, user_id, "alpaca")
        client_order_id = _fresh_key()
        replacement = await provider.replace_order(
            order.broker_order_id,
            quantity=amendment.quantity,
            limit_price=amendment.limit_price,
            client_order_id=client_order_id,
        )

        # Retired, but NOT marked reconciled: the reconciliation sweep is the
        # authority on how an order ended, and it may still have a fill on it.
        order.status = "replaced"
        order.updated_at = _utcnow()
        session.add(order)

        new_row = TradeOrder(
            user_id=user_id,
            broker=order.broker,
            client_order_id=client_order_id,
            broker_order_id=replacement.id,
            symbol=replacement.symbol or order.symbol,
            side=replacement.side or order.side,
            quantity=replacement.quantity or quantity,
            order_type=replacement.order_type or order.order_type,
            status=replacement.status or "pending_replace",
            filled_quantity=replacement.filled_quantity,
            filled_avg_price=replacement.filled_avg_price,
            reconciled=False,
        )
        session.add(new_row)
        session.commit()
        session.refresh(new_row)
        record_order_replaced()
        return new_row

    def get_order(self, session: Session, user_id: int, order_id: int) -> TradeOrder:
        """One order, scoped to its owner (404 rather than 403, as with cancel)."""
        order = self._find_order(session, user_id, order_id)
        if order is None:
            raise NotFoundError("Emir bulunamadi.")
        return order

    async def _market_price(self, symbol: str) -> float:
        data = await self._market.get_market_data(symbol)
        return data.quote.price

    # --- Execution ---
    async def execute(
        self, session: Session, user_id: int, req: TradeRequest
    ) -> TradeResult:
        """Second span of the hot path, and the source of the order-reject rate.

        Every rejection is counted with a low-cardinality reason label so the
        operator alert rules can distinguish "users hitting risk limits" from
        "execution is broken".
        """
        with span(
            "order.execute", symbol=req.symbol.upper().strip(), **{"user.id": user_id}
        ) as sp:
            try:
                result = await self._execute_inner(session, user_id, req, sp)
            except AppError as exc:
                record_order_rejected(_reject_reason(exc))
                sp.set_attribute("order.rejected", True)
                sp.record_exception(exc)
                raise
            record_order_accepted()
            return result

    async def _execute_inner(
        self, session: Session, user_id: int, req: TradeRequest, sp
    ) -> TradeResult:
        if not self._settings.trade_enabled:
            raise AppError(
                "Trade execution devre disi.", status_code=403, reason="disabled"
            )
        if self._risk is not None:
            await self._risk.assert_not_halted(session, user_id)  # kill-switch

        # Cheapest layer of the duplicate defence, and the one that catches the
        # common case (a double click, or a client retrying after a timeout):
        # a key we have already seen replays its order and never reaches the
        # venue, so no risk gate or broker call runs a second time.
        client_order_id = req.idempotency_key or _fresh_key()
        existing = self._find_by_client_order_id(session, user_id, client_order_id)
        if existing is not None:
            sp.set_attribute("order.duplicate", True)
            return _replay(existing)

        side = _resolve_side(req)              # validates action (HOLD rejected)
        self._validate_confidence(req)         # AI-signal confidence gate
        price = await self._reference_price(req)
        notional = price * req.quantity
        self._validate_notional(notional)      # max order size

        provider = self._provider_or_error(session, user_id, "alpaca")
        account = await provider.get_account()
        self._validate_buying_power(side, notional, account)  # live buying power

        sp.set_attribute("order.side", side)
        sp.set_attribute("order.notional", float(notional))

        broker_order = await provider.place_order(
            OrderRequest(
                symbol=req.symbol.upper().strip(),
                side=side,
                quantity=req.quantity,
                order_type=req.order_type,
                limit_price=req.limit_price,
                time_in_force=req.time_in_force,
                client_order_id=client_order_id,
            )
        )
        sp.set_attribute("order.id", str(broker_order.id))
        # Third layer: the unique index. If a concurrent request persisted the
        # same key while we were at the venue, its order is the real one.
        won = self._persist_order(session, user_id, req, broker_order, client_order_id)
        if not won:
            sp.set_attribute("order.duplicate", True)
            return _replay(
                self._find_by_client_order_id(session, user_id, client_order_id)
            )
        return TradeResult(
            accepted=True,
            order=broker_order,
            message=(
                f"{side.upper()} {req.quantity} {req.symbol.upper()} "
                f"@~{round(price, 2)} (paper)."
            ),
        )

    def _find_by_client_order_id(
        self, session: Session, user_id: int, client_order_id: str
    ) -> Optional[TradeOrder]:
        return session.exec(
            select(TradeOrder).where(
                TradeOrder.user_id == user_id,
                TradeOrder.client_order_id == client_order_id,
            )
        ).first()

    def _persist_order(
        self, session, user_id, req, broker_order, client_order_id: str
    ) -> bool:
        """Persist the order + audit record. False when a concurrent request
        already claimed this idempotency key (the unique index rejected us)."""
        session.add(
            TradeOrder(
                user_id=user_id,
                broker="alpaca",
                client_order_id=client_order_id,
                broker_order_id=broker_order.id,
                symbol=broker_order.symbol,
                side=broker_order.side,
                quantity=broker_order.quantity,
                order_type=broker_order.order_type,
                status=broker_order.status or "pending",
                filled_quantity=broker_order.filled_quantity,
                filled_avg_price=broker_order.filled_avg_price,
                reconciled=False,
            )
        )
        session.add(
            TradeAuditLog(
                user_id=user_id,
                order_id=broker_order.id,
                signal_type=req.action,
                confidence=req.confidence,
                raw_ai_context=req.ai_context or "",
            )
        )
        try:
            session.commit()
        except IntegrityError:
            # The unique index fired: another request persisted this key first.
            session.rollback()
            return False
        return True

    # --- Risk validation (defensive, fail-closed) ---
    def _validate_confidence(self, req: TradeRequest) -> None:
        if (
            req.confidence is not None
            and req.confidence < self._settings.min_trade_confidence
        ):
            raise AppError(
                f"Sinyal guveni ({req.confidence:.2f}) minimum esigin "
                f"({self._settings.min_trade_confidence:.2f}) altinda; islem reddedildi.",
                status_code=422,
                reason="confidence",
            )

    def _validate_notional(self, notional: float) -> None:
        if notional <= 0:
            raise AppError(
                "Gecersiz islem buyuklugu.", status_code=422, reason="notional"
            )
        if notional > self._settings.max_order_notional:
            raise AppError(
                f"Emir buyuklugu ({notional:.2f}) izin verilen azami "
                f"({self._settings.max_order_notional:.2f}) ustunde.",
                status_code=422,
                reason="notional",
            )

    def _validate_buying_power(self, side: str, notional: float, account) -> None:
        if side == "buy" and account.buying_power and notional > account.buying_power:
            raise AppError(
                f"Yetersiz alim gucu: {account.buying_power:.2f} < {notional:.2f}.",
                status_code=422,
                reason="buying_power",
            )

    async def _reference_price(self, req: TradeRequest) -> float:
        if req.limit_price is not None:
            return req.limit_price
        data = await self._market.get_market_data(req.symbol)
        return data.quote.price


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fresh_key() -> str:
    """A unique key for a caller that supplied none.

    Deliberately random rather than derived from the order's contents: two
    identical orders an hour apart are two real intents, and hashing the body
    would silently swallow the second. Callers that want de-duplication opt in
    by sending their own key.
    """
    return uuid4().hex


def _replay(order: Optional[TradeOrder]) -> TradeResult:
    """Echo an already-placed order back to a repeated submission."""
    if order is None:  # pragma: no cover - the row was just observed to exist
        raise AppError("Emir bulunamadi.", status_code=409, reason="duplicate")
    return TradeResult(
        accepted=True,
        duplicate=True,
        order=BrokerOrder(
            id=order.broker_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            order_type=order.order_type,
            status=order.status,
            filled_quantity=order.filled_quantity,
            filled_avg_price=order.filled_avg_price,
        ),
        message=(
            f"Ayni idempotency anahtari ile gonderilmis emir mevcut; "
            f"yeni emir olusturulmadi ({order.broker_order_id})."
        ),
    )


def _audit_read(entry: TradeAuditLog, order: Optional[TradeOrder]) -> AuditLogRead:
    """Project one audit row + its (possibly absent) order into the read model."""
    return AuditLogRead(
        id=entry.id,
        order_id=entry.order_id,
        signal_type=entry.signal_type,
        confidence=entry.confidence,
        execution_timestamp=entry.execution_timestamp,
        raw_ai_context=entry.raw_ai_context,
        symbol=order.symbol if order else None,
        status=order.status if order else None,
        filled_quantity=order.filled_quantity if order else None,
        filled_avg_price=order.filled_avg_price if order else None,
    )


def _reject_reason(exc: AppError) -> str:
    """Metric label for a rejection — structural, never parsed from the message."""
    return getattr(exc, "reason", None) or "unknown"


def _resolve_side(req: TradeRequest) -> str:
    if req.action == "BUY":
        return "buy"
    if req.action == "SELL":
        return "sell"
    raise AppError(
        "HOLD sinyali icra edilemez; yalnizca BUY/SELL.",
        status_code=422,
        reason="invalid_action",
    )


def _credential_read(record: BrokerCredential, api_key_plain: str) -> CredentialRead:
    last4 = api_key_plain[-4:] if len(api_key_plain) >= 4 else "****"
    return CredentialRead(
        broker=record.broker,
        paper=record.paper,
        created_at=record.created_at,
        api_key_last4=last4,
    )
