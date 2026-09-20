"""Deep module for durable account activity and a small timeline interface."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.activity import ActivityEvent, ActivityEventRead, ActivityPage


class ActivityService:
    """Write once, then list a tenant-scoped reverse-chronological timeline."""

    def record(
        self, session: Session, user_id: int, kind: str, summary: str,
        *, severity: int = 1, payload: dict[str, Any] | None = None,
    ) -> ActivityEvent:
        event = ActivityEvent(
            user_id=user_id,
            kind=kind,
            severity=severity,
            summary=summary[:500],
            payload_json=json.dumps(payload or {}, separators=(",", ":"), default=str),
        )
        session.add(event)
        return event

    def list(self, session: Session, user_id: int, limit: int, offset: int) -> ActivityPage:
        condition = ActivityEvent.user_id == user_id
        total = int(session.exec(select(func.count()).select_from(ActivityEvent).where(condition)).one())
        rows = session.exec(
            select(ActivityEvent).where(condition)
            .order_by(ActivityEvent.created_at.desc(), ActivityEvent.id.desc())
            .offset(offset).limit(limit)
        ).all()
        return ActivityPage(total=total, limit=limit, offset=offset, items=[_read(row) for row in rows])


def _read(row: ActivityEvent) -> ActivityEventRead:
    try:
        payload = json.loads(row.payload_json)
    except (TypeError, ValueError):
        payload = {}
    return ActivityEventRead(
        id=row.id,
        kind=row.kind,
        severity=row.severity,
        summary=row.summary,
        payload=payload if isinstance(payload, dict) else {},
        created_at=row.created_at,
    )
