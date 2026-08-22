"""Backtesting endpoint — replays historical bars through the signal rules."""
from fastapi import APIRouter, Depends, Request

from app.core.auth import get_current_user
from app.core.deps import get_backtest_service
from app.core.rate_limit import AI_RATE_LIMIT, limiter
from app.models.backtest import BacktestReport, BacktestRequest
from app.models.user import User
from app.services.backtest_service import BacktestService

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/run", response_model=BacktestReport)
@limiter.limit(AI_RATE_LIMIT)
async def run_backtest(
    request: Request,
    data: BacktestRequest,
    user: User = Depends(get_current_user),
    service: BacktestService = Depends(get_backtest_service),
) -> BacktestReport:
    """Historical simulation: metrics summary + equity curve for the symbol."""
    return await service.run(data)
