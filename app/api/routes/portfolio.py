"""Portfolio endpoints — ledger-driven holdings, P&L, and risk. Auth required."""
from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import get_portfolio_service, get_quant_service
from app.core.rate_limit import AI_RATE_LIMIT, limiter
from app.db.database import get_session
from app.models.portfolio import Advice, RiskReport
from app.models.quant import CorrelationMatrix, OptimizationReport
from app.models.transaction import Holding, TransactionCreate, TransactionRead
from app.models.user import User
from app.services.portfolio_service import PortfolioService
from app.services.quant_service import QuantService

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.post("/transactions", response_model=TransactionRead, status_code=201)
def record_transaction(
    data: TransactionCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: PortfolioService = Depends(get_portfolio_service),
) -> TransactionRead:
    return service.record_transaction(session, data, user.id)


@router.get("/transactions", response_model=list[TransactionRead])
def list_transactions(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: PortfolioService = Depends(get_portfolio_service),
) -> list[TransactionRead]:
    return service.list_transactions(session, user.id)


@router.get("", response_model=list[Holding])
def holdings(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: PortfolioService = Depends(get_portfolio_service),
) -> list[Holding]:
    """Current net positions reconstructed from the ledger."""
    return service.active_holdings(session, user.id)


@router.get("/risk", response_model=RiskReport)
@limiter.limit(AI_RATE_LIMIT)
async def risk_report(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: PortfolioService = Depends(get_portfolio_service),
) -> RiskReport:
    """Instant quant risk + realized/unrealized P&L from the ledger."""
    return await service.risk_report(session, user.id)


@router.get("/risk/ai-insights", response_model=Advice)
@limiter.limit(AI_RATE_LIMIT)
async def risk_ai_insights(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: PortfolioService = Depends(get_portfolio_service),
) -> Advice:
    return await service.risk_advice(session, user.id)


@router.get("/correlation", response_model=CorrelationMatrix)
@limiter.limit(AI_RATE_LIMIT)
async def correlation(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    quant: QuantService = Depends(get_quant_service),
) -> CorrelationMatrix:
    """N x N return-correlation matrix across the user's active holdings."""
    return await quant.correlation(session, user.id)


@router.get("/optimize", response_model=OptimizationReport)
@limiter.limit(AI_RATE_LIMIT)
async def optimize(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    quant: QuantService = Depends(get_quant_service),
) -> OptimizationReport:
    """Markowitz max-Sharpe target weights vs. the current allocation."""
    return await quant.optimize(session, user.id)
