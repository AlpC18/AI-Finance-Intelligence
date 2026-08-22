"""Registration + authentication + token issuance (access + refresh)."""
from typing import Optional

from sqlmodel import Session, select

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.models.user import TokenPair, User, UserCreate


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def register(self, session: Session, data: UserCreate) -> User:
        email = data.email.strip().lower()
        existing = session.exec(select(User).where(User.email == email)).first()
        if existing is not None:
            raise AppError("Bu e-posta zaten kayitli.", status_code=409)
        user = User(email=email, hashed_password=hash_password(data.password))
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    def authenticate(
        self, session: Session, email: str, password: str
    ) -> Optional[User]:
        user = session.exec(
            select(User).where(User.email == email.strip().lower())
        ).first()
        if user is None or not verify_password(password, user.hashed_password):
            return None
        return user

    def issue_tokens(self, user: User) -> TokenPair:
        subject = str(user.id)
        return TokenPair(
            access_token=create_access_token(subject, self._settings),
            refresh_token=create_refresh_token(subject, self._settings),
        )
