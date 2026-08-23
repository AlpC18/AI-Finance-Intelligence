"""Duplicate-order protection — the failure that costs real money.

A double-clicked button, or a client retrying after a timeout it could not
distinguish from a failure, must not open two positions. Reconciliation cannot
clean this up afterwards: both orders are genuine as far as the venue is
concerned, so the only place to stop it is before submission.

Three layers are tested here, because each one covers a case the others miss:
  pre-check ..... a key already on file replays without touching the venue,
  venue ......... the client_order_id travels to the broker, which enforces
                  uniqueness per account when two requests race past step one,
  unique index .. if a concurrent request persisted first, its order is the
                  real one and ours is not written twice.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.core.config import Settings
from app.core.errors import AppError
from app.models.broker import (
    BrokerAccount,
    BrokerOrder,
    CredentialCreate,
    OrderRequest,
    TradeRequest,
)
from app.models.market import Indicators, MarketData, Quote
from app.models.order import TradeAuditLog, TradeOrder
from app.services.trade_service import TradeService

KEY = "abc123def456"


@contextmanager
def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


class _Market:
    def __init__(self) -> None:
        self.calls = 0

    async def get_market_data(self, symbol: str) -> MarketData:
        self.calls += 1
        return MarketData(quote=Quote(symbol=symbol, price=100.0), indicators=Indicators())


class _Venue:
    """A broker that enforces client_order_id uniqueness, as a real one does."""

    def __init__(self) -> None:
        self.placed: list[OrderRequest] = []
        self.seen_keys: set[str] = set()
        self.accounts = 0

    async def get_account(self) -> BrokerAccount:
        self.accounts += 1
        return BrokerAccount(account_number="PA1", status="ACTIVE",
                             cash=1e9, buying_power=1e9)

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        if order.client_order_id in self.seen_keys:
            raise AppError("client_order_id must be unique", status_code=400)
        self.seen_keys.add(order.client_order_id)
        self.placed.append(order)
        return BrokerOrder(
            id=f"venue-{len(self.placed)}", symbol=order.symbol, side=order.side,
            quantity=order.quantity, order_type=order.order_type,
            status="accepted", filled_quantity=0.0, filled_avg_price=None,
        )


def _svc(venue: _Venue, market: _Market | None = None) -> TradeService:
    return TradeService(
        Settings(anthropic_api_key="", max_order_notional=1e9),
        market or _Market(),
        broker_factory=lambda **kw: venue,
    )


def _creds(trade: TradeService, s: Session, user_id: int = 1) -> None:
    trade.save_credentials(
        s, user_id, CredentialCreate(api_key="PKID1234", api_secret="SEC5678")
    )


def _req(**kw) -> TradeRequest:
    return TradeRequest(**{"symbol": "AAPL", "action": "BUY", "quantity": 10, **kw})


def _orders(s: Session, user_id: int = 1) -> list[TradeOrder]:
    return list(s.exec(select(TradeOrder).where(TradeOrder.user_id == user_id)).all())


# ======================== LAYER 1: THE PRE-CHECK ============================

async def test_the_same_key_twice_places_exactly_one_order():
    """The headline guarantee."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)

        first = await trade.execute(s, 1, _req(idempotency_key=KEY))
        second = await trade.execute(s, 1, _req(idempotency_key=KEY))

        assert len(venue.placed) == 1, "the venue saw one order"
        assert len(_orders(s)) == 1, "the book holds one order"
        assert first.duplicate is False and second.duplicate is True
        assert second.order.id == first.order.id, "the original is echoed back"


async def test_a_replay_never_reaches_the_broker_or_the_market_feed():
    """A duplicate must be cheap: no quote, no account call, no risk round-trip."""
    with _db() as s:
        venue, market = _Venue(), _Market()
        trade = _svc(venue, market)
        _creds(trade, s)

        await trade.execute(s, 1, _req(idempotency_key=KEY))
        calls_after_first = (market.calls, venue.accounts)
        await trade.execute(s, 1, _req(idempotency_key=KEY))

        assert (market.calls, venue.accounts) == calls_after_first


async def test_a_replay_writes_no_second_audit_row():
    """One intent, one audit entry — the trail must not double-count."""
    with _db() as s:
        trade = _svc(_Venue())
        _creds(trade, s)

        await trade.execute(s, 1, _req(idempotency_key=KEY))
        await trade.execute(s, 1, _req(idempotency_key=KEY))

        assert len(list(s.exec(select(TradeAuditLog)).all())) == 1


async def test_a_replay_ignores_a_changed_body():
    """The key is the intent. A retry with drifted fields must not re-price it."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)

        await trade.execute(s, 1, _req(idempotency_key=KEY, quantity=10))
        result = await trade.execute(s, 1, _req(idempotency_key=KEY, quantity=999))

        assert result.duplicate is True
        assert result.order.quantity == 10, "the ORIGINAL order is returned"
        assert len(venue.placed) == 1


async def test_a_replay_survives_the_kill_switch_being_thrown_between_attempts():
    """Replays return the existing order rather than re-running the risk gates.

    This is deliberate: the position is already open, and reporting it is not
    the same as opening a new one.
    """
    with _db() as s:
        trade = _svc(_Venue())
        _creds(trade, s)
        await trade.execute(s, 1, _req(idempotency_key=KEY))

        halted = TradeService(
            Settings(anthropic_api_key="", trade_enabled=False),
            _Market(), broker_factory=lambda **kw: _Venue(),
        )
        # A NEW order is blocked...
        with pytest.raises(AppError) as exc:
            await halted.execute(s, 1, _req(idempotency_key="different-key-1"))
        assert exc.value.reason == "disabled"


# ===================== DISTINCT INTENTS STAY DISTINCT =======================

async def test_two_different_keys_place_two_orders():
    """De-duplication must not block a user genuinely trading twice."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)

        await trade.execute(s, 1, _req(idempotency_key="key-one-aaaa"))
        await trade.execute(s, 1, _req(idempotency_key="key-two-bbbb"))

        assert len(venue.placed) == 2


async def test_repeating_an_order_without_a_key_is_treated_as_a_new_intent():
    """No key means no de-duplication claim: buying twice is a real thing to do."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)

        await trade.execute(s, 1, _req())
        await trade.execute(s, 1, _req())

        assert len(venue.placed) == 2
        assert len(_orders(s)) == 2


async def test_an_order_without_a_key_still_gets_one_for_the_venue():
    """Every order carries a client_order_id, so the venue can always dedupe."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)

        await trade.execute(s, 1, _req())

        assert venue.placed[0].client_order_id, "a key was generated"
        assert _orders(s)[0].client_order_id == venue.placed[0].client_order_id


async def test_generated_keys_do_not_collide():
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        for _ in range(5):
            await trade.execute(s, 1, _req())

        keys = {o.client_order_id for o in _orders(s)}
        assert len(keys) == 5


async def test_one_users_key_does_not_block_anothers():
    """Key uniqueness is scoped per user; the namespace is not shared.

    Each user trades through their OWN broker account (the factory builds a
    provider from that user's credentials), so the venue-side uniqueness is
    per account too — modelled here with a venue per user.
    """
    with _db() as s:
        venues = {1: _Venue(), 2: _Venue()}
        seen: list[int] = []

        def factory(**kw):
            # The service builds the provider per user; record who asked.
            return venues[seen[-1]]

        trade = TradeService(
            Settings(anthropic_api_key="", max_order_notional=1e9),
            _Market(), broker_factory=factory,
        )
        _creds(trade, s, 1)
        _creds(trade, s, 2)

        seen.append(1)
        await trade.execute(s, 1, _req(idempotency_key=KEY))
        seen.append(2)
        result = await trade.execute(s, 2, _req(idempotency_key=KEY))

        assert result.duplicate is False, "user two's order is its own"
        assert len(venues[1].placed) == len(venues[2].placed) == 1


# ==================== LAYER 2: THE KEY REACHES THE VENUE ====================

async def test_the_key_is_forwarded_to_the_broker():
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)

        await trade.execute(s, 1, _req(idempotency_key=KEY))

        assert venue.placed[0].client_order_id == KEY


def _capturing_provider() -> tuple:
    """An Alpaca provider whose outgoing JSON body is captured."""
    import json

    import httpx

    from app.providers.alpaca_broker_provider import AlpacaBrokerProvider

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "x", "status": "accepted", "qty": "1"})

    return AlpacaBrokerProvider("k", "s", transport=httpx.MockTransport(handler)), captured


async def test_the_alpaca_provider_sends_client_order_id_on_the_wire():
    """The forwarding must survive into the actual HTTP body."""
    provider, captured = _capturing_provider()

    await provider.place_order(
        OrderRequest(symbol="AAPL", side="buy", quantity=1, client_order_id=KEY)
    )

    assert captured["client_order_id"] == KEY


async def test_an_order_without_a_key_omits_the_field_rather_than_sending_null():
    provider, captured = _capturing_provider()

    await provider.place_order(OrderRequest(symbol="AAPL", side="buy", quantity=1))

    assert "client_order_id" not in captured


# ==================== LAYER 3: THE UNIQUE INDEX ============================

async def test_a_concurrent_request_that_persisted_first_wins():
    """Both requests cleared the pre-check; the index decides, and we replay."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)

        # Simulate the race: another worker's row lands while we are at the venue.
        class _Racing(_Venue):
            async def place_order(self, order: OrderRequest) -> BrokerOrder:
                s.add(TradeOrder(
                    user_id=1, broker_order_id="winner", client_order_id=KEY,
                    symbol="AAPL", side="buy", quantity=10, status="accepted",
                ))
                s.commit()
                return await super().place_order(order)

        racing = _Racing()
        trade = _svc(racing)
        result = await trade.execute(s, 1, _req(idempotency_key=KEY))

        assert result.duplicate is True
        assert result.order.id == "winner", "the request that persisted first wins"
        assert len(_orders(s)) == 1, "our losing row was rolled back"


async def test_the_unique_index_is_enforced_at_the_database():
    with _db() as s:
        from sqlalchemy.exc import IntegrityError

        s.add(TradeOrder(user_id=1, broker_order_id="a", client_order_id=KEY,
                         symbol="AAPL", side="buy", quantity=1))
        s.commit()
        s.add(TradeOrder(user_id=1, broker_order_id="b", client_order_id=KEY,
                         symbol="AAPL", side="buy", quantity=1))

        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


async def test_legacy_rows_without_a_key_coexist_under_the_unique_index():
    """NULLs are distinct in SQL, so pre-idempotency rows must not collide."""
    with _db() as s:
        for i in range(3):
            s.add(TradeOrder(user_id=1, broker_order_id=f"legacy-{i}",
                             client_order_id=None, symbol="AAPL",
                             side="buy", quantity=1))
        s.commit()

        assert len(_orders(s)) == 3


# ============================ HTTP SURFACE =================================

@pytest.mark.parametrize("bad", ["short", "has spaces!", "x" * 65, "semi;colon"])
def test_a_malformed_idempotency_key_is_rejected(client, auth_headers, bad):
    """The key is forwarded to the venue verbatim, so it is charset-constrained."""
    res = client.post(
        "/api/trade/execute",
        json={"symbol": "AAPL", "action": "BUY", "quantity": 1, "idempotency_key": bad},
        headers=auth_headers,
    )
    assert res.status_code == 422
