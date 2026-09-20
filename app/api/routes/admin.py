"""Fail-closed operator overview for a configured admin allowlist."""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.database import get_session
from app.models.activity import ActivityEvent
from app.models.admin import AdminOverview
from app.models.consumer import ApiConsumerKey
from app.models.order import TradeOrder
from app.models.user import User

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _is_admin(email: str) -> bool:
    allowed = {entry.strip().lower() for entry in get_settings().admin_emails.split(",") if entry.strip()}
    return bool(allowed) and email.lower() in allowed


@router.get("/overview", response_model=AdminOverview)
def overview(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> AdminOverview:
    if not _is_admin(user.email):
        raise AppError("Yönetici yetkisi gerekli.", status_code=403, reason="admin_required")
    settings = get_settings()
    return AdminOverview(
        users=int(session.exec(select(func.count()).select_from(User)).one()),
        orders=int(session.exec(select(func.count()).select_from(TradeOrder)).one()),
        active_api_keys=int(session.exec(select(func.count()).select_from(ApiConsumerKey).where(ApiConsumerKey.is_active == True)).one()),  # noqa: E712
        activity_events=int(session.exec(select(func.count()).select_from(ActivityEvent)).one()),
        trading_mode=settings.trading_mode,
        cache_backend=settings.cache_backend,
    )
