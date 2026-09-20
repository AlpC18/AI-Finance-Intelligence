"""Deep watchlist module: canonical symbols, ownership and duplicate handling."""
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError, NotFoundError
from app.models.watchlist import WatchlistCreate, WatchlistItem, WatchlistUpdate
from app.services.activity_service import ActivityService


class WatchlistService:
    def __init__(self, activity: ActivityService | None = None) -> None:
        self._activity = activity

    def add(self, session: Session, user_id: int, data: WatchlistCreate) -> WatchlistItem:
        symbol = data.symbol.upper().strip()
        item = WatchlistItem(
            user_id=user_id,
            symbol=symbol,
            note=data.note.strip(),
            target_price=data.target_price,
        )
        session.add(item)
        self._record(session, user_id, "Watchlist sembolü eklendi", {
            "action": "added", "symbol": symbol,
        })
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise AppError("Sembol zaten watchlist'te.", status_code=409, reason="watchlist_duplicate") from exc
        session.refresh(item)
        return item

    def list(self, session: Session, user_id: int) -> list[WatchlistItem]:
        return list(session.exec(
            select(WatchlistItem).where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.created_at.desc())
        ).all())

    def remove(self, session: Session, user_id: int, item_id: int) -> None:
        item = session.get(WatchlistItem, item_id)
        if item is None or item.user_id != user_id:
            raise NotFoundError("Watchlist kaydi bulunamadi.")
        session.delete(item)
        self._record(session, user_id, "Watchlist sembolü kaldırıldı", {
            "action": "removed", "symbol": item.symbol,
        })
        session.commit()

    def update(
        self, session: Session, user_id: int, item_id: int, data: WatchlistUpdate
    ) -> WatchlistItem:
        item = session.get(WatchlistItem, item_id)
        if item is None or item.user_id != user_id:
            raise NotFoundError("Watchlist kaydi bulunamadi.")
        updates = data.model_dump(exclude_unset=True)
        if "note" in updates:
            item.note = updates["note"].strip()
        if "target_price" in updates:
            item.target_price = updates["target_price"]
        session.add(item)
        self._record(session, user_id, "Watchlist sembolü güncellendi", {
            "action": "updated", "symbol": item.symbol,
        })
        session.commit()
        session.refresh(item)
        return item

    def _record(self, session: Session, user_id: int, summary: str, payload: dict) -> None:
        if self._activity is not None:
            self._activity.record(session, user_id, "watchlist", summary, payload=payload)
