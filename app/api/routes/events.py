"""Event-driven intelligence endpoint — categorized, impact-scored market events."""
from fastapi import APIRouter, Depends, Request

from app.core.auth import get_current_user
from app.core.deps import get_event_service
from app.core.rate_limit import AI_RATE_LIMIT, limiter
from app.models.event import EventResponse
from app.models.user import User
from app.services.event_intelligence_service import EventIntelligenceService

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=EventResponse)
@limiter.limit(AI_RATE_LIMIT)
async def list_events(
    request: Request,
    user: User = Depends(get_current_user),
    service: EventIntelligenceService = Depends(get_event_service),
) -> EventResponse:
    """Ranked, categorized events (legal/macro/tech/executive) with impact scores."""
    return EventResponse(events=await service.get_events())
