"""Trade execution: encrypted credentials, risk validation, kill-switch, orders.

Every order is validated against the user's risk limits (kill-switch, confidence
gate, max notional, live buying power) BEFORE submission, then persisted as a
TradeOrder and recorded in the append-only TradeAuditLog. Broker API keys are
only ever read through the Fernet-encrypted BrokerCredential record.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlmodel import Session, select

from app.core.config import Settings
from app.core.crypto import decrypt, encrypt
from app.core.errors import AppError
from app.core.metrics import record_order_accepted, record_order_rejected
from app.core.tracing import span
from app.models.broker import (
    BrokerCredential,
    CredentialCreate,
    CredentialRead,
    OrderRequest,
    TradeRequest,
    TradeResult,
)
from app.models.order import TradeAuditLog, TradeOrder
from app.providers.alpaca_broker_provider import AlpacaBrokerProvider
from app.providers.broker_base import BrokerProvider
from app.services.market_service import MarketService
from app.services.risk_service import RiskService

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

    def list_orders(self, session: Session, user_id: int) -> list[TradeOrder]:
        stmt = (
            select(TradeOrder)
            .where(TradeOrder.user_id == user_id)
            .order_by(TradeOrder.created_at.desc())
        )
        return list(session.exec(stmt).all())

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
            )
        )
        sp.set_attribute("order.id", str(broker_order.id))
        self._persist_order(session, user_id, req, broker_order)
        return TradeResult(
            accepted=True,
            order=broker_order,
            message=(
                f"{side.upper()} {req.quantity} {req.symbol.upper()} "
                f"@~{round(price, 2)} (paper)."
            ),
        )

    def _persist_order(self, session, user_id, req, broker_order) -> None:
        """Persist the submitted order + write the append-only audit record."""
        session.add(
            TradeOrder(
                user_id=user_id,
                broker="alpaca",
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
                raw_ai_context=req.ai_context or "",
            )
        )
        session.commit()

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
