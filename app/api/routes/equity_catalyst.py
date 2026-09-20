"""Equity Specific Catalysts & Stock Trajectory Intelligence API endpoints."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.routes.auth import get_current_user
from app.models.equity_catalyst import StockCatalystAnalysis
from app.models.user import User
from app.services.stock_catalyst_service import StockCatalystAnalyzerService

router = APIRouter(prefix="/api/equity-catalysts", tags=["equity-catalyst-intelligence"])


class StockNewsAnalysisRequest(BaseModel):
    symbol: str
    headline: str
    content: Optional[str] = ""
    current_price: Optional[float] = 100.0


@router.post("/analyze", response_model=StockCatalystAnalysis)
def analyze_stock_catalyst(
    request: StockNewsAnalysisRequest,
    current_user: User = Depends(get_current_user),
) -> StockCatalystAnalysis:
    """Analyze company-specific news to compute expected stock price surge, drop, or contagion."""
    svc = StockCatalystAnalyzerService()
    return svc.analyze_stock_news(
        symbol=request.symbol,
        headline=request.headline,
        raw_content=request.content or "",
        current_price=request.current_price or 100.0,
    )


@router.get("/quick-scan/{symbol}", response_model=StockCatalystAnalysis)
def quick_scan_stock_catalyst(
    symbol: str,
    scenario: str = Query(default="earnings_beat", enum=["earnings_beat", "earnings_miss", "fda_approval", "short_report", "buyout_offer", "buyback"]),
    current_user: User = Depends(get_current_user),
) -> StockCatalystAnalysis:
    """Simulates or scans predefined high-conviction catalysts for any equity ticker."""
    svc = StockCatalystAnalyzerService()
    headlines = {
        "earnings_beat": f"{symbol.upper()} reports record Q2 revenue beat and raises full-year profit guidance by 22%.",
        "earnings_miss": f"{symbol.upper()} cuts full-year revenue outlook; profit margins slump on slowing enterprise demand.",
        "fda_approval": f"FDA approves {symbol.upper()} breakthrough oncology therapy following stellar Phase 3 trial success.",
        "short_report": f"Short seller Hindenburg issues forensic fraud report alleging aggressive accounting irregularities at {symbol.upper()}.",
        "buyout_offer": f"Private equity syndicate submits $45B acquisition buyout offer for {symbol.upper()} at 35% premium.",
        "buyback": f"{symbol.upper()} board authorizes new $25 Billion share repurchase buyback program.",
    }
    headline = headlines.get(scenario, f"{symbol.upper()} issues corporate operational update.")
    return svc.analyze_stock_news(symbol=symbol, headline=headline)
