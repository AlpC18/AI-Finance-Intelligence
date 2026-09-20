"""Multi-Agent committee decision endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.routes.auth import get_current_user
from app.core.config import Settings, get_settings
from app.db.database import get_session
from app.models.multi_agent import MultiAgentConsensus, MultiAgentDebateRequest
from app.models.user import User
from app.services.event_intelligence_service import EventIntelligenceService
from app.services.market_service import MarketService
from app.services.multi_agent_service import MultiAgentService

router = APIRouter(prefix="/market/multi-agent", tags=["multi-agent"])


@router.post("/deliberate", response_model=MultiAgentConsensus)
async def run_multi_agent_deliberation(
    request: MultiAgentDebateRequest,
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> MultiAgentConsensus:
    """Run collaborative multi-agent deliberation on a symbol."""
    market_svc = MarketService(settings)
    try:
        quote = await market_svc.get_quote(request.symbol)
        indicators = await market_svc.get_indicators(request.symbol)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Market data unavailable for symbol {request.symbol}: {exc}"
        ) from exc

    fundamentals = None
    try:
        fundamentals = await market_svc.get_fundamentals(request.symbol)
    except Exception:
        fundamentals = None

    event_svc = EventIntelligenceService()
    events = event_svc.get_active_events_for_symbol(request.symbol)

    multi_agent_svc = MultiAgentService(settings)
    consensus = await multi_agent_svc.deliberate(
        symbol=request.symbol,
        quote=quote,
        indicators=indicators,
        fundamentals=fundamentals,
        events=events,
        risk_tolerance=request.risk_tolerance,
    )
    return consensus
