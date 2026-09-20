"""Trade execution + encryption-at-rest: crypto, risk gates, paper orders, CI seam."""
import json

import httpx
import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, select

from app.core.config import Settings
from app.core.crypto import EncryptionError, decrypt, encrypt
from app.core.deps import get_trade_service
from app.models.broker import (
    BrokerAccount,
    BrokerCredential,
    BrokerOrder,
    CredentialCreate,
    OrderRequest,
    TradeRequest,
)
from app.core.errors import AppError
from app.providers.alpaca_broker_provider import AlpacaBrokerProvider
from app.services.market_service import MarketService
from app.services.trade_service import TradeService


# --- Encryption at rest --------------------------------------------------------
def test_fernet_roundtrip_and_ciphertext_is_not_plaintext():
    secret = "PKID-super-secret-key"
    token = encrypt(secret)
    assert token != secret and secret not in token
    assert decrypt(token) == secret


def test_decrypt_rejects_tampered_ciphertext():
    token = encrypt("abc")
    with pytest.raises(EncryptionError):
        decrypt(token[:-4] + "AAAA")


def test_production_requires_valid_encryption_key():
    errs = Settings(
        environment="production",
        jwt_secret="x" * 40,
        anthropic_api_key="sk-real",
        cache_backend="redis",
        encryption_key="",
    ).production_config_errors()
    assert any("ENCRYPTION_KEY is required" in e for e in errs)

    errs = Settings(
        environment="production",
        jwt_secret="x" * 40,
        anthropic_api_key="sk-real",
        cache_backend="redis",
        encryption_key="not-a-valid-fernet-key",
    ).production_config_errors()
    assert any("valid urlsafe-base64 Fernet key" in e for e in errs)


# --- Broker provider (hermetic via httpx.MockTransport) ------------------------
def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_alpaca_provider_places_order_over_mock_transport():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        if request.url.path.endswith("/v2/account"):
            return httpx.Response(200, json={"account_number": "PA1", "status": "ACTIVE",
                                              "cash": "100000", "buying_power": "100000"})
        return httpx.Response(200, json={"id": "ord_1", "symbol": "AAPL", "side": "buy",
                                         "qty": "3", "type": "market", "status": "accepted",
                                         "filled_qty": "0"})

    broker = AlpacaBrokerProvider("k", "s", transport=_mock_transport(handler))
    acct = await broker.get_account()
    assert acct.buying_power == 100000
    order = await broker.place_order(OrderRequest(symbol="AAPL", side="buy", quantity=3))
    assert order.id == "ord_1" and order.side == "buy" and seen["path"].endswith("/v2/orders")


@pytest.mark.asyncio
async def test_alpaca_provider_sends_a_complete_protective_bracket():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "ord_2", "symbol": "AAPL", "side": "buy",
                                         "qty": "1", "type": "market", "status": "accepted"})

    broker = AlpacaBrokerProvider("k", "s", transport=_mock_transport(handler))
    await broker.place_order(OrderRequest(
        symbol="AAPL", side="buy", quantity=1, stop_loss=95, take_profit=120,
    ))
    assert seen["order_class"] == "bracket"
    assert seen["stop_loss"] == {"stop_price": "95"}
    assert seen["take_profit"] == {"limit_price": "120"}


@pytest.mark.asyncio
async def test_alpaca_provider_maps_http_error_to_broker_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "insufficient buying power"})

    broker = AlpacaBrokerProvider("k", "s", transport=_mock_transport(handler))
    with pytest.raises(AppError) as exc:
        await broker.place_order(OrderRequest(symbol="AAPL", side="buy", quantity=1))
    assert exc.value.status_code == 400
    assert "insufficient buying power" in exc.value.message


# --- Trade service (fake broker + fake market) ---------------------------------
class _FakeMarketProvider:
    async def get_history(self, symbol, period="6mo"):
        return pd.DataFrame({"Close": np.linspace(100, 110, 30)})

    async def get_price(self, symbol):
        return 110.0


class _FakeBroker:
    def __init__(self, buying_power=100000.0, **_):
        self._bp = buying_power
        self.placed = []

    async def get_account(self):
        return BrokerAccount(account_number="PA1", status="ACTIVE",
                             cash=self._bp, buying_power=self._bp)

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        self.placed.append(order)
        return BrokerOrder(id="ord_1", symbol=order.symbol, side=order.side,
                           quantity=order.quantity, order_type=order.order_type,
                           status="accepted", filled_quantity=0.0)


def _service(settings: Settings, broker: _FakeBroker) -> TradeService:
    market = MarketService(_FakeMarketProvider(), ai=None, cache=_NoopCache())
    return TradeService(settings, market, broker_factory=lambda **kw: broker)


class _NoopCache:
    async def get_or_set(self, key, factory, ttl=None):
        return await factory()


def _seed_creds(client, session_factory, headers) -> None:
    client.app.dependency_overrides  # ensure app wired
    client.post("/api/trade/credentials", headers=headers,
                json={"broker": "alpaca", "api_key": "PKID1234", "api_secret": "SEC5678"})


def _wire(client, service: TradeService):
    client.app.dependency_overrides[get_trade_service] = lambda: service


def test_credentials_saved_encrypted_and_secrets_never_returned(client, auth_headers):
    broker = _FakeBroker()
    _wire(client, _service(Settings(anthropic_api_key=""), broker))
    r = client.post("/api/trade/credentials", headers=auth_headers,
                    json={"broker": "alpaca", "api_key": "PKID1234", "api_secret": "SEC5678"})
    assert r.status_code == 201
    body = r.json()
    assert body["api_key_last4"] == "1234"
    assert "SEC5678" not in r.text and "PKID1234" not in r.text


def test_execute_buy_places_paper_order(client, auth_headers):
    broker = _FakeBroker()
    _wire(client, _service(Settings(anthropic_api_key=""), broker))
    client.post("/api/trade/credentials", headers=auth_headers,
                json={"api_key": "PKID1234", "api_secret": "SEC5678"})
    r = client.post("/api/trade/execute", headers=auth_headers,
                    json={"symbol": "AAPL", "action": "BUY", "quantity": 2, "confidence": 0.8})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True and body["order"]["side"] == "buy"
    assert broker.placed and broker.placed[0].side == "buy"


def test_execute_persists_and_forwards_a_valid_protective_bracket(client, auth_headers):
    broker = _FakeBroker()
    _wire(client, _service(Settings(anthropic_api_key=""), broker))
    client.post("/api/trade/credentials", headers=auth_headers,
                json={"api_key": "PKID1234", "api_secret": "SEC5678"})
    r = client.post("/api/trade/execute", headers=auth_headers, json={
        "symbol": "AAPL", "action": "BUY", "quantity": 1,
        "stop_loss": 100, "take_profit": 120,
    })
    assert r.status_code == 200, r.text
    assert broker.placed[0].stop_loss == 100
    assert broker.placed[0].take_profit == 120


def test_execute_rejects_inverted_protective_bracket(client, auth_headers):
    _wire(client, _service(Settings(anthropic_api_key=""), _FakeBroker()))
    client.post("/api/trade/credentials", headers=auth_headers,
                json={"api_key": "PKID1234", "api_secret": "SEC5678"})
    r = client.post("/api/trade/execute", headers=auth_headers, json={
        "symbol": "AAPL", "action": "BUY", "quantity": 1,
        "stop_loss": 115, "take_profit": 120,
    })
    assert r.status_code == 422


def test_execute_rejects_hold_signal(client, auth_headers):
    _wire(client, _service(Settings(anthropic_api_key=""), _FakeBroker()))
    client.post("/api/trade/credentials", headers=auth_headers,
                json={"api_key": "PKID1234", "api_secret": "SEC5678"})
    r = client.post("/api/trade/execute", headers=auth_headers,
                    json={"symbol": "AAPL", "action": "HOLD", "quantity": 1})
    assert r.status_code == 422


def test_execute_enforces_confidence_gate(client, auth_headers):
    settings = Settings(anthropic_api_key="", min_trade_confidence=0.7)
    _wire(client, _service(settings, _FakeBroker()))
    client.post("/api/trade/credentials", headers=auth_headers,
                json={"api_key": "PKID1234", "api_secret": "SEC5678"})
    r = client.post("/api/trade/execute", headers=auth_headers,
                    json={"symbol": "AAPL", "action": "BUY", "quantity": 1, "confidence": 0.5})
    assert r.status_code == 422 and "guveni" in r.json()["error"]


def test_execute_enforces_max_notional(client, auth_headers):
    settings = Settings(anthropic_api_key="", max_order_notional=100.0)  # price ~110 * 2 > 100
    _wire(client, _service(settings, _FakeBroker()))
    client.post("/api/trade/credentials", headers=auth_headers,
                json={"api_key": "PKID1234", "api_secret": "SEC5678"})
    r = client.post("/api/trade/execute", headers=auth_headers,
                    json={"symbol": "AAPL", "action": "BUY", "quantity": 2})
    assert r.status_code == 422 and "azami" in r.json()["error"]


def test_execute_enforces_buying_power(client, auth_headers):
    broker = _FakeBroker(buying_power=50.0)  # can't afford ~110 * 1
    _wire(client, _service(Settings(anthropic_api_key=""), broker))
    client.post("/api/trade/credentials", headers=auth_headers,
                json={"api_key": "PKID1234", "api_secret": "SEC5678"})
    r = client.post("/api/trade/execute", headers=auth_headers,
                    json={"symbol": "AAPL", "action": "BUY", "quantity": 1})
    assert r.status_code == 422 and "alim gucu" in r.json()["error"]


def test_execute_without_credentials_is_rejected(client, auth_headers):
    _wire(client, _service(Settings(anthropic_api_key=""), _FakeBroker()))
    r = client.post("/api/trade/execute", headers=auth_headers,
                    json={"symbol": "AAPL", "action": "BUY", "quantity": 1})
    assert r.status_code == 400 and "kimlik" in r.json()["error"]


def test_trade_endpoints_require_auth(client):
    assert client.post("/api/trade/execute",
                       json={"symbol": "AAPL", "action": "BUY", "quantity": 1}).status_code == 401
    assert client.post("/api/trade/credentials",
                       json={"api_key": "x", "api_secret": "y"}).status_code == 401
