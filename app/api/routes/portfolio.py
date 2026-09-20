"""Portfolio endpoints — ledger-driven holdings, P&L, and risk. Auth required."""
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.csv_export import content_disposition, to_csv
from app.core.deps import (
    get_performance_service,
    get_portfolio_service,
    get_quant_service,
    get_tax_lot_service,
    get_goal_service,
    get_rebalance_service,
)
from app.core.rate_limit import AI_RATE_LIMIT, limiter
from app.db.database import get_session
from app.models.performance import PerformanceReport
from app.models.portfolio import Advice, RiskReport
from app.models.quant import (
    CorrelationMatrix,
    MonteCarloReport,
    MonteCarloRequest,
    OptimizationReport,
)
from app.models.transaction import Holding, TransactionCreate, TransactionRead
from app.models.user import User
from app.models.tax import LotMethod, TaxLotReport
from app.services.tax_lot_service import TaxLotService
from app.models.goals import PortfolioGoalProgress, PortfolioGoalUpdate
from app.models.rebalance import RebalancePlan, RebalanceRequest
from app.services.goal_service import GoalService
from app.services.rebalance_service import RebalanceService
from datetime import datetime, timezone
from app.services.performance_service import PerformanceService
from app.services.portfolio_service import PortfolioService
from app.services.quant_service import QuantService

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.put("/goal", response_model=PortfolioGoalProgress)
async def set_goal(data: PortfolioGoalUpdate, user: User = Depends(get_current_user),
                   session: Session = Depends(get_session),
                   service: GoalService = Depends(get_goal_service)) -> PortfolioGoalProgress:
    service.set(session, user.id, data)
    return await service.progress(session, user.id)


@router.get("/goal", response_model=PortfolioGoalProgress)
async def goal_progress(user: User = Depends(get_current_user), session: Session = Depends(get_session),
                        service: GoalService = Depends(get_goal_service)) -> PortfolioGoalProgress:
    return await service.progress(session, user.id)


@router.post("/rebalance/plan", response_model=RebalancePlan)
async def rebalance_plan(data: RebalanceRequest, user: User = Depends(get_current_user),
                         session: Session = Depends(get_session),
                         service: RebalanceService = Depends(get_rebalance_service)) -> RebalancePlan:
    return await service.plan(session, user.id, data)


@router.get("/tax-lots", response_model=TaxLotReport)
def tax_lots(
    year: int = Query(default_factory=lambda: datetime.now(timezone.utc).year, ge=2000, le=2100),
    method: LotMethod = Query(default="FIFO"),
    user: User = Depends(get_current_user), session: Session = Depends(get_session),
    service: TaxLotService = Depends(get_tax_lot_service),
) -> TaxLotReport:
    """Realized gain/loss and remaining tax lots from the immutable ledger."""
    return service.report(session, user.id, year, method)


@router.get("/export/tax-lots", response_class=PlainTextResponse)
def export_tax_lots(
    year: int = Query(default_factory=lambda: datetime.now(timezone.utc).year, ge=2000, le=2100),
    method: LotMethod = Query(default="FIFO"),
    user: User = Depends(get_current_user), session: Session = Depends(get_session),
    service: TaxLotService = Depends(get_tax_lot_service),
) -> PlainTextResponse:
    report = service.report(session, user.id, year, method)
    body = to_csv(
        ["symbol", "acquired_at", "sold_at", "quantity", "cost_basis", "proceeds", "gain_loss", "term"],
        [(r.symbol, r.acquired_at.isoformat(), r.sold_at.isoformat(), r.quantity,
          r.cost_basis, r.proceeds, r.gain_loss, r.term) for r in report.realized],
    )
    return PlainTextResponse(body, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": content_disposition(f"tax-lots-{year}-{method.lower()}.csv"),
    })


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
    response: Response,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: PortfolioService = Depends(get_portfolio_service),
) -> list[TransactionRead]:
    """A page of the ledger, oldest first.

    Bounded by default so one account's history can never be an unbounded
    query. `X-Total-Count` carries the full size, so a client can tell a
    complete answer from a truncated one without the response shape changing.
    """
    response.headers["X-Total-Count"] = str(
        service.count_transactions(session, user.id)
    )
    return service.list_transactions(session, user.id, limit=limit, offset=offset)


@router.get("/export/transactions", response_class=PlainTextResponse)
def export_transactions(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: PortfolioService = Depends(get_portfolio_service),
) -> PlainTextResponse:
    """The FULL ledger as CSV, for tax filing and audit.

    Deliberately unpaginated: a partial export is worse than none for the
    purpose, and this is a download rather than a UI read. Cells are escaped
    against spreadsheet formula injection on the way out.
    """
    rows = service.list_transactions(session, user.id)
    body = to_csv(
        ["timestamp", "symbol", "action", "quantity", "price", "value"],
        [
            (
                t.timestamp.isoformat(), t.symbol, t.action,
                t.quantity, t.price, round(t.quantity * t.price, 2),
            )
            for t in rows
        ],
    )
    return PlainTextResponse(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition("transactions.csv")},
    )


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


@router.post("/monte-carlo", response_model=MonteCarloReport)
@limiter.limit(AI_RATE_LIMIT)
async def monte_carlo(
    request: Request,
    data: MonteCarloRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    quant: QuantService = Depends(get_quant_service),
) -> MonteCarloReport:
    """Historical-bootstrap terminal-value distribution for active holdings."""
    return await quant.monte_carlo(session, user.id, data)


@router.get("/performance", response_model=PerformanceReport)
async def performance(
    days: int = Query(default=365, ge=1, le=3650),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: PerformanceService = Depends(get_performance_service),
) -> PerformanceReport:
    """Equity curve, return, and max drawdown from the daily equity snapshots.

    Snapshots are captured lazily (once per active UTC day), so the series can
    have gaps; `sparse` flags that, and the gaps are left unobserved rather
    than interpolated flat.
    """
    return await service.report(session, user.id, days)
