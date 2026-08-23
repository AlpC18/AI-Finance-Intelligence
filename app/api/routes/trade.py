"""Trade endpoints: encrypted creds, risk-checked orders, reconciliation, kill-switch."""
import hmac

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.csv_export import content_disposition, to_csv
from app.core.deps import (
    get_reconciliation_service,
    get_risk_service,
    get_signal_performance_service,
    get_trade_service,
)
from app.core.errors import AppError, NotFoundError
from app.core.rate_limit import TRADE_RATE_LIMIT, limiter
from app.db.database import get_session
from app.models.broker import (
    CredentialCreate,
    CredentialRead,
    OrderAmendment,
    TradeRequest,
    TradeResult,
)
from app.models.order import AuditLogPage, OrderRead
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
from app.models.signal import SignalScorecard
from app.models.user import User
from app.services.reconciliation_service import TradeReconciliationService
from app.services.risk_service import RiskService
from app.services.signal_performance_service import SignalPerformanceService
from app.services.trade_service import TradeService

# The audit export is a download, not a UI read, so it is capped high
# rather than paginated - a partial compliance trail defeats the purpose.
_AUDIT_EXPORT_CAP = 100_000

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
    response: Response,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> list[OrderRead]:
    """A page of submitted orders and their reconciliation state.

    `X-Total-Count` reports the full size so truncation is detectable.
    """
    response.headers["X-Total-Count"] = str(service.count_orders(session, user.id))
    return service.list_orders(session, user.id, limit=limit, offset=offset)


@router.get("/orders/{order_id}", response_model=OrderRead)
def get_order(
    order_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> OrderRead:
    """One submitted order and its reconciliation state."""
    return service.get_order(session, user.id, order_id)


@router.patch("/orders/{order_id}", response_model=OrderRead)
async def replace_order(
    order_id: int,
    data: OrderAmendment,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> OrderRead:
    """Amend a working order's size or limit price, keeping its queue position.

    The venue retires the original and issues a replacement, so the RESPONSE IS
    THE NEW ORDER, with a new id. 409 when the order is past amending, 423 when
    the kill-switch is engaged.
    """
    return await service.replace_order(session, user.id, order_id, data)


@router.delete("/orders/{order_id}", response_model=OrderRead)
async def cancel_order(
    order_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> OrderRead:
    """Request cancellation of one working order.

    Returns the order in ``pending_cancel``: the venue has accepted the request,
    but the order is not dead until reconciliation says so. 409 when the order
    already reached a terminal state, 404 when it is not this user's.
    """
    return await service.cancel_order(session, user.id, order_id)


@router.get("/export/audit", response_class=PlainTextResponse)
def export_audit_log(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> PlainTextResponse:
    """The full compliance trail as CSV.

    Carries the model's reasoning verbatim, which is exactly the untrusted
    free text that makes formula escaping non-optional here.
    """
    page = service.list_audit_log(session, user.id, limit=_AUDIT_EXPORT_CAP)
    body = to_csv(
        ["executed_at", "order_id", "signal", "confidence", "symbol",
         "status", "filled_quantity", "filled_avg_price", "ai_context"],
        [
            (
                i.execution_timestamp.isoformat(), i.order_id, i.signal_type,
                i.confidence, i.symbol, i.status, i.filled_quantity,
                i.filled_avg_price, i.raw_ai_context,
            )
            for i in page.items
        ],
    )
    return PlainTextResponse(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition("audit-log.csv"),
            "X-Total-Count": str(page.total),
        },
    )


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
    service: TradeService = Depends(get_trade_service),
) -> KillSwitchStatus:
    """Operator toggle: manually halt (enabled=True) or resume automated trading.

    Halting also flattens working orders unless ``cancel_open`` is false. The
    halt is written FIRST so no new order can slip in behind the cancellations,
    and the flatten is best-effort: a venue that refuses one cancel must not
    leave the switch un-pulled.
    """
    risk.set_manual_halt(session, user.id, data.enabled)
    canceled = None
    if data.enabled and data.cancel_open:
        canceled = await service.cancel_open_orders(session, user.id)
    status = await risk.status(session, user.id)
    return status.model_copy(update={"canceled_orders": canceled})


@router.get("/audit", response_model=AuditLogPage)
def list_audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    symbol: str | None = Query(default=None, max_length=20),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: TradeService = Depends(get_trade_service),
) -> AuditLogPage:
    """The append-only compliance trail: every signal that became an order.

    Scoped to the caller — the audit log is per-user and there is no operator
    view here, so no user can ever read another's reasoning or positions.
    """
    return service.list_audit_log(session, user.id, limit, offset, symbol)


@router.get("/signal-scorecard", response_model=SignalScorecard)
async def signal_scorecard(
    symbol: str | None = Query(default=None, max_length=20),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: SignalPerformanceService = Depends(get_signal_performance_service),
) -> SignalScorecard:
    """Did following the AI signals actually make money?

    Marks every filled signal against the latest quote and reports the hit rate
    by confidence bucket, so `min_trade_confidence` can be judged on evidence
    rather than taste. Unrealized by construction — see `basis` on the payload.
    """
    return await service.scorecard(session, user.id, symbol)


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
