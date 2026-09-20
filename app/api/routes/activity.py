"""Authenticated append-only account activity timeline."""
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import get_activity_service
from app.db.database import get_session
from app.models.activity import ActivityPage
from app.models.user import User
from app.services.activity_service import ActivityService

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("", response_model=ActivityPage)
def list_activity(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: ActivityService = Depends(get_activity_service),
) -> ActivityPage:
    """Durable timeline for live and historical account activity."""
    return service.list(session, user.id, limit, offset)
