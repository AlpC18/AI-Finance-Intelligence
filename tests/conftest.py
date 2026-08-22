"""Test fixtures: async fake providers, disabled AI, in-memory DB, auth helpers."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.core.config import Settings
from app.core.deps import (
    get_ai_service,
    get_connection_manager,
    get_market_service,
    get_news_service,
    get_portfolio_service,
    get_token_store,
    get_alert_service,
)
from app.api.websockets import ConnectionManager
from app.core.token_store import InMemoryBlocklist
from app.db.database import get_session
from app.main import create_app
from app.models.news import Article
from app.services.ai_service import AIService
from app.services.alert_service import AlertService
from app.services.market_service import MarketService
from app.services.news_service import NewsService
from app.services.portfolio_service import PortfolioService


def _fake_history() -> pd.DataFrame:
    rng = np.linspace(100, 120, 80) + np.sin(np.linspace(0, 10, 80))
    return pd.DataFrame({"Close": rng})


class _FakeMarketProvider:
    async def get_history(self, symbol, period="6mo"):
        return _fake_history()

    async def get_price(self, symbol):
        return float(_fake_history()["Close"].iloc[-1])


class _FakeNewsProvider:
    async def fetch(self, query, limit=10):
        return [
            Article(title=f"{query} rallies on strong earnings", link="http://x/1"),
            Article(title=f"{query} faces regulatory scrutiny", link="http://x/2"),
        ][:limit]


class _NoopCache:
    async def get(self, key):
        return None

    async def set(self, key, value, ttl=None):
        return None

    async def get_or_set(self, key, factory, ttl=None):
        return await factory()


@pytest.fixture
def disabled_ai() -> AIService:
    return AIService(Settings(anthropic_api_key=""))


@pytest.fixture
def client(disabled_ai):
    app = create_app()
    app.state.limiter.enabled = False  # don't rate-limit during tests
    app.state.enable_scheduler = False  # never start the background scheduler in tests

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    app.state.test_engine = engine  # exposed for the E2E flow test

    def _session_override():
        with Session(engine) as session:
            yield session

    market = MarketService(_FakeMarketProvider(), disabled_ai, _NoopCache())
    news = NewsService(_FakeNewsProvider(), disabled_ai, _NoopCache())
    portfolio = PortfolioService(_FakeMarketProvider(), disabled_ai)

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_market_service] = lambda: market
    app.dependency_overrides[get_news_service] = lambda: news
    app.dependency_overrides[get_portfolio_service] = lambda: portfolio
    blocklist = InMemoryBlocklist()
    app.dependency_overrides[get_token_store] = lambda: blocklist
    app.dependency_overrides[get_alert_service] = lambda: AlertService(market)
    app.dependency_overrides[get_ai_service] = lambda: disabled_ai
    app.dependency_overrides[get_connection_manager] = lambda: ConnectionManager()

    with TestClient(app) as c:
        yield c


def _register_and_login(client: TestClient, email: str, password: str = "password123") -> dict:
    client.post("/api/auth/register", json={"email": email, "password": password})
    tok = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def auth_headers(client):
    return _register_and_login(client, "a@example.com")


@pytest.fixture
def make_user(client):
    def _make(email: str):
        return _register_and_login(client, email)

    return _make
