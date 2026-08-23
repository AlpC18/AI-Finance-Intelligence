"""The append-only compliance trail, and who is allowed to read it.

The audit log is the record of *why* a trade happened - the signal, its
confidence, and the AI's reasoning. Two properties matter more than the
formatting: it is scoped per user (one tenant must never read another's
reasoning), and it outlives the orders it refers to (append-only means a row
must still render after its order is gone).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.core.config import Settings
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


class _Broker:
    """Hands back a distinct order id per submission so rows stay tellable apart."""

    def __init__(self) -> None:
        self.n = 0

    async def get_account(self) -> BrokerAccount:
        return BrokerAccount(account_number="PA1", status="ACTIVE",
                             cash=1e9, buying_power=1e9)

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        self.n += 1
        return BrokerOrder(
            id=f"ord-{self.n}", symbol=order.symbol, side=order.side,
            quantity=order.quantity, order_type=order.order_type,
            status="accepted", filled_quantity=0.0, filled_avg_price=None,
        )


def _service() -> TradeService:
    broker = _Broker()  # one instance: ids increment, as a real venue's would
    return TradeService(
        Settings(anthropic_api_key="", max_order_notional=1e9),
        _Market(),
        broker_factory=lambda **kw: broker,
    )


async def _execute(trade: TradeService, s: Session, user_id: int, **kw) -> None:
    req = TradeRequest(**{"symbol": "AAPL", "action": "BUY", "quantity": 1, **kw})
    await trade.execute(s, user_id, req)


def _with_creds(trade: TradeService, s: Session, user_id: int) -> None:
    trade.save_credentials(
        s, user_id, CredentialCreate(api_key="PKID1234", api_secret="SEC5678")
    )


# ============================== WRITE SIDE =================================

async def test_executing_a_signal_records_its_confidence_on_the_audit_row():
    """Confidence is the gate input; without it the scorecard cannot calibrate."""
    with _db() as s:
        trade = _service()
        _with_creds(trade, s, 1)
        await _execute(trade, s, 1, confidence=0.82, ai_context="momentum + earnings beat")

        row = s.exec(select(TradeAuditLog)).one()
        assert row.confidence == 0.82
        assert row.signal_type == "BUY"
        assert row.raw_ai_context == "momentum + earnings beat"


async def test_a_signal_without_confidence_records_null_not_zero():
    """A missing reading must stay missing — 0.0 would read as 'no confidence'."""
    with _db() as s:
        trade = _service()
        _with_creds(trade, s, 1)
        await _execute(trade, s, 1)

        assert s.exec(select(TradeAuditLog)).one().confidence is None


# ============================== READ SIDE ==================================

async def test_the_trail_reads_back_newest_first_with_the_joined_order():
    with _db() as s:
        trade = _service()
        _with_creds(trade, s, 1)
        await _execute(trade, s, 1, symbol="AAPL", confidence=0.6)
        await _execute(trade, s, 1, symbol="MSFT", confidence=0.9)

        # Same-instant timestamps would make "newest first" untestable.
        rows = s.exec(select(TradeAuditLog).order_by(TradeAuditLog.id)).all()
        rows[0].execution_timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
        s.add(rows[0])
        s.commit()

        page = trade.list_audit_log(s, 1)

        assert page.total == 2
        assert [i.symbol for i in page.items] == ["MSFT", "AAPL"]
        assert page.items[0].confidence == 0.9
        assert page.items[0].status == "accepted", "the order fields are joined in"


async def test_the_trail_paginates_without_losing_the_total():
    with _db() as s:
        trade = _service()
        _with_creds(trade, s, 1)
        for _ in range(5):
            await _execute(trade, s, 1)

        first = trade.list_audit_log(s, 1, limit=2, offset=0)
        second = trade.list_audit_log(s, 1, limit=2, offset=2)

        assert first.total == second.total == 5, "total is the unpaginated count"
        assert len(first.items) == len(second.items) == 2
        assert {i.order_id for i in first.items}.isdisjoint(
            {i.order_id for i in second.items}
        ), "pages must not overlap"


async def test_the_trail_can_be_filtered_to_one_symbol():
    with _db() as s:
        trade = _service()
        _with_creds(trade, s, 1)
        await _execute(trade, s, 1, symbol="AAPL")
        await _execute(trade, s, 1, symbol="MSFT")

        page = trade.list_audit_log(s, 1, symbol="msft")

        assert page.total == 1
        assert page.items[0].symbol == "MSFT", "the filter is case-insensitive"


async def test_one_users_reasoning_is_never_visible_to_another():
    """The strongest property here: signal reasoning is private per tenant."""
    with _db() as s:
        trade = _service()
        _with_creds(trade, s, 1)
        _with_creds(trade, s, 2)
        await _execute(trade, s, 1, ai_context="user-one secret thesis")
        await _execute(trade, s, 2, ai_context="user-two secret thesis")

        page = trade.list_audit_log(s, 2)

        assert page.total == 1
        assert page.items[0].raw_ai_context == "user-two secret thesis"
        assert "user-one" not in page.model_dump_json()


async def test_an_audit_row_outlives_the_order_it_refers_to():
    """Append-only: purging the order must not erase or break the trail."""
    with _db() as s:
        trade = _service()
        _with_creds(trade, s, 1)
        await _execute(trade, s, 1, confidence=0.7)

        for order in s.exec(select(TradeOrder)).all():
            s.delete(order)
        s.commit()

        page = trade.list_audit_log(s, 1)

        assert page.total == 1, "the trail survives"
        item = page.items[0]
        assert item.confidence == 0.7, "the signal is still fully readable"
        assert item.symbol is None and item.status is None, "order fields absent, not faked"


async def test_a_user_with_no_history_gets_an_empty_page_not_an_error():
    with _db() as s:
        page = _service().list_audit_log(s, 99)
        assert page.total == 0 and page.items == []


# ============================== HTTP SURFACE ===============================

def test_the_audit_endpoint_requires_authentication(client):
    assert client.get("/api/trade/audit").status_code in (401, 403)


def test_the_audit_endpoint_returns_an_empty_page_for_a_new_user(client, auth_headers):
    res = client.get("/api/trade/audit", headers=auth_headers)

    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0 and body["items"] == []
    assert body["limit"] == 50 and body["offset"] == 0


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 500}, {"offset": -1}])
def test_the_audit_endpoint_rejects_out_of_range_paging(client, auth_headers, params):
    res = client.get("/api/trade/audit", params=params, headers=auth_headers)
    assert res.status_code == 422


async def test_a_colliding_broker_order_id_cannot_leak_across_tenants():
    """Two tenants sharing a broker order id must not see each other's fills.

    The join is keyed on the id AND the user, so a collision (a broker reusing
    ids, or a test double) can never attach one user's execution to another
    user's audit row.
    """
    with _db() as s:
        trade = _service()
        _with_creds(trade, s, 1)
        await _execute(trade, s, 1, symbol="AAPL")

        # User two's audit row carries the SAME order id as user one's order.
        s.add(TradeAuditLog(user_id=2, order_id="ord-1", signal_type="SELL"))
        s.commit()

        page = trade.list_audit_log(s, 2)

        assert page.total == 1, "no fan-out from the colliding id"
        assert page.items[0].symbol is None, "user one's order must not attach"
