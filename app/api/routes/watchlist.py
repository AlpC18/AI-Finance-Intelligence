"""Authenticated watchlist endpoints."""
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session

from app.core.auth import get_current_user
from app.core.deps import get_watchlist_service, get_ws_broadcaster
from app.db.database import get_session
from app.models.user import User
from app.models.watchlist import WatchlistCreate, WatchlistRead, WatchlistUpdate
from app.services.watchlist_service import WatchlistService

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.post("", response_model=WatchlistRead, status_code=201)
def add_watchlist_item(data: WatchlistCreate, tasks: BackgroundTasks, user: User = Depends(get_current_user),
                       session: Session = Depends(get_session),
                       service: WatchlistService = Depends(get_watchlist_service)) -> WatchlistRead:
    item = service.add(session, user.id, data)
    tasks.add_task(get_ws_broadcaster().publish_activity, user.id, {"type": "activity", "kind": "watchlist"})
    return item


@router.get("", response_model=list[WatchlistRead])
def list_watchlist(user: User = Depends(get_current_user),
                   session: Session = Depends(get_session),
                   service: WatchlistService = Depends(get_watchlist_service)) -> list[WatchlistRead]:
    return service.list(session, user.id)


@router.patch("/{item_id}", response_model=WatchlistRead)
def update_watchlist_item(item_id: int, data: WatchlistUpdate, tasks: BackgroundTasks,
                          user: User = Depends(get_current_user),
                          session: Session = Depends(get_session),
                          service: WatchlistService = Depends(get_watchlist_service)) -> WatchlistRead:
    """Set a research note and/or price target without recreating the item."""
    item = service.update(session, user.id, item_id, data)
    tasks.add_task(get_ws_broadcaster().publish_activity, user.id, {"type": "activity", "kind": "watchlist"})
    return item


@router.delete("/{item_id}", status_code=204)
def remove_watchlist_item(item_id: int, tasks: BackgroundTasks, user: User = Depends(get_current_user),
                          session: Session = Depends(get_session),
                          service: WatchlistService = Depends(get_watchlist_service)) -> None:
    service.remove(session, user.id, item_id)
    tasks.add_task(get_ws_broadcaster().publish_activity, user.id, {"type": "activity", "kind": "watchlist"})
