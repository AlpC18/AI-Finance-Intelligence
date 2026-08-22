"""Alert CRUD endpoints. Auth required; scoped to the current user."""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import get_alert_service
from app.db.database import get_session
from app.models.alert import Alert, AlertCreate, AlertRead
from app.models.user import User
from app.services.alert_service import AlertService

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post("", response_model=AlertRead, status_code=201)
def create_alert(
    data: AlertCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: AlertService = Depends(get_alert_service),
) -> Alert:
    return service.create(session, data, user.id)


@router.get("", response_model=list[AlertRead])
def list_alerts(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: AlertService = Depends(get_alert_service),
) -> list[Alert]:
    return service.list(session, user.id)


@router.delete("/{alert_id}", status_code=204)
def delete_alert(
    alert_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    service: AlertService = Depends(get_alert_service),
) -> None:
    service.delete(session, alert_id, user.id)
