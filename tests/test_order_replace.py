"""Order amendment — repricing without losing your place in the book.

Cancel-and-resubmit was the only way to reprice a resting limit order. It costs
queue position, and between the two calls the intent is not in the market at
all: a move in that window is simply missed.

The two properties that carry risk here:

  1. **A replace cannot be a way around the kill-switch.** An amendment can
     RAISE exposure, so a halt that refused new orders while letting existing
     ones be scaled up would be a hole straight through it.
  2. **The venue issues a NEW order id and retires the original.** Modelling
     that as an in-place edit would leave the local row pointing at an id the
     venue has closed, and the audit trail claiming one order where there were
     two.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.core.config import Settings
from app.core.errors import AppError
from app.models.broker import (
    BrokerAccount,
    BrokerOrder,
    CredentialCreate,
    OrderAmendment,
    OrderRequest,
)
from app.models.market import Indicators, MarketData, Quote
from app.models.order import TradeOrder, is_cancelable, is_terminal
from app.providers.alpaca_broker_provider import AlpacaBrokerProvider
from app.providers.broker_base import BrokerError
from app.services.risk_service import RiskService
from app.services.trade_service import TradeService


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
    async def get_market_data(self, symbol: str) -> MarketData:
        return MarketData(quote=Quote(symbol=symbol, price=100.0), indicators=Indicators())


class _Venue:
    """Replaces the way Alpaca does: a new id, the original retired."""

    def __init__(self) -> None:
        self.replaced: list[dict] = []
        self.next_id = "venue-2"
        self.error: Exception | None = None

    async def get_account(self) -> BrokerAccount:
        return BrokerAccount(account_number="PA1", status="ACTIVE",
                             cash=1e9, buying_power=1e9)

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        return BrokerOrder(id="venue-1", symbol=order.symbol, side=order.side,
                           quantity=order.quantity, order_type=order.order_type,
                           status="accepted")

    async def get_order(self, order_id: str) -> BrokerOrder:
        return BrokerOrder(id=order_id, symbol="AAPL", side="buy", quantity=10,
                           order_type="limit", status="replaced")

    async def get_positions(self) -> list:
        return []

    async def cancel_order(self, order_id: str) -> None:
        return None

    async def replace_order(self, order_id, quantity=None, limit_price=None,
                            client_order_id=None) -> BrokerOrder:
        if self.error is not None:
            raise self.error
        self.replaced.append({
            "order_id": order_id, "quantity": quantity,
            "limit_price": limit_price, "client_order_id": client_order_id,
        })
        return BrokerOrder(
            id=self.next_id, symbol="AAPL", side="buy",
            quantity=quantity if quantity is not None else 10,
            order_type="limit", status="accepted",
        )


class _HaltedRisk:
    async def assert_not_halted(self, session, user_id):
        raise AppError("Kill-switch aktif.", status_code=423, reason="risk_halt")


def _svc(venue: _Venue, risk=None) -> TradeService:
    return TradeService(
        Settings(anthropic_api_key="", max_order_notional=1e9),
        _Market(),
        broker_factory=lambda **kw: venue,
        risk=risk,
    )


def _creds(trade: TradeService, s: Session, user_id: int = 1) -> None:
    trade.save_credentials(
        s, user_id, CredentialCreate(api_key="PKID1234", api_secret="SEC5678")
    )


def _order(s: Session, *, user_id: int = 1, status: str = "accepted",
           broker_order_id: str = "venue-1") -> TradeOrder:
    row = TradeOrder(
        user_id=user_id, broker="alpaca", broker_order_id=broker_order_id,
        symbol="AAPL", side="buy", quantity=10, order_type="limit",
        status=status, client_order_id=f"key-{broker_order_id}",
    )
    s.add(row)
    s.commit()
    s.refresh(row)
    return row


def _rows(s: Session, user_id: int = 1) -> list[TradeOrder]:
    return list(s.exec(select(TradeOrder).where(TradeOrder.user_id == user_id)).all())


# ========================== PROVIDER: ALPACA'S PATCH ========================

async def test_replace_patches_the_order_and_returns_the_new_one():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={
            "id": "new-id", "symbol": "AAPL", "side": "buy", "qty": "7",
            "type": "limit", "status": "accepted", "filled_qty": "0",
        })

    provider = AlpacaBrokerProvider("k", "s", transport=httpx.MockTransport(handler))

    result = await provider.replace_order("old-id", quantity=7, limit_price=99.5)

    assert seen["method"] == "PATCH" and seen["path"] == "/v2/orders/old-id"
    body = json.loads(seen["body"])
    assert body["qty"] == "7" and body["limit_price"] == "99.5"
    assert result.id == "new-id", "the replacement, not the original"


async def test_only_the_named_fields_are_sent():
    """Sending an unspecified field would overwrite it with a stale value."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "n", "symbol": "AAPL", "side": "buy",
                                         "qty": "1", "status": "accepted"})

    provider = AlpacaBrokerProvider("k", "s", transport=httpx.MockTransport(handler))

    await provider.replace_order("old", limit_price=101.0)

    assert json.loads(seen["body"]) == {"limit_price": "101.0"}, "no stale qty sent"


# ====================== THE AMENDMENT MUST BE A CHANGE ======================

def test_an_amendment_that_changes_nothing_is_rejected():
    """A no-op replace is a round-trip that can only lose queue position."""
    with pytest.raises(ValueError):
        OrderAmendment()


@pytest.mark.parametrize("field", ["quantity", "limit_price"])
def test_a_non_positive_amendment_is_rejected(field):
    with pytest.raises(ValueError):
        OrderAmendment(**{field: 0})


# ==================== THE KILL-SWITCH IS NOT BYPASSABLE =====================

async def test_a_halted_account_cannot_amend_an_order_upward():
    """The sharp one. Without this check, 'halt' means 'no NEW orders' while an
    existing order can still be scaled to any size the venue allows."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue, risk=_HaltedRisk())
        _creds(trade, s)
        row = _order(s)

        with pytest.raises(AppError) as exc:
            await trade.replace_order(s, 1, row.id, OrderAmendment(quantity=1000))

        assert exc.value.status_code == 423
        assert venue.replaced == [], "the venue was never asked"


async def test_the_halt_is_checked_before_the_venue_is_touched():
    """Order matters: a replace sent and then refused locally would leave the
    venue working an amendment the app believes it blocked."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue, risk=_HaltedRisk())
        _creds(trade, s)
        row = _order(s)

        with pytest.raises(AppError):
            await trade.replace_order(s, 1, row.id, OrderAmendment(limit_price=99))

        s.refresh(row)
        assert row.status == "accepted", "untouched"


async def test_an_amendment_over_the_size_limit_is_refused():
    """Our own notional policy still applies to the amended order."""
    with _db() as s:
        venue = _Venue()
        trade = TradeService(
            Settings(anthropic_api_key="", max_order_notional=500.0),
            _Market(), broker_factory=lambda **kw: venue,
        )
        _creds(trade, s)
        row = _order(s)

        with pytest.raises(AppError) as exc:
            await trade.replace_order(s, 1, row.id, OrderAmendment(quantity=100))

        assert exc.value.status_code == 422 and exc.value.reason == "notional"
        assert venue.replaced == []


# ================== THE NEW ID IS MODELLED HONESTLY =========================

async def test_a_replace_retires_the_original_and_opens_a_new_row():
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        old = _order(s)

        new = await trade.replace_order(s, 1, old.id, OrderAmendment(limit_price=99.0))

        s.refresh(old)
        assert old.status == "replaced", "the venue closed this id"
        assert new.id != old.id and new.broker_order_id == "venue-2"
        assert len(_rows(s)) == 2, "the amendment history is visible, not overwritten"


async def test_replaced_is_terminal_and_no_longer_amendable_or_cancelable():
    assert is_terminal("replaced")
    assert not is_cancelable("replaced")


async def test_the_retired_order_is_left_for_reconciliation_to_settle():
    """Not marked reconciled here: the sweep is the authority on how an order
    ended, and the original may still carry a fill."""
    with _db() as s:
        trade = _svc(_Venue())
        _creds(trade, s)
        old = _order(s)

        await trade.replace_order(s, 1, old.id, OrderAmendment(limit_price=99.0))

        s.refresh(old)
        assert old.reconciled is False


async def test_the_replacement_carries_a_fresh_idempotency_key():
    """Reusing the original's key would collide with the unique index and, at
    the venue, be refused as a duplicate."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        old = _order(s)

        new = await trade.replace_order(s, 1, old.id, OrderAmendment(quantity=5))

        assert new.client_order_id not in (None, old.client_order_id)
        assert venue.replaced[0]["client_order_id"] == new.client_order_id


async def test_amending_only_the_price_keeps_the_original_quantity():
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        old = _order(s)

        await trade.replace_order(s, 1, old.id, OrderAmendment(limit_price=95.0))

        assert venue.replaced[0]["quantity"] is None, "quantity was not overwritten"


# ============================ REFUSALS AND TENANCY ==========================

@pytest.mark.parametrize("status", ["filled", "canceled", "replaced", "rejected"])
async def test_a_finished_order_cannot_be_amended(status):
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        row = _order(s, status=status)

        with pytest.raises(AppError) as exc:
            await trade.replace_order(s, 1, row.id, OrderAmendment(quantity=5))

        assert exc.value.status_code == 409 and exc.value.reason == "not_amendable"
        assert venue.replaced == []


async def test_another_users_order_cannot_be_amended():
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s, user_id=1)
        _creds(trade, s, user_id=2)
        victim = _order(s, user_id=2, broker_order_id="theirs")

        with pytest.raises(AppError) as exc:
            await trade.replace_order(s, 1, victim.id, OrderAmendment(quantity=1))

        assert exc.value.status_code == 404
        assert venue.replaced == []
        s.refresh(victim)
        assert victim.status == "accepted"


async def test_a_venue_refusal_leaves_the_original_working():
    """No new row, no retirement: nothing happened, and the book must say so."""
    with _db() as s:
        venue = _Venue()
        venue.error = BrokerError("too late", 400, upstream_status=422)
        trade = _svc(venue)
        _creds(trade, s)
        old = _order(s)

        with pytest.raises(BrokerError):
            await trade.replace_order(s, 1, old.id, OrderAmendment(quantity=5))

        s.refresh(old)
        assert old.status == "accepted"
        assert len(_rows(s)) == 1


# ============================== SINGLE FETCH ================================

async def test_one_order_can_be_fetched_by_id():
    with _db() as s:
        trade = _svc(_Venue())
        row = _order(s)

        assert trade.get_order(s, 1, row.id).broker_order_id == "venue-1"


async def test_fetching_another_users_order_is_a_404():
    with _db() as s:
        trade = _svc(_Venue())
        theirs = _order(s, user_id=2, broker_order_id="theirs")

        with pytest.raises(AppError) as exc:
            trade.get_order(s, 1, theirs.id)

        assert exc.value.status_code == 404
