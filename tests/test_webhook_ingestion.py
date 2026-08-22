"""Broker webhook ingestion — the event-driven fast path for fills.

Guarantees under test:
  * authentication is constant-time shared-secret and closed by default,
  * an inbound fill lands in the ledger immediately (no poll wait),
  * it is idempotent with the polling backstop — a fill is booked exactly once,
    from whichever path arrives first,
  * malformed or unknown payloads are rejected/ignored, never crash the endpoint.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.models.order import TradeOrder
from app.models.transaction import Transaction
from app.models.webhook import AlpacaTradeUpdate
from app.services.portfolio_service import PortfolioService
from app.services.reconciliation_service import TradeReconciliationService


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def recon(disabled_ai):
    from tests.conftest import _FakeMarketProvider

    return TradeReconciliationService(PortfolioService(_FakeMarketProvider(), disabled_ai))


def _open_order(session: Session, broker_order_id: str = "ord-1") -> TradeOrder:
    order = TradeOrder(
        user_id=1,
        broker="alpaca",
        broker_order_id=broker_order_id,
        symbol="AAPL",
        side="buy",
        quantity=10,
        order_type="market",
        status="pending",
        reconciled=False,
    )
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def _fill_event(order_id: str = "ord-1", qty: float = 10, price: float = 101.5) -> AlpacaTradeUpdate:
    return AlpacaTradeUpdate(
        event="fill",
        order={
            "id": order_id,
            "symbol": "AAPL",
            "side": "buy",
            "status": "filled",
            "filled_qty": qty,
            "filled_avg_price": price,
        },
    )


# --- Payload validation at the boundary ---
def test_payload_coerces_alpaca_string_numerics():
    """Alpaca sends numerics as strings; the model must coerce, not reject."""
    update = AlpacaTradeUpdate(
        event="fill",
        order={
            "id": "ord-9",
            "symbol": "AAPL",
            "side": "buy",
            "status": "filled",
            "filled_qty": "3",
            "filled_avg_price": "99.25",
        },
    )
    assert update.order.filled_qty == 3.0
    assert update.order.filled_avg_price == 99.25


def test_payload_rejects_missing_order_id():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AlpacaTradeUpdate(event="fill", order={"id": "", "status": "filled"})


def test_payload_ignores_unknown_keys():
    update = AlpacaTradeUpdate(
        event="fill",
        order={"id": "ord-1", "status": "filled", "malicious": "<script>"},
    )
    assert not hasattr(update.order, "malicious")


# --- Ingestion semantics ---
@pytest.mark.asyncio
async def test_webhook_fill_writes_transaction_immediately(session, recon):
    order = _open_order(session)

    wrote = await recon.ingest_fill_event(session, _fill_event())

    assert wrote is True
    session.refresh(order)
    assert order.reconciled is True
    assert order.status == "filled"
    txns = list(session.exec(select(Transaction)).all())
    assert len(txns) == 1
    assert (txns[0].symbol, txns[0].action, txns[0].quantity) == ("AAPL", "BUY", 10)
    assert txns[0].price == 101.5


@pytest.mark.asyncio
async def test_duplicate_webhook_is_idempotent(session, recon):
    """Alpaca retries deliveries; a redelivered fill must not double-book."""
    _open_order(session)

    assert await recon.ingest_fill_event(session, _fill_event()) is True
    assert await recon.ingest_fill_event(session, _fill_event()) is False

    assert len(list(session.exec(select(Transaction)).all())) == 1


@pytest.mark.asyncio
async def test_poll_backstop_does_not_rebook_a_webhook_fill(session, recon):
    """The poll loop is a backstop: it must be idempotent with the fast path."""
    order = _open_order(session)
    await recon.ingest_fill_event(session, _fill_event())

    class _Broker:
        async def get_order(self, _id):
            from app.models.broker import BrokerOrder

            return BrokerOrder(
                id="ord-1",
                symbol="AAPL",
                side="buy",
                quantity=10,
                order_type="market",
                status="filled",
                filled_quantity=10,
                filled_avg_price=101.5,
            )

    session.refresh(order)
    wrote = await recon.reconcile_order(session, order, _Broker())
    session.commit()

    assert wrote is False
    assert len(list(session.exec(select(Transaction)).all())) == 1


@pytest.mark.asyncio
async def test_unknown_order_id_is_ignored_not_raised(session, recon):
    """A foreign/stale event must be accepted and written nowhere."""
    assert await recon.ingest_fill_event(session, _fill_event("nope")) is False
    assert list(session.exec(select(Transaction)).all()) == []


@pytest.mark.asyncio
async def test_canceled_event_closes_order_without_a_transaction(session, recon):
    order = _open_order(session)
    update = AlpacaTradeUpdate(
        event="canceled",
        order={"id": "ord-1", "symbol": "AAPL", "side": "buy", "status": "canceled"},
    )

    assert await recon.ingest_fill_event(session, update) is False
    session.refresh(order)
    assert order.reconciled is True  # terminal, nothing to book
    assert list(session.exec(select(Transaction)).all()) == []


@pytest.mark.asyncio
async def test_partial_fill_stays_open_for_the_final_event(session, recon):
    order = _open_order(session)
    update = AlpacaTradeUpdate(
        event="partial_fill",
        order={
            "id": "ord-1",
            "symbol": "AAPL",
            "side": "buy",
            "status": "partially_filled",
            "filled_qty": 4,
            "filled_avg_price": 100.0,
        },
    )

    assert await recon.ingest_fill_event(session, update) is False
    session.refresh(order)
    assert order.reconciled is False   # still open
    assert order.filled_quantity == 4  # progress is recorded
    assert list(session.exec(select(Transaction)).all()) == []


@pytest.mark.asyncio
async def test_fill_increments_the_webhook_source_metric(session, recon):
    from prometheus_client import REGISTRY

    def value() -> float:
        return REGISTRY.get_sample_value(
            "trade_fills_reconciled_total", {"source": "webhook"}
        ) or 0.0

    _open_order(session)
    before = value()
    await recon.ingest_fill_event(session, _fill_event())
    assert value() == before + 1


# --- Endpoint authentication ---
def test_webhook_disabled_without_secret(client):
    """Closed by default: no configured secret means the endpoint is off."""
    resp = client.post("/api/trade/webhook", json=_fill_event().model_dump())
    assert resp.status_code == 503


def test_webhook_rejects_wrong_and_missing_secret(client, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "trade_webhook_secret", "s3cret", raising=False)
    payload = _fill_event().model_dump()

    assert client.post("/api/trade/webhook", json=payload).status_code == 401
    assert client.post(
        "/api/trade/webhook", json=payload, headers={"X-Webhook-Secret": "wrong"}
    ).status_code == 401


def test_webhook_accepts_correct_secret(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "trade_webhook_secret", "s3cret", raising=False)

    resp = client.post(
        "/api/trade/webhook",
        json=_fill_event("unknown-order").model_dump(),
        headers={"X-Webhook-Secret": "s3cret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"accepted": True, "reconciled": False, "detail": ""}


def test_webhook_rejects_malformed_body(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "trade_webhook_secret", "s3cret", raising=False)

    resp = client.post(
        "/api/trade/webhook",
        json={"event": "fill"},  # missing 'order'
        headers={"X-Webhook-Secret": "s3cret"},
    )
    assert resp.status_code == 422
