"""Adversarial execution stress — how the desk behaves when the market misbehaves.

Unit tests elsewhere prove the happy path. This suite is the opposite: it feeds
the execution and reconciliation layers the things that actually go wrong on a
live desk, and pins the invariant that must survive each one.

  partial fills .... the ledger books the *cumulative* fill exactly once, and
                     never books a partial as if it were complete,
  broker outage .... a failure after submission leaves the order OPEN for the
                     next sweep - we never guess, and never double-book,
  slippage ......... the ledger records the price the market gave us, not the
                     price we asked for,
  halts/rejects .... a terminal non-fill closes the order with no ledger write,
                     and a pre-trade halt never reaches the broker at all.

The through-line: when reality is ambiguous, stay closed and stay silent rather
than writing something confident and wrong into the ledger.
"""
from __future__ import annotations

from contextlib import contextmanager

import httpx
import numpy as np
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.core.config import Settings
from app.core.errors import AppError
from app.models.broker import (
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    CredentialCreate,
    OrderRequest,
    TradeRequest,
)
from app.models.market import Indicators, MarketData, Quote
from app.models.order import TradeOrder
from app.models.transaction import Transaction
from app.models.webhook import AlpacaTradeUpdate
from app.providers.broker_base import BrokerError
from app.services.market_service import MarketService
from app.services.portfolio_service import PortfolioService
from app.services.reconciliation_service import TradeReconciliationService
from app.services.trade_service import TradeService

REFERENCE_PRICE = 110.0


@contextmanager
def _db():
    """A disposable in-memory ledger. Disposed so tests leak no connections."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


class _FakeMarket:
    async def get_history(self, symbol, period="6mo"):
        return pd.DataFrame({"Close": np.linspace(100, REFERENCE_PRICE, 30)})

    async def get_price(self, symbol):
        return REFERENCE_PRICE


class _NoopCache:
    async def get_or_set(self, key, factory, ttl=None):
        return await factory()


def _recon() -> TradeReconciliationService:
    return TradeReconciliationService(PortfolioService(None, None))


def _open_order(session: Session, **kw) -> TradeOrder:
    defaults = dict(
        user_id=1, broker_order_id="o1", symbol="AAPL", side="buy",
        quantity=10, status="accepted",
    )
    order = TradeOrder(**{**defaults, **kw})
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def _remote(**kw) -> BrokerOrder:
    defaults = dict(
        id="o1", symbol="AAPL", side="buy", quantity=10,
        order_type="market", status="filled", filled_quantity=10.0,
        filled_avg_price=100.0,
    )
    return BrokerOrder(**{**defaults, **kw})


class _Poll:
    """A broker whose get_order replies come from a scripted queue."""

    def __init__(self, *replies) -> None:
        self._replies = list(replies)
        self.polls = 0

    async def get_order(self, order_id):
        self.polls += 1
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def _txs(session: Session, user_id: int = 1) -> list[Transaction]:
    return list(session.exec(select(Transaction).where(Transaction.user_id == user_id)).all())


def _trade_service(broker, **settings_kw) -> TradeService:
    return TradeService(
        Settings(anthropic_api_key="", **settings_kw),
        MarketService(_FakeMarket(), None, _NoopCache()),
        broker_factory=lambda **kw: broker,
    )


# ============================ 1. PARTIAL FILLS ==============================

async def test_partial_fill_writes_nothing_and_keeps_the_order_open():
    """A half-filled order is not a filled order — the ledger stays untouched."""
    with _db() as s:
        order = _open_order(s)
        wrote = await _recon().reconcile_order(
            s, order, _Poll(_remote(status="partially_filled", filled_quantity=4.0,
                                    filled_avg_price=100.0))
        )
        s.commit()
        assert wrote is False
        assert order.reconciled is False      # still open for the next sweep
        assert order.filled_quantity == 4.0   # progress is tracked...
        assert _txs(s) == []                  # ...but nothing is booked


async def test_partial_then_complete_books_the_cumulative_fill_exactly_once():
    """Alpaca reports cumulative fills; we must book 10, not 4 + 10."""
    with _db() as s:
        order = _open_order(s)
        recon = _recon()
        broker = _Poll(
            _remote(status="partially_filled", filled_quantity=4.0, filled_avg_price=100.0),
            _remote(status="filled", filled_quantity=10.0, filled_avg_price=101.5),
        )
        assert await recon.reconcile_order(s, order, broker) is False
        assert await recon.reconcile_order(s, order, broker) is True
        s.commit()

        txs = _txs(s)
        assert len(txs) == 1
        assert txs[0].quantity == 10.0 and txs[0].price == 101.5


async def test_partial_fill_then_cancel_closes_without_booking():
    """The unfilled remainder is cancelled: terminal, no ledger write."""
    with _db() as s:
        order = _open_order(s)
        recon = _recon()
        broker = _Poll(
            _remote(status="partially_filled", filled_quantity=6.0, filled_avg_price=99.0),
            _remote(status="canceled", filled_quantity=6.0, filled_avg_price=99.0),
        )
        await recon.reconcile_order(s, order, broker)
        assert await recon.reconcile_order(s, order, broker) is False
        s.commit()
        assert order.reconciled is True and _txs(s) == []


async def test_webhook_and_poll_cannot_double_book_the_same_fill():
    """The fast path and the backstop race on every fill; exactly one wins."""
    with _db() as s:
        order = _open_order(s)
        recon = _recon()

        wrote_hook = await recon.ingest_fill_event(
            s,
            AlpacaTradeUpdate(
                event="fill",
                order={"id": "o1", "symbol": "AAPL", "side": "buy",
                       "status": "filled", "filled_qty": "10", "filled_avg_price": "100.25"},
            ),
        )
        wrote_poll = await recon.reconcile_order(s, order, _Poll(_remote(filled_avg_price=100.25)))
        s.commit()

        assert (wrote_hook, wrote_poll) == (True, False)
        assert len(_txs(s)) == 1


async def test_zero_quantity_fill_is_never_booked():
    """status=filled with qty 0 is broker noise, not an execution."""
    with _db() as s:
        order = _open_order(s)
        wrote = await _recon().reconcile_order(
            s, order, _Poll(_remote(filled_quantity=0.0, filled_avg_price=100.0))
        )
        s.commit()
        assert wrote is False and _txs(s) == []


async def test_fill_without_a_price_is_never_booked():
    """A fill with no average price cannot be valued — refuse to guess one."""
    with _db() as s:
        order = _open_order(s)
        wrote = await _recon().reconcile_order(
            s, order, _Poll(_remote(filled_quantity=10.0, filled_avg_price=None))
        )
        s.commit()
        assert wrote is False and _txs(s) == []


# ======================= 2. BROKER OUTAGE / TIMEOUT =========================

async def test_poll_timeout_leaves_the_order_open_for_the_next_sweep():
    with _db() as s:
        order = _open_order(s)
        wrote = await _recon().reconcile_order(
            s, order, _Poll(httpx.ReadTimeout("broker hung"))
        )
        s.commit()
        assert wrote is False and order.reconciled is False and _txs(s) == []


async def test_order_reconciles_once_the_broker_comes_back():
    """An outage must delay reconciliation, never lose the fill."""
    with _db() as s:
        order = _open_order(s)
        recon = _recon()
        broker = _Poll(httpx.ConnectError("down"), _remote(filled_avg_price=102.0))

        assert await recon.reconcile_order(s, order, broker) is False
        assert await recon.reconcile_order(s, order, broker) is True
        s.commit()
        assert len(_txs(s)) == 1 and _txs(s)[0].price == 102.0


async def test_one_users_broker_outage_does_not_stall_the_whole_sweep():
    with _db() as s:
        _open_order(s, user_id=1, broker_order_id="a1")
        _open_order(s, user_id=2, broker_order_id="b1")

        def broker_for(user_id):
            if user_id == 1:
                return _Poll(httpx.ConnectError("user 1 broker down"))
            return _Poll(_remote(id="b1", filled_avg_price=100.0))

        written = await _recon().reconcile_open_orders(s, broker_for)
        assert written == 1                       # user 2 still booked
        assert len(_txs(s, user_id=2)) == 1
        assert _txs(s, user_id=1) == []           # user 1 left for next sweep


async def test_sweep_skips_users_without_credentials():
    with _db() as s:
        _open_order(s, user_id=1)
        assert await _recon().reconcile_open_orders(s, lambda uid: None) == 0
        assert _txs(s) == []


async def test_position_drift_reports_in_sync_when_the_broker_is_unreachable():
    """Never invent drift from an outage — that would trigger a false alarm."""
    class _Down:
        async def get_positions(self):
            raise httpx.ConnectError("positions unavailable")

    with _db() as s:
        s.add(Transaction(user_id=1, symbol="AAPL", action="BUY", quantity=5, price=100))
        s.commit()
        report = await _recon().position_drift(s, 1, _Down())
        assert report.in_sync is True and report.items == []


async def test_broker_rejection_at_submission_persists_no_order():
    """If place_order fails, nothing may be written — the order does not exist."""
    class _Rejects:
        async def get_account(self):
            return BrokerAccount(account_number="PA1", status="ACTIVE",
                                 cash=1e6, buying_power=1e6)

        async def place_order(self, order: OrderRequest):
            raise BrokerError("Alpaca error 500: venue unavailable", status_code=502)

    with _db() as s:
        trade = _trade_service(_Rejects())
        trade.save_credentials(s, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678"))
        with pytest.raises(BrokerError):
            await trade.execute(s, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=1))
        assert list(s.exec(select(TradeOrder)).all()) == []


async def test_account_lookup_outage_stops_before_any_order_is_placed():
    class _AccountDown:
        def __init__(self):
            self.placed = []

        async def get_account(self):
            raise httpx.ReadTimeout("account endpoint hung")

        async def place_order(self, order):
            self.placed.append(order)
            raise AssertionError("must never be reached")

    with _db() as s:
        broker = _AccountDown()
        trade = _trade_service(broker)
        trade.save_credentials(s, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678"))
        with pytest.raises(httpx.ReadTimeout):
            await trade.execute(s, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=1))
        assert broker.placed == []


# =========================== 3. EXTREME SLIPPAGE ============================

@pytest.mark.parametrize(
    "fill_price,label",
    [(275.0, "gap-up 150%"), (11.0, "gap-down 90%"), (0.01, "flash crash")],
)
async def test_ledger_records_the_executed_price_not_the_reference(fill_price, label):
    """A gap between quote and fill must land in the ledger truthfully."""
    with _db() as s:
        order = _open_order(s, quantity=10)
        wrote = await _recon().reconcile_order(
            s, order, _Poll(_remote(filled_quantity=10.0, filled_avg_price=fill_price))
        )
        s.commit()
        assert wrote is True
        tx = _txs(s)[0]
        assert tx.price == fill_price, label
        assert tx.quantity == 10.0


async def test_slippage_beyond_the_notional_cap_is_still_booked_honestly():
    """The cap is a *pre-trade* control. Once filled, the ledger tells the truth."""
    with _db() as s:
        order = _open_order(s, quantity=100)
        await _recon().reconcile_order(
            s, order, _Poll(_remote(filled_quantity=100.0, filled_avg_price=9_999.0))
        )
        s.commit()
        assert _txs(s)[0].price == 9_999.0


async def test_notional_cap_blocks_the_order_before_submission():
    class _Broker:
        def __init__(self):
            self.placed = []

        async def get_account(self):
            return BrokerAccount(account_number="PA1", status="ACTIVE",
                                 cash=1e9, buying_power=1e9)

        async def place_order(self, order):
            self.placed.append(order)
            raise AssertionError("must never be reached")

    with _db() as s:
        broker = _Broker()
        trade = _trade_service(broker, max_order_notional=1_000.0)
        trade.save_credentials(s, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678"))
        with pytest.raises(AppError) as exc:
            await trade.execute(s, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=50))
        assert exc.value.reason == "notional" and broker.placed == []


async def test_insufficient_buying_power_is_rejected_before_submission():
    class _Poor:
        def __init__(self):
            self.placed = []

        async def get_account(self):
            return BrokerAccount(account_number="PA1", status="ACTIVE",
                                 cash=50.0, buying_power=50.0)

        async def place_order(self, order):
            self.placed.append(order)
            raise AssertionError("must never be reached")

    with _db() as s:
        broker = _Poor()
        trade = _trade_service(broker)
        trade.save_credentials(s, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678"))
        with pytest.raises(AppError) as exc:
            await trade.execute(s, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=10))
        assert exc.value.reason == "buying_power" and broker.placed == []


# ========================= 4. MARKET HALT / REJECTION =======================

@pytest.mark.parametrize("status", ["canceled", "expired", "rejected", "done_for_day"])
async def test_terminal_non_fill_closes_the_order_with_no_ledger_write(status):
    """Halt-driven cancellations close cleanly — nothing enters the ledger."""
    with _db() as s:
        order = _open_order(s)
        wrote = await _recon().reconcile_order(
            s, order, _Poll(_remote(status=status, filled_quantity=0.0, filled_avg_price=None))
        )
        s.commit()
        assert wrote is False and order.reconciled is True and _txs(s) == []


async def test_non_terminal_status_keeps_the_order_open():
    """`new`/`pending_new` during a halt must not be mistaken for terminal."""
    with _db() as s:
        order = _open_order(s)
        await _recon().reconcile_order(
            s, order, _Poll(_remote(status="pending_new", filled_quantity=0.0,
                                    filled_avg_price=None))
        )
        s.commit()
        assert order.reconciled is False


async def test_kill_switch_halt_never_reaches_the_broker():
    class _Halted:
        async def assert_not_halted(self, session, user_id):
            raise AppError("Gunluk zarar limiti asildi.", status_code=423, reason="halt")

    class _Broker:
        def __init__(self):
            self.placed = []

        async def get_account(self):
            raise AssertionError("must never be reached")

        async def place_order(self, order):
            self.placed.append(order)

    with _db() as s:
        broker = _Broker()
        trade = TradeService(
            Settings(anthropic_api_key=""),
            MarketService(_FakeMarket(), None, _NoopCache()),
            broker_factory=lambda **kw: broker,
            risk=_Halted(),
        )
        trade.save_credentials(s, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678"))
        with pytest.raises(AppError) as exc:
            await trade.execute(s, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=1))
        assert exc.value.status_code == 423 and broker.placed == []


async def test_trading_disabled_globally_blocks_execution():
    class _Broker:
        def __init__(self):
            self.placed = []

        async def get_account(self):
            raise AssertionError("must never be reached")

        async def place_order(self, order):
            self.placed.append(order)

    with _db() as s:
        broker = _Broker()
        trade = _trade_service(broker, trade_enabled=False)
        trade.save_credentials(s, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678"))
        with pytest.raises(AppError) as exc:
            await trade.execute(s, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=1))
        assert exc.value.reason == "disabled" and broker.placed == []


async def test_unknown_order_webhook_is_ignored_not_booked():
    """A stale or foreign fill event must never create a phantom transaction."""
    with _db() as s:
        wrote = await _recon().ingest_fill_event(
            s,
            AlpacaTradeUpdate(
                event="fill",
                order={"id": "not-ours", "symbol": "AAPL", "side": "buy",
                       "status": "filled", "filled_qty": "5", "filled_avg_price": "100"},
            ),
        )
        assert wrote is False and _txs(s) == []


async def test_drift_is_flagged_when_a_halt_left_the_ledger_behind():
    """Broker holds stock the ledger never booked — the operator must see it."""
    class _Positions:
        async def get_positions(self):
            return [BrokerPosition(symbol="AAPL", quantity=7.0, avg_entry_price=100.0)]

    with _db() as s:
        s.add(Transaction(user_id=1, symbol="AAPL", action="BUY", quantity=3, price=100))
        s.commit()
        report = await _recon().position_drift(s, 1, _Positions())
        assert report.in_sync is False
        assert report.items[0].symbol == "AAPL" and report.items[0].drift == 4.0


# ===================== 5. PRE-TRADE GATES ON THE SELL/LIMIT PATH ============
#
# The gates above are all exercised on the BUY + market-order path. These pin
# the branches the happy path never reaches: the short side, a limit order
# priced off its own limit, and a feed that quotes something impossible.


class _QuotingMarket:
    """A market boundary that quotes exactly what the test tells it to."""

    def __init__(self, price: float) -> None:
        self.price = price
        self.asked = []

    async def get_market_data(self, symbol: str) -> MarketData:
        self.asked.append(symbol)
        return MarketData(
            quote=Quote(symbol=symbol, price=self.price), indicators=Indicators()
        )


class _Recorder:
    """A broker that accepts anything and remembers what it was handed."""

    def __init__(self, buying_power: float = 1e6) -> None:
        self.placed: list[OrderRequest] = []
        self._buying_power = buying_power

    async def get_account(self) -> BrokerAccount:
        return BrokerAccount(
            account_number="PA1", status="ACTIVE",
            cash=self._buying_power, buying_power=self._buying_power,
        )

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        self.placed.append(order)
        return _remote(id="o-new", side=order.side, quantity=order.quantity,
                       status="accepted", filled_quantity=0.0, filled_avg_price=None)


def _trade_service_with_market(broker, market, **settings_kw) -> TradeService:
    return TradeService(
        Settings(anthropic_api_key="", **settings_kw),
        market,
        broker_factory=lambda **kw: broker,
    )


async def _with_credentials(trade: TradeService, session: Session) -> None:
    trade.save_credentials(
        session, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678")
    )


async def test_a_sell_reaches_the_broker_as_the_short_side():
    """SELL must resolve to 'sell' — a mis-resolved side is a wrong-way trade."""
    with _db() as s:
        broker = _Recorder()
        trade = _trade_service_with_market(broker, _QuotingMarket(REFERENCE_PRICE))
        await _with_credentials(trade, s)

        result = await trade.execute(
            s, 1, TradeRequest(symbol="AAPL", action="SELL", quantity=10)
        )

        assert broker.placed[0].side == "sell"
        assert result.accepted is True
        assert list(s.exec(select(TradeOrder)).all())[0].side == "sell"


async def test_selling_is_not_blocked_by_buying_power():
    """Buying power constrains purchases only; a sell raises cash, it never spends it."""
    with _db() as s:
        broker = _Recorder(buying_power=1.0)          # far below the notional
        trade = _trade_service_with_market(
            broker, _QuotingMarket(REFERENCE_PRICE), max_order_notional=1e6
        )
        await _with_credentials(trade, s)

        await trade.execute(
            s, 1, TradeRequest(symbol="AAPL", action="SELL", quantity=100)
        )

        assert broker.placed[0].side == "sell", "the sell must not hit the cash gate"


async def test_a_limit_order_is_sized_off_its_limit_not_the_live_quote():
    """The cap must be measured against the price we are actually willing to pay."""
    with _db() as s:
        broker = _Recorder()
        market = _QuotingMarket(1.0)                  # a cheap, irrelevant quote
        trade = _trade_service_with_market(broker, market, max_order_notional=1_000.0)
        await _with_credentials(trade, s)

        with pytest.raises(AppError) as exc:
            await trade.execute(
                s, 1,
                TradeRequest(symbol="AAPL", action="BUY", quantity=10,
                             order_type="limit", limit_price=500.0),
            )

        assert exc.value.reason == "notional", "sized off the 1.0 quote, not the limit"
        assert broker.placed == []
        assert market.asked == [], "a limit order must not need the live feed at all"


async def test_a_limit_order_within_the_cap_is_submitted_with_its_limit():
    with _db() as s:
        broker = _Recorder()
        trade = _trade_service_with_market(
            broker, _QuotingMarket(REFERENCE_PRICE), max_order_notional=10_000.0
        )
        await _with_credentials(trade, s)

        await trade.execute(
            s, 1,
            TradeRequest(symbol="AAPL", action="BUY", quantity=2,
                         order_type="limit", limit_price=250.0),
        )

        assert broker.placed[0].order_type == "limit"
        assert broker.placed[0].limit_price == 250.0


@pytest.mark.parametrize("bad_price", [0.0, -5.0])
async def test_an_impossible_quote_is_rejected_before_it_becomes_an_order(bad_price):
    """A degraded feed quoting 0 or negative must not size a zero-notional order."""
    with _db() as s:
        broker = _Recorder()
        trade = _trade_service_with_market(broker, _QuotingMarket(bad_price))
        await _with_credentials(trade, s)

        with pytest.raises(AppError) as exc:
            await trade.execute(
                s, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=10)
            )

        assert exc.value.reason == "notional"
        assert exc.value.status_code == 422
        assert broker.placed == []
        assert list(s.exec(select(TradeOrder)).all()) == []


async def test_reading_credentials_back_exposes_a_fingerprint_and_nothing_more():
    """Decryption round-trips, but the projection leaks neither key nor secret."""
    with _db() as s:
        trade = _trade_service_with_market(_Recorder(), _QuotingMarket(REFERENCE_PRICE))
        await _with_credentials(trade, s)

        read = trade.get_credential_read(s, 1, "alpaca")

        assert read is not None
        assert read.broker == "alpaca"
        assert read.api_key_last4 == "1234", "the fingerprint proves it decrypted"
        body = read.model_dump_json()
        assert "PKID1234" not in body, "the full key must never surface"
        assert "SEC5678" not in body, "the secret must never surface"


async def test_credentials_for_an_unknown_broker_read_back_as_absent():
    with _db() as s:
        trade = _trade_service_with_market(_Recorder(), _QuotingMarket(REFERENCE_PRICE))
        await _with_credentials(trade, s)

        assert trade.get_credential_read(s, 1, "not-a-broker") is None
