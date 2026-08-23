"""Rate limiting — who gets charged for a request, and what happens at the cap.

Every other test in this suite runs with the limiter switched off (conftest
sets `limiter.enabled = False`), which left the whole mechanism unexercised.
Two things are worth proving:

  the key ..... `user_or_ip_key` decides WHOSE quota a request spends. If it
                falls back to IP where it should identify a user, everyone
                behind one NAT shares a bucket; if it identifies a user from a
                token it should not trust, a caller picks their own bucket.
  the cap ..... the limit actually rejects with 429 once spent, and one user
                being capped does not lock out anybody else.

The expensive endpoints are the AI ones, so this is budget protection as much
as abuse protection.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool
from starlette.requests import Request

from app.core.config import Settings, get_settings
from app.core.rate_limit import user_or_ip_key
from app.core.security import create_access_token, create_refresh_token


def _request(headers: dict | None = None, client_ip: str = "203.0.113.7") -> Request:
    """A minimal ASGI scope — all `user_or_ip_key` reads is headers + client."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({
        "type": "http", "method": "GET", "path": "/", "headers": raw,
        "client": (client_ip, 12345), "scheme": "http", "server": ("test", 80),
        "query_string": b"", "root_path": "", "app": None,
    })


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ============================== THE KEY ====================================

def test_an_authenticated_caller_is_keyed_by_user_id():
    token = create_access_token("42", get_settings())

    assert user_or_ip_key(_request(_bearer(token))) == "user:42"


def test_two_users_get_separate_buckets():
    s = get_settings()
    one = user_or_ip_key(_request(_bearer(create_access_token("1", s))))
    two = user_or_ip_key(_request(_bearer(create_access_token("2", s))))

    assert one != two


def test_the_same_user_from_two_addresses_shares_one_bucket():
    """Quota follows the account, so rotating IPs must not multiply it."""
    token = create_access_token("42", get_settings())

    a = user_or_ip_key(_request(_bearer(token), client_ip="198.51.100.1"))
    b = user_or_ip_key(_request(_bearer(token), client_ip="203.0.113.9"))

    assert a == b == "user:42"


def test_an_anonymous_caller_falls_back_to_their_address():
    assert user_or_ip_key(_request(client_ip="198.51.100.4")) == "198.51.100.4"


@pytest.mark.parametrize("header", [
    {"Authorization": "Bearer not-a-jwt"},
    {"Authorization": "Bearer "},
    {"Authorization": "Basic abc123"},
    {"Authorization": "bearer lowercase-scheme"},
    {"Authorization": ""},
])
def test_an_unusable_authorization_header_falls_back_to_the_address(header):
    """A caller must never pick their own bucket with a token we cannot verify."""
    assert user_or_ip_key(_request(header, client_ip="198.51.100.5")) == "198.51.100.5"


def test_a_token_signed_by_someone_else_is_not_trusted():
    """The signature is what separates a claim from an identity."""
    forged = create_access_token(
        "999", Settings(anthropic_api_key="", jwt_secret="a" * 40)
    )

    key = user_or_ip_key(_request(_bearer(forged), client_ip="198.51.100.6"))

    assert key == "198.51.100.6", "an unverifiable token must not grant a bucket"


def test_a_refresh_token_cannot_be_used_to_claim_a_bucket():
    """Only access tokens identify a caller; a refresh token is not a credential here."""
    refresh = create_refresh_token("42", get_settings())

    key = user_or_ip_key(_request(_bearer(refresh), client_ip="198.51.100.7"))

    assert key == "198.51.100.7"


def test_an_expired_token_falls_back_to_the_address():
    expired = create_access_token(
        "42", Settings(anthropic_api_key="", access_token_expire_minutes=-5,
                       jwt_secret=get_settings().jwt_secret)
    )

    key = user_or_ip_key(_request(_bearer(expired), client_ip="198.51.100.8"))

    assert key == "198.51.100.8"


# ============================== THE CAP ====================================

@pytest.fixture
def limited_client(disabled_ai):
    """The app with the limiter LEFT ON and a tiny cap, so 429 is reachable."""
    from app.api.websockets import ConnectionManager
    from app.core.deps import (
        get_ai_service, get_connection_manager, get_market_service,
        get_news_service, get_portfolio_service, get_token_store,
    )
    from app.core.token_store import InMemoryBlocklist
    from app.db.database import get_session
    from app.main import create_app
    from app.services.market_service import MarketService
    from app.services.news_service import NewsService
    from app.services.portfolio_service import PortfolioService
    from tests.conftest import _FakeMarketProvider, _FakeNewsProvider, _NoopCache

    app = create_app()
    app.state.enable_scheduler = False
    app.state.limiter.enabled = True  # the point of this fixture
    app.state.limiter.reset()

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as session:
            yield session

    market = MarketService(_FakeMarketProvider(), disabled_ai, _NoopCache())
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_market_service] = lambda: market
    app.dependency_overrides[get_news_service] = lambda: NewsService(
        _FakeNewsProvider(), disabled_ai, _NoopCache())
    app.dependency_overrides[get_portfolio_service] = lambda: PortfolioService(
        _FakeMarketProvider(), disabled_ai)
    app.dependency_overrides[get_token_store] = lambda: InMemoryBlocklist()
    app.dependency_overrides[get_ai_service] = lambda: disabled_ai
    app.dependency_overrides[get_connection_manager] = lambda: ConnectionManager()

    with TestClient(app) as c:
        yield c
    app.state.limiter.reset()
    app.state.limiter.enabled = False


def _login(client: TestClient, email: str) -> dict:
    client.post("/api/auth/register", json={"email": email, "password": "password123"})
    tok = client.post(
        "/api/auth/login", json={"email": email, "password": "password123"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _spend(client: TestClient, headers: dict, path: str, n: int) -> list[int]:
    return [client.get(path, headers=headers).status_code for _ in range(n)]


def test_a_capped_endpoint_eventually_answers_429(limited_client):
    headers = _login(limited_client, "capped@example.com")

    codes = _spend(limited_client, headers, "/api/portfolio/optimize", 12)

    assert 429 in codes, f"expected the AI cap to bite, saw {sorted(set(codes))}"


def test_one_user_hitting_the_cap_does_not_lock_out_another(limited_client):
    """The isolation that makes per-user keying worth having."""
    heavy = _login(limited_client, "heavy@example.com")
    light = _login(limited_client, "light@example.com")

    _spend(limited_client, heavy, "/api/portfolio/optimize", 12)
    after = limited_client.get("/api/portfolio/optimize", headers=light).status_code

    assert after != 429, "the quiet user must still be served"


def test_the_limiter_is_off_for_the_ordinary_suite(client):
    """Guards the fixture contract the other 400+ tests depend on."""
    assert client.app.state.limiter.enabled is False
