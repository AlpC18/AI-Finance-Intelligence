"""News endpoints: instant raw articles + on-demand AI insight (rate-limited)."""
from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import get_news_service
from app.core.rate_limit import AI_RATE_LIMIT, limiter
from app.models.news import NewsInsight, NewsResponse
from app.services.news_service import NewsService

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("", response_model=NewsResponse)
async def news(
    query: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(10, ge=1, le=25),
    service: NewsService = Depends(get_news_service),
) -> NewsResponse:
    return await service.get_news(query, limit)


@router.get("/ai-insights", response_model=NewsInsight)
@limiter.limit(AI_RATE_LIMIT)
async def news_ai_insights(
    request: Request,
    query: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(10, ge=1, le=25),
    service: NewsService = Depends(get_news_service),
) -> NewsInsight:
    return await service.get_insight(query, limit)
