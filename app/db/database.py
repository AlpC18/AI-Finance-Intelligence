"""SQLite engine/session via SQLModel."""
from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings

_settings = get_settings()
_connect_args = {"check_same_thread": False} if "sqlite" in _settings.database_url else {}
engine = create_engine(_settings.database_url, connect_args=_connect_args)


def init_db() -> None:
    """Create tables. Import models so they register on SQLModel.metadata."""
    from app.models import alert, broker, order, risk, transaction, user  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
