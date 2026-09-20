"""Macroeconomic, Regulatory, and Catalyst News Intelligence API endpoints."""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.routes.auth import get_current_user
from app.models.macro_news_impact import (
    BatchNewsDigestReport,
    NewsEventImpactAnalysis,
)
from app.models.user import User
from app.services.macro_news_analyzer_service import MacroNewsAnalyzerService

router = APIRouter(prefix="/api/macro-news", tags=["macro-news-intelligence"])


class EventAnalysisRequest(BaseModel):
    headline: str
    content: Optional[str] = ""


class BatchDigestRequest(BaseModel):
    headlines: List[str]


@router.post("/analyze-event", response_model=NewsEventImpactAnalysis)
def analyze_breaking_news_event(
    request: EventAnalysisRequest,
    current_user: User = Depends(get_current_user),
) -> NewsEventImpactAnalysis:
    """Analyze breaking news event (Fed rate, SEC lawsuit, Crypto ETF, Earnings shock)."""
    svc = MacroNewsAnalyzerService()
    return svc.analyze_event(headline=request.headline, raw_content=request.content or "")


@router.post("/digest", response_model=BatchNewsDigestReport)
def generate_macro_news_digest(
    request: BatchDigestRequest,
    current_user: User = Depends(get_current_user),
) -> BatchNewsDigestReport:
    """Generate multi-asset macroeconomic news digest and transmission scorecard."""
    svc = MacroNewsAnalyzerService()
    return svc.generate_digest(headlines=request.headlines)


@router.get("/fed-rate-scenario", response_model=NewsEventImpactAnalysis)
def simulate_fed_rate_scenario(
    action: str = Query(default="cut", enum=["cut", "hike", "pause"]),
    basis_points: int = Query(default=25, ge=0, le=100),
    current_user: User = Depends(get_current_user),
) -> NewsEventImpactAnalysis:
    """Simulate market-wide asset impact of a Federal Reserve interest rate decision."""
    svc = MacroNewsAnalyzerService()
    headline = f"Federal Reserve {action}s interest rates by {basis_points} basis points in FOMC policy decision."
    return svc.analyze_event(headline=headline)
