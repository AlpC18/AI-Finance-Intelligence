"""Order cancellation — the missing half of the kill-switch.

Before this existed the app could open a position but never pull one back:
`BrokerProvider` had no cancel at all, and the kill-switch only gated NEW
submissions. An operator tripping the switch mid-drawdown left every resting
order live at the venue, still filling into the exact loss the switch was
pulled to stop.

Two properties carry the weight here and most of the tests below defend one of
them:

  1. A cancel request is not a cancellation. The venue only ACCEPTS the ask;
     the order can still fill on its way down. So a successful cancel writes
     `pending_cancel` (non-terminal) and lets the existing reconciliation sweep
     settle the truth. Writing `canceled` locally would park a lie in the
     ledger that a later fill contradicts.

  2. Flattening is best-effort but the halt is not. A venue that refuses one
     cancel must never leave the switch un-pulled.
"""
from __future__ import annotations

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
    OrderRequest,
)
from app.models.market import Indicators, MarketData, Quote
from app.models.order import TradeOrder, is_cancelable, is_terminal
from app.providers.alpaca_broker_provider import AlpacaBrokerProvider
from app.providers.broker_base import BrokerError
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
    """A broker that records cancels and can be told to refuse them."""

    def __init__(self, cancel_error: Exception | None = None) -> None:
        self.canceled: list[str] = []
        self.cancel_error = cancel_error
        self.remote_state = BrokerOrder(
            id="venue-1", symbol="AAPL", side="buy", quantity=10,
            order_type="market", status="filled", filled_quantity=10.0,
            filled_avg_price=101.5,
        )

    async def get_account(self) -> BrokerAccount:
        return BrokerAccount(account_number="PA1", status="ACTIVE",
                             cash=1e9, buying_power=1e9)

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        return BrokerOrder(
            id="venue-1", symbol=order.symbol, side=order.side,
            quantity=order.quantity, order_type=order.order_type, status="accepted",
        )

    async def get_order(self, order_id: str) -> BrokerOrder:
        return self.remote_state

    async def get_positions(self) -> list:
        return []

    async def cancel_order(self, order_id: str) -> None:
        if self.cancel_error is not None:
            raise self.cancel_error
        self.canceled.append(order_id)


def _svc(venue: _Venue) -> TradeService:
    return TradeService(
        Settings(anthropic_api_key="", max_order_notional=1e9),
        _Market(),
        broker_factory=lambda **kw: venue,
    )


def _creds(trade: TradeService, s: Session, user_id: int = 1) -> None:
    trade.save_credentials(
        s, user_id, CredentialCreate(api_key="PKID1234", api_secret="SEC5678")
    )


def _order(s: Session, *, user_id: int = 1, status: str = "accepted",
           broker_order_id: str = "venue-1", reconciled: bool = False) -> TradeOrder:
    row = TradeOrder(
        user_id=user_id, broker="alpaca", broker_order_id=broker_order_id,
        symbol="AAPL", side="buy", quantity=10, order_type="market",
        status=status, reconciled=reconciled,
    )
    s.add(row)
    s.commit()
    s.refresh(row)
    return row


# ===================== THE PROVIDER SPEAKS ALPACA'S DIALECT =================

async def test_cancel_issues_a_delete_and_survives_an_empty_204_body():
    """204 No Content is the SUCCESS case, and it has no JSON to parse.

    Calling .json() on it raises, which would report a completed cancellation
    as a failure and invite the caller to retry something already done.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(204)

    provider = AlpacaBrokerProvider(
        "k", "s", transport=httpx.MockTransport(handler)
    )

    assert await provider.cancel_order("abc-123") is None
    assert seen == {"method": "DELETE", "path": "/v2/orders/abc-123"}


@pytest.mark.parametrize("upstream", [404, 422])
async def test_a_refused_cancel_carries_the_venues_own_status(upstream):
    """The distinction has to survive the trip: 422 (too late) and 404 (never
    heard of it) mean different things and are answered differently."""
    provider = AlpacaBrokerProvider(
        "k", "s",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(upstream, json={"message": "nope"})
        ),
    )

    with pytest.raises(BrokerError) as exc:
        await provider.cancel_order("abc-123")

    assert exc.value.upstream_status == upstream


# ============ A CANCEL REQUEST IS NOT A CANCELLATION =======================

async def test_an_accepted_cancel_records_pending_cancel_not_canceled():
    """The headline property. The venue agreed to TRY."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        row = _order(s)

        result = await trade.cancel_order(s, 1, row.id)

        assert result.status == "pending_cancel"
        assert venue.canceled == ["venue-1"]


async def test_pending_cancel_is_not_terminal_so_reconciliation_keeps_polling():
    """If this were terminal the sweep would stop watching, and an order that
    filled anyway would never reach the ledger."""
    assert not is_terminal("pending_cancel")
    assert is_cancelable("pending_cancel"), "a dropped request must be retryable"


async def test_a_cancel_leaves_the_order_unreconciled():
    """`reconciled` means "the ledger is settled with this order". Asking for a
    cancel settles nothing."""
    with _db() as s:
        trade = _svc(_Venue())
        _creds(trade, s)
        row = _order(s)

        result = await trade.cancel_order(s, 1, row.id)

        assert result.reconciled is False


@pytest.mark.parametrize("status", ["filled", "canceled", "expired", "rejected"])
async def test_a_terminal_order_is_refused_without_touching_the_venue(status):
    """Cheap and correct: there is nothing to cancel, so do not spend a call
    finding that out."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        row = _order(s, status=status)

        with pytest.raises(AppError) as exc:
            await trade.cancel_order(s, 1, row.id)

        assert exc.value.status_code == 409
        assert exc.value.reason == "not_cancelable"
        assert venue.canceled == [], "the venue was never asked"


async def test_a_second_cancel_of_a_pending_cancel_is_still_forwarded():
    """A request the venue dropped has to be retryable, or an order can get
    stuck working forever with no way to ask again."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        row = _order(s, status="pending_cancel")

        await trade.cancel_order(s, 1, row.id)

        assert venue.canceled == ["venue-1"]


# ==================== TENANCY: NOT AN ENUMERATION ORACLE ===================

async def test_another_users_order_is_a_404_not_a_403():
    """A 403 would confirm the id exists. Both cases must look identical."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s, user_id=1)
        _creds(trade, s, user_id=2)
        victim = _order(s, user_id=2)

        with pytest.raises(AppError) as exc:
            await trade.cancel_order(s, 1, victim.id)

        assert exc.value.status_code == 404
        assert venue.canceled == [], "the other tenant's order was never touched"
        s.refresh(victim)
        assert victim.status == "accepted", "and its state is unchanged"


async def test_an_unknown_order_id_is_a_404():
    with _db() as s:
        trade = _svc(_Venue())
        _creds(trade, s)

        with pytest.raises(AppError) as exc:
            await trade.cancel_order(s, 1, 99999)

        assert exc.value.status_code == 404


async def test_cancelling_without_broker_credentials_is_refused():
    with _db() as s:
        trade = _svc(_Venue())
        row = _order(s)  # no credentials saved

        with pytest.raises(AppError) as exc:
            await trade.cancel_order(s, 1, row.id)

        assert exc.value.status_code == 400
        assert exc.value.reason == "no_credentials"


# ============ THE RACE: IT FILLED WHILE WE WERE ASKING =====================

async def test_a_too_late_cancel_resyncs_the_order_from_the_venue():
    """The sharp case. The venue says 422 because the order already filled, so
    our row is stale. Answering with a bare error next to a local status that
    still reads "accepted" leaves the caller unable to tell which to believe -
    so pull the truth before replying.
    """
    with _db() as s:
        venue = _Venue(cancel_error=BrokerError("too late", 400, upstream_status=422))
        trade = _svc(venue)
        _creds(trade, s)
        row = _order(s)

        with pytest.raises(AppError) as exc:
            await trade.cancel_order(s, 1, row.id)

        assert exc.value.status_code == 409
        s.refresh(row)
        assert row.status == "filled", "the stale row was corrected"
        assert row.filled_quantity == 10.0
        assert row.filled_avg_price == 101.5


async def test_a_failed_resync_still_answers_409_rather_than_raising():
    """The resync is a courtesy on an error path. Failing it must not replace a
    clear 'too late' with a confusing transport error."""
    class _NoPoll(_Venue):
        async def get_order(self, order_id: str):
            raise RuntimeError("venue unreachable")

    with _db() as s:
        venue = _NoPoll(cancel_error=BrokerError("too late", 400, upstream_status=422))
        trade = _svc(venue)
        _creds(trade, s)
        row = _order(s)

        with pytest.raises(AppError) as exc:
            await trade.cancel_order(s, 1, row.id)

        assert exc.value.status_code == 409


async def test_a_venue_outage_propagates_and_leaves_the_order_working():
    """A 5xx is not 'cancelled'. Silently marking pending_cancel here would tell
    the operator the order is on its way down when nothing was ever sent."""
    with _db() as s:
        venue = _Venue(cancel_error=BrokerError("upstream down", 502, upstream_status=503))
        trade = _svc(venue)
        _creds(trade, s)
        row = _order(s)

        with pytest.raises(BrokerError):
            await trade.cancel_order(s, 1, row.id)

        s.refresh(row)
        assert row.status == "accepted", "still working, and honestly labelled"


# ======================== FLATTEN: THE KILL-SWITCH HALF ====================

async def test_flatten_cancels_every_working_order():
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        for i in range(3):
            _order(s, broker_order_id=f"venue-{i}")

        count = await trade.cancel_open_orders(s, 1)

        assert count == 3
        assert sorted(venue.canceled) == ["venue-0", "venue-1", "venue-2"]


async def test_flatten_skips_orders_that_are_already_finished():
    """Terminal orders are not 'working'; asking about them wastes a call and
    invites a spurious 422."""
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s)
        _order(s, broker_order_id="live", status="accepted")
        _order(s, broker_order_id="done", status="filled", reconciled=True)

        count = await trade.cancel_open_orders(s, 1)

        assert count == 1 and venue.canceled == ["live"]


async def test_one_refused_cancel_does_not_strand_the_rest():
    """The caller is an operator stopping everything at once. A single stubborn
    order must not abort the sweep."""
    class _Flaky(_Venue):
        async def cancel_order(self, order_id: str) -> None:
            if order_id == "bad":
                raise BrokerError("nope", 400, upstream_status=422)
            self.canceled.append(order_id)

    with _db() as s:
        venue = _Flaky()
        trade = _svc(venue)
        _creds(trade, s)
        _order(s, broker_order_id="good-1")
        _order(s, broker_order_id="bad")
        _order(s, broker_order_id="good-2")

        count = await trade.cancel_open_orders(s, 1)

        assert count == 2
        assert sorted(venue.canceled) == ["good-1", "good-2"]


async def test_flatten_without_credentials_cancels_nothing_and_does_not_raise():
    """No credentials means nothing of ours is resting at a venue. That is not
    an error, and it must not be able to block a halt."""
    with _db() as s:
        trade = _svc(_Venue())
        _order(s)

        assert await trade.cancel_open_orders(s, 1) == 0


async def test_flatten_touches_only_the_requesting_user():
    with _db() as s:
        venue = _Venue()
        trade = _svc(venue)
        _creds(trade, s, user_id=1)
        _creds(trade, s, user_id=2)
        _order(s, user_id=1, broker_order_id="mine")
        other = _order(s, user_id=2, broker_order_id="theirs")

        count = await trade.cancel_open_orders(s, 1)

        assert count == 1 and venue.canceled == ["mine"]
        s.refresh(other)
        assert other.status == "accepted"


# ============================ OVER THE WIRE ================================
# The service tests above own the semantics; these pin the HTTP contract and
# the one thing only the route can get wrong: the ORDER of halt vs flatten.

import numpy as np
import pandas as pd
from sqlmodel import Session as _Session

from app.core.deps import get_risk_service, get_trade_service
from app.models.risk import RiskSetting
from app.services.market_service import MarketService
from app.services.portfolio_service import PortfolioService
from app.services.risk_service import RiskService


class _Mkt:
    async def get_history(self, symbol, period="6mo"):
        return pd.DataFrame({"Close": np.linspace(100, 110, 30)})

    async def get_price(self, symbol):
        return 110.0


class _Cache:
    async def get_or_set(self, key, factory, ttl=None):
        return await factory()


def _wire(client, venue: _Venue) -> TradeService:
    portfolio = PortfolioService(_Mkt(), None)
    risk = RiskService(portfolio)
    trade = TradeService(
        Settings(anthropic_api_key="", max_order_notional=1e9),
        MarketService(_Mkt(), None, _Cache()),
        broker_factory=lambda **kw: venue,
        risk=risk,
    )
    client.app.dependency_overrides[get_risk_service] = lambda: risk
    client.app.dependency_overrides[get_trade_service] = lambda: trade
    return trade


def _seed(client, trade: TradeService, *broker_ids: str) -> list[int]:
    """Credentials + working orders for user 1, written through the app's engine."""
    ids = []
    with _Session(client.app.state.test_engine) as s:
        _creds(trade, s)
        for bid in broker_ids:
            ids.append(_order(s, broker_order_id=bid).id)
    return ids


def test_delete_orders_returns_the_order_in_pending_cancel(client, auth_headers):
    venue = _Venue()
    trade = _wire(client, venue)
    (order_id,) = _seed(client, trade, "venue-1")

    res = client.delete(f"/api/trade/orders/{order_id}", headers=auth_headers)

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "pending_cancel"
    assert venue.canceled == ["venue-1"]


def test_deleting_an_order_requires_authentication(client):
    _wire(client, _Venue())

    assert client.delete("/api/trade/orders/1").status_code == 401


def test_deleting_another_users_order_is_404(client, auth_headers, make_user):
    venue = _Venue()
    trade = _wire(client, venue)
    (order_id,) = _seed(client, trade, "venue-1")
    intruder = make_user("thief@example.com")

    res = client.delete(f"/api/trade/orders/{order_id}", headers=intruder)

    assert res.status_code == 404
    assert venue.canceled == []


def test_deleting_a_finished_order_is_409(client, auth_headers):
    trade = _wire(client, _Venue())
    with _Session(client.app.state.test_engine) as s:
        _creds(trade, s)
        order_id = _order(s, status="filled", reconciled=True).id

    res = client.delete(f"/api/trade/orders/{order_id}", headers=auth_headers)

    assert res.status_code == 409


def test_engaging_the_kill_switch_flattens_working_orders(client, auth_headers):
    """The whole point: halting stops the bleeding instead of only refusing
    new bets while the old ones keep filling."""
    venue = _Venue()
    trade = _wire(client, venue)
    _seed(client, trade, "a", "b")

    res = client.post("/api/trade/kill-switch", headers=auth_headers,
                      json={"enabled": True})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["halted"] is True
    assert body["canceled_orders"] == 2
    assert sorted(venue.canceled) == ["a", "b"]


def test_the_halt_is_recorded_before_any_cancel_is_sent(client, auth_headers):
    """Ordering, not decoration. Cancel-then-halt leaves a window in which a
    new order can be accepted behind the cancellations and survive the flatten.
    """
    observed: list[bool] = []

    class _Watching(_Venue):
        async def cancel_order(self, order_id: str) -> None:
            with _Session(client.app.state.test_engine) as s:
                row = s.exec(
                    select(RiskSetting).where(RiskSetting.user_id == 1)
                ).first()
                observed.append(bool(row and row.manual_halt))
            self.canceled.append(order_id)

    venue = _Watching()
    trade = _wire(client, venue)
    _seed(client, trade, "a", "b")

    client.post("/api/trade/kill-switch", headers=auth_headers, json={"enabled": True})

    assert observed == [True, True], "trading was already halted when we cancelled"


def test_cancel_open_false_halts_without_touching_resting_orders(client, auth_headers):
    """The opt-out: freeze submissions but leave working orders alone."""
    venue = _Venue()
    trade = _wire(client, venue)
    _seed(client, trade, "a")

    res = client.post("/api/trade/kill-switch", headers=auth_headers,
                      json={"enabled": True, "cancel_open": False})

    body = res.json()
    assert body["halted"] is True
    assert body["canceled_orders"] is None, "nothing was attempted"
    assert venue.canceled == []


def test_resuming_trading_never_cancels_anything(client, auth_headers):
    """cancel_open defaults to true; it must not fire on the way back up."""
    venue = _Venue()
    trade = _wire(client, venue)
    _seed(client, trade, "a")

    res = client.post("/api/trade/kill-switch", headers=auth_headers,
                      json={"enabled": False})

    assert res.json()["canceled_orders"] is None
    assert venue.canceled == []


def test_a_plain_status_read_reports_no_cancellations(client, auth_headers):
    """None, not 0: a GET asked the venue for nothing, which is different from
    trying and finding nothing to cancel."""
    _wire(client, _Venue())

    body = client.get("/api/trade/kill-switch", headers=auth_headers).json()

    assert body["canceled_orders"] is None


def test_the_switch_still_engages_when_every_cancel_fails(client, auth_headers):
    """A broken venue must not be able to hold the handle up."""
    class _Broken(_Venue):
        async def cancel_order(self, order_id: str) -> None:
            raise BrokerError("venue down", 502, upstream_status=503)

    trade = _wire(client, _Broken())
    _seed(client, trade, "a", "b")

    res = client.post("/api/trade/kill-switch", headers=auth_headers,
                      json={"enabled": True})

    body = res.json()
    assert body["halted"] is True, "the halt landed regardless"
    assert body["canceled_orders"] == 0
