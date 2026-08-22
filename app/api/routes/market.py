"""Market endpoints: instant quant data + on-demand AI signal. Rate-limited."""
from fastapi import APIRouter, Depends, Request

from app.core.deps import get_market_service
from app.core.rate_limit import AI_RATE_LIMIT, limiter
from app.models.market import MarketData, Signal
from app.services.market_service import MarketService

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/{symbol}", response_model=MarketData)
@limiter.limit(AI_RATE_LIMIT)
async def market_data(
    request: Request,
    symbol: str,
    service: MarketService = Depends(get_market_service),
) -> MarketData:
    return await service.get_market_data(symbol)


@router.get("/{symbol}/ai-insights", response_model=Signal)
@limiter.limit(AI_RATE_LIMIT)
async def market_ai_insights(
    request: Request,
    symbol: str,
    service: MarketService = Depends(get_market_service),
) -> Signal:
    return await service.get_signal(symbol)
