"""Consumer API keys: hashed at rest, scoped, and only revealed at creation."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField
from sqlmodel import Field, SQLModel

ApiScope = Literal["read:portfolio", "read:market", "read:activity", "webhook:receive"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApiConsumerKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    name: str
    key_prefix: str = Field(index=True, unique=True)
    key_hash: str = Field(unique=True)
    scopes_csv: str
    is_active: bool = Field(default=True, index=True)
    request_count: int = Field(default=0)
    last_used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)
    revoked_at: Optional[datetime] = None


class ApiKeyCreate(BaseModel):
    name: str = PField(min_length=1, max_length=80)
    scopes: set[ApiScope] = PField(min_length=1)


class ApiKeyRead(BaseModel):
    id: int
    name: str
    key_prefix: str
    scopes: list[ApiScope]
    is_active: bool
    request_count: int
    last_used_at: Optional[datetime]
    created_at: datetime
    revoked_at: Optional[datetime]


class ApiKeyCreated(ApiKeyRead):
    """`secret` is returned exactly once and is never persisted."""

    secret: str
