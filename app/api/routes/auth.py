"""Authentication endpoints: register, login, refresh, logout (JWT + blocklist)."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlmodel import Session

from app.core.auth import bearer_scheme, get_current_user
from app.core.config import get_settings
from app.core.deps import get_activity_service, get_auth_service, get_token_store
from app.core.rate_limit import LOGIN_RATE_LIMIT, REGISTER_RATE_LIMIT, limiter
from app.core.security import decode_token, remaining_ttl_seconds
from app.core.token_store import TokenBlocklist
from app.db.database import get_session
from app.models.user import (
    LogoutRequest,
    RefreshRequest,
    TokenPair,
    User,
    UserCreate,
    UserLogin,
    UserRead,
)
from app.services.auth_service import AuthService
from app.services.activity_service import ActivityService

router = APIRouter(prefix="/api/auth", tags=["auth"])

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Gecersiz veya suresi dolmus token."
)


@router.post("/register", response_model=UserRead, status_code=201)
@limiter.limit(REGISTER_RATE_LIMIT)
def register(
    request: Request,
    data: UserCreate,
    session: Session = Depends(get_session),
    service: AuthService = Depends(get_auth_service),
    activity: ActivityService = Depends(get_activity_service),
) -> UserRead:
    """Create an account. IP-capped: bulk registration is how throwaway
    accounts get farmed, and there is no legitimate caller who needs to open
    accounts faster than a person can fill in a form."""
    user = service.register(session, data)
    activity.record(session, user.id, "security", "Account registered")
    session.commit()
    return UserRead(id=user.id, email=user.email, created_at=user.created_at)


@router.post("/login", response_model=TokenPair)
@limiter.limit(LOGIN_RATE_LIMIT)
def login(
    request: Request,
    data: UserLogin,
    session: Session = Depends(get_session),
    service: AuthService = Depends(get_auth_service),
    activity: ActivityService = Depends(get_activity_service),
) -> TokenPair:
    """Exchange credentials for a token pair.

    The cap is the only thing standing between a leaked password list and this
    endpoint: without it an attacker can test credentials as fast as the server
    answers, and bcrypt alone just makes that expensive for US too. Keyed by IP
    because an unauthenticated caller has no user identity to key on.

    The failure response is deliberately identical for an unknown email and a
    wrong password - a distinguishable answer turns this into an account
    enumeration oracle.
    """
    user = service.authenticate(session, data.email, data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="E-posta veya sifre hatali."
        )
    activity.record(session, user.id, "security", "Successful login")
    session.commit()
    return service.issue_tokens(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest,
    session: Session = Depends(get_session),
    service: AuthService = Depends(get_auth_service),
    blocklist: TokenBlocklist = Depends(get_token_store),
) -> TokenPair:
    settings = get_settings()
    payload = decode_token(body.refresh_token, settings)
    if not payload or payload.get("type") != "refresh":
        raise _UNAUTHORIZED
    jti = payload.get("jti")
    if jti and await blocklist.is_blocked(jti):
        raise _UNAUTHORIZED
    try:
        user = session.get(User, int(payload["sub"]))
    except (KeyError, TypeError, ValueError):
        raise _UNAUTHORIZED
    if user is None:
        raise _UNAUTHORIZED
    # Rotate: revoke the presented refresh token so it can't be replayed.
    if jti:
        await blocklist.block(jti, remaining_ttl_seconds(payload))
    return service.issue_tokens(user)


@router.post("/logout", status_code=204)
async def logout(
    body: LogoutRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    _user: User = Depends(get_current_user),
    blocklist: TokenBlocklist = Depends(get_token_store),
) -> None:
    settings = get_settings()
    if credentials and credentials.credentials:
        access = decode_token(credentials.credentials, settings)
        if access and access.get("jti"):
            await blocklist.block(access["jti"], remaining_ttl_seconds(access))
    if body.refresh_token:
        refresh_payload = decode_token(body.refresh_token, settings)
        if (
            refresh_payload
            and refresh_payload.get("type") == "refresh"
            and refresh_payload.get("jti")
        ):
            await blocklist.block(
                refresh_payload["jti"], remaining_ttl_seconds(refresh_payload)
            )
