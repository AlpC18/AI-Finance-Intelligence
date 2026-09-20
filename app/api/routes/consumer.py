"""Scoped external-consumer surface authenticated by opaque API keys."""
from fastapi import APIRouter, Depends, Header
from sqlmodel import Session

from app.core.deps import get_activity_service, get_consumer_service
from app.db.database import get_session
from app.models.activity import ActivityPage
from app.services.activity_service import ActivityService
from app.services.consumer_service import ConsumerService

router = APIRouter(prefix="/api/consumer", tags=["consumer"])


@router.get("/activity", response_model=ActivityPage)
def consumer_activity(
    x_api_key: str = Header(default=""),
    session: Session = Depends(get_session),
    consumers: ConsumerService = Depends(get_consumer_service),
    activity: ActivityService = Depends(get_activity_service),
) -> ActivityPage:
    """Example scoped consumer endpoint; usage is accounted at authentication."""
    key = consumers.authenticate(session, x_api_key, "read:activity")
    return activity.list(session, key.user_id, limit=50, offset=0)
