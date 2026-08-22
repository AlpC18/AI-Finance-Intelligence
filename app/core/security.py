"""Password hashing (bcrypt) and JWT (access + refresh) with jti for revocation."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import bcrypt
import jwt

from app.core.config import Settings

TokenType = Literal["access", "refresh"]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _create_token(
    subject: str, token_type: TokenType, expires_delta: timedelta, settings: Settings
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, settings: Settings) -> str:
    return _create_token(
        subject,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
        settings,
    )


def create_refresh_token(subject: str, settings: Settings) -> str:
    return _create_token(
        subject, "refresh", timedelta(days=settings.refresh_token_expire_days), settings
    )


def decode_token(token: str, settings: Settings) -> Optional[dict]:
    try:
        return jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None


def remaining_ttl_seconds(payload: dict) -> int:
    """Seconds until this token expires (>=0), for setting the blocklist TTL."""
    exp = payload.get("exp")
    if not exp:
        return 0
    delta = int(exp - datetime.now(timezone.utc).timestamp())
    return max(delta, 0)
