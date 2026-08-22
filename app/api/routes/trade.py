"""Trade endpoints: encrypted creds, risk-checked orders, reconciliation, kill-switch."""
import hmac

from fastapi import APIRouter, Depends, Header, Request
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import (
    get_reconciliation_service,
    get_risk_service,
    get_trade_service,
)
from app.core.errors import AppError, NotFoundError
from app.core.rate_limit import TRADE_RATE_LIMIT, limiter
from app.db.database import get_session
from app.models.broker import (
    CredentialCreate,
    CredentialRead,
    TradeRequest,
    TradeResult,
)
from app.models.order import OrderRead
from app.core.config import get_settings
from app.models.deck import TradeDeckState, WsContract
from app.models.webhook import AlpacaTradeUpdate, WebhookResult
from app.models.risk import (
    DriftReport,
    KillSwitchStatus,
    KillSwitchToggle,
    RiskConfigRead,
    RiskConfigUpdate,
)
from app.models.user import User
from app.services.reconciliation_service import TradeReconciliationService
from app.services.risk_service import RiskService
from app.services.trade_service import TradeService

router = APIRouter(prefix="/api/trade", tags=["trade"])


@router.post("/credentials", response_model=CredentialRead, status_code=201)
def save_credentials(
    data: CredentialCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> CredentialRead:
    """Store broker API keys ENCRYPTED at rest. Secrets are never returned."""
    return service.save_credentials(session, user.id, data)


@router.get("/credentials", response_model=CredentialRead)
def read_credentials(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> CredentialRead:
    cred = service.get_credential_read(session, user.id)
    if cred is None:
        raise NotFoundError("Broker kimlik bilgisi bulunamadi.")
    return cred


@router.post("/execute", response_model=TradeResult)
@limiter.limit(TRADE_RATE_LIMIT)
async def execute_trade(
    request: Request,
    data: TradeRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> TradeResult:
    """Validate the user's risk limits, then place a paper order from an AI signal."""
    return await service.execute(session, user.id, data)


@router.post("/webhook", response_model=WebhookResult)
async def alpaca_webhook(
    payload: AlpacaTradeUpdate,
    x_webhook_secret: str | None = Header(default=None),
    session: Session = Depends(get_session),
    recon: TradeReconciliationService = Depends(get_reconciliation_service),
) -> WebhookResult:
    """Ingest an Alpaca trade-update and reconcile fills into the ledger instantly.

    Shared-secret authenticated (constant-time compare). This is the fast path;
    the background poll loop remains a backstop and is idempotent with it. An
    unknown order id is accepted but writes nothing (never raises).
    """
    secret = get_settings().trade_webhook_secret
    if not secret:
        raise AppError("Webhook alimi devre disi.", status_code=503)
    if not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, secret):
        raise AppError("Gecersiz webhook imzasi.", status_code=401)
    wrote = await recon.ingest_fill_event(session, payload)
    return WebhookResult(accepted=True, reconciled=wrote)


@router.get("/orders", response_model=list[OrderRead])
def list_orders(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> list[OrderRead]:
    """Submitted orders and their reconciliation state."""
    return service.list_orders(session, user.id)


@router.get("/reconciliation", response_model=DriftReport)
async def position_reconciliation(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
    recon: TradeReconciliationService = Depends(get_reconciliation_service),
) -> DriftReport:
    """On-demand broker-vs-ledger position drift for the current user."""
    provider = service.provider_for_user(session, user.id)
    if provider is None:
        raise NotFoundError("Broker kimlik bilgisi bulunamadi.")
    return await recon.position_drift(session, user.id, provider)


@router.get("/risk-config", response_model=RiskConfigRead)
def get_risk_config(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    risk: RiskService = Depends(get_risk_service),
) -> RiskConfigRead:
    return risk.get_config(session, user.id)


@router.post("/risk-config", response_model=RiskConfigRead)
def set_risk_config(
    data: RiskConfigUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    risk: RiskService = Depends(get_risk_service),
) -> RiskConfigRead:
    """Set the daily-loss kill-switch threshold (percent; 0 disables)."""
    return risk.set_config(session, user.id, data)


@router.get("/kill-switch", response_model=KillSwitchStatus)
async def kill_switch_status(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    risk: RiskService = Depends(get_risk_service),
) -> KillSwitchStatus:
    return await risk.status(session, user.id)


@router.post("/kill-switch", response_model=KillSwitchStatus)
async def toggle_kill_switch(
    data: KillSwitchToggle,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    risk: RiskService = Depends(get_risk_service),
) -> KillSwitchStatus:
    """Operator toggle: manually halt (enabled=True) or resume automated trading."""
    risk.set_manual_halt(session, user.id, data.enabled)
    return await risk.status(session, user.id)


@router.get("/deck", response_model=TradeDeckState)
async def trade_deck(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
    risk: RiskService = Depends(get_risk_service),
) -> TradeDeckState:
    """One unified, typed payload for the trading UI."""
    settings = get_settings()
    return TradeDeckState(
        has_credentials=service.get_credential_read(session, user.id) is not None,
        open_orders=sum(1 for o in service.list_orders(session, user.id) if not o.reconciled),
        kill_switch=await risk.status(session, user.id),
        risk_config=risk.get_config(session, user.id),
        max_order_notional=settings.max_order_notional,
        min_trade_confidence=settings.min_trade_confidence,
        trade_enabled=settings.trade_enabled,
        ws=WsContract(),
    )
