"""Auth dependency: resolves current user from a Bearer access token or 401s.

Enforces token type == access and rejects revoked (blocklisted) tokens.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.config import get_settings
from app.core.deps import get_token_store
from app.core.security import decode_token
from app.core.token_store import TokenBlocklist
from app.db.database import get_session
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Kimlik dogrulama gerekli.",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
    blocklist: TokenBlocklist = Depends(get_token_store),
) -> User:
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED
    payload = decode_token(credentials.credentials, get_settings())
    if not payload or payload.get("type") != "access":
        raise _UNAUTHORIZED
    jti = payload.get("jti")
    if jti and await blocklist.is_blocked(jti):
        raise _UNAUTHORIZED
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise _UNAUTHORIZED
    user = session.get(User, user_id)
    if user is None:
        raise _UNAUTHORIZED
    return user
