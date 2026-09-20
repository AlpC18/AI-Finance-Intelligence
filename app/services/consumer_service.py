"""Deep module for opaque consumer credentials, scopes, rotation, and usage."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.errors import AppError, NotFoundError
from app.models.consumer import ApiConsumerKey, ApiKeyCreate, ApiKeyCreated, ApiKeyRead

_PREFIX = "afi_"


class ConsumerService:
    def create(self, session: Session, user_id: int, data: ApiKeyCreate) -> ApiKeyCreated:
        secret = f"{_PREFIX}{secrets.token_urlsafe(32)}"
        row = ApiConsumerKey(
            user_id=user_id,
            name=data.name.strip(),
            key_prefix=secret[:12],
            key_hash=_hash(secret),
            scopes_csv=",".join(sorted(data.scopes)),
        )
        session.add(row); session.commit(); session.refresh(row)
        return ApiKeyCreated(**_read(row).model_dump(), secret=secret)

    def list(self, session: Session, user_id: int) -> list[ApiKeyRead]:
        rows = session.exec(
            select(ApiConsumerKey).where(ApiConsumerKey.user_id == user_id)
            .order_by(ApiConsumerKey.created_at.desc())
        ).all()
        return [_read(row) for row in rows]

    def revoke(self, session: Session, user_id: int, key_id: int) -> None:
        row = session.get(ApiConsumerKey, key_id)
        if row is None or row.user_id != user_id:
            raise NotFoundError("API anahtari bulunamadi.")
        if row.is_active:
            row.is_active = False
            row.revoked_at = datetime.now(timezone.utc)
            session.add(row); session.commit()

    def authenticate(self, session: Session, secret: str, scope: str) -> ApiConsumerKey:
        if not secret.startswith(_PREFIX):
            raise AppError("Gecersiz API anahtari.", status_code=401, reason="api_key_invalid")
        row = session.exec(select(ApiConsumerKey).where(ApiConsumerKey.key_hash == _hash(secret))).first()
        if row is None or not row.is_active:
            raise AppError("Gecersiz API anahtari.", status_code=401, reason="api_key_invalid")
        scopes = _scopes(row)
        if scope not in scopes:
            raise AppError("API anahtarinin gerekli yetkisi yok.", status_code=403, reason="api_key_scope")
        row.request_count += 1
        row.last_used_at = datetime.now(timezone.utc)
        session.add(row); session.commit()
        return row


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _scopes(row: ApiConsumerKey) -> list[str]:
    return [scope for scope in row.scopes_csv.split(",") if scope]


def _read(row: ApiConsumerKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=row.id, name=row.name, key_prefix=row.key_prefix, scopes=_scopes(row),
        is_active=row.is_active, request_count=row.request_count,
        last_used_at=row.last_used_at, created_at=row.created_at, revoked_at=row.revoked_at,
    )
