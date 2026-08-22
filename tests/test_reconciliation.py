"""Execution feedback loop: order->ledger reconciliation, drift, audit, kill-switch."""
from datetime import datetime, timezone

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
from app.models.order import TradeAuditLog, TradeOrder
from app.models.risk import EquitySnapshot, RiskConfigUpdate
from app.models.transaction import Transaction
from app.services.market_service import MarketService
from app.services.portfolio_service import PortfolioService
from app.services.reconciliation_service import TradeReconciliationService
from app.services.risk_service import RiskService
from app.services.trade_service import TradeService


def _engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


class _FakeMarket:
    async def get_history(self, symbol, period="6mo"):
        return pd.DataFrame({"Close": np.linspace(100, 110, 30)})

    async def get_price(self, symbol):
        return 110.0


class _NoopCache:
    async def get_or_set(self, key, factory, ttl=None):
        return await factory()


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


# --- Order reconciliation into the ledger -------------------------------------
@pytest.mark.asyncio
async def test_reconcile_order_writes_fill_into_ledger():
    engine = _engine()
    with Session(engine) as s:
        order = TradeOrder(user_id=1, broker_order_id="o1", symbol="AAPL",
                           side="buy", quantity=3, status="accepted")
        s.add(order); s.commit(); s.refresh(order)

        class _P:
            async def get_order(self, oid):
                return BrokerOrder(id=oid, symbol="AAPL", side="buy", quantity=3,
                                   order_type="market", status="filled",
                                   filled_quantity=3, filled_avg_price=101.0)

        recon = TradeReconciliationService(PortfolioService(None, None))
        wrote = await recon.reconcile_order(s, order, _P())
        s.commit()

        assert wrote is True
        txs = list(s.exec(select(Transaction).where(Transaction.user_id == 1)).all())
        assert len(txs) == 1
        assert txs[0].action == "BUY" and txs[0].quantity == 3 and txs[0].price == 101.0
        assert order.reconciled is True


@pytest.mark.asyncio
async def test_reconcile_is_idempotent_and_skips_terminal_non_fill():
    engine = _engine()
    with Session(engine) as s:
        order = TradeOrder(user_id=1, broker_order_id="o2", symbol="AAPL",
                           side="sell", quantity=1, status="accepted")
        s.add(order); s.commit(); s.refresh(order)

        class _Canceled:
            async def get_order(self, oid):
                return BrokerOrder(id=oid, symbol="AAPL", side="sell", quantity=1,
                                   order_type="market", status="canceled",
                                   filled_quantity=0.0)

        recon = TradeReconciliationService(PortfolioService(None, None))
        wrote = await recon.reconcile_order(s, order, _Canceled())
        s.commit()
        assert wrote is False
        assert order.reconciled is True  # terminal, closed with no ledger write
        assert not list(s.exec(select(Transaction)).all())


# --- Position drift -----------------------------------------------------------
@pytest.mark.asyncio
async def test_position_drift_flags_mismatch():
    engine = _engine()
    with Session(engine) as s:
        s.add(Transaction(user_id=1, symbol="AAPL", action="BUY", quantity=3, price=100))
        s.commit()

        class _P:
            async def get_positions(self):
                return [BrokerPosition(symbol="AAPL", quantity=5),
                        BrokerPosition(symbol="MSFT", quantity=2)]

        recon = TradeReconciliationService(PortfolioService(None, None))
        report = await recon.position_drift(s, 1, _P())
        assert report.in_sync is False
        drift = {i.symbol: i.drift for i in report.items}
        assert drift["AAPL"] == 2.0  # broker 5 - ledger 3
        assert drift["MSFT"] == 2.0  # broker 2 - ledger 0


# --- Kill-switch --------------------------------------------------------------
@pytest.mark.asyncio
async def test_kill_switch_halts_on_daily_drawdown():
    engine = _engine()
    with Session(engine) as s:
        s.add(Transaction(user_id=1, symbol="AAPL", action="BUY", quantity=1, price=100))
        s.commit()
        today = datetime.now(timezone.utc).date().isoformat()
        s.add(EquitySnapshot(user_id=1, snapshot_date=today, opening_equity=1_000_000.0))
        s.commit()

        risk = RiskService(PortfolioService(_FakeMarket(), None))
        risk.set_config(s, 1, RiskConfigUpdate(daily_loss_limit_pct=5.0))
        status = await risk.status(s, 1)
        assert status.halted is True and status.drawdown_pct < -5

        with pytest.raises(AppError) as exc:
            await risk.assert_not_halted(s, 1)
        assert exc.value.status_code == 423


@pytest.mark.asyncio
async def test_kill_switch_disabled_when_limit_zero():
    engine = _engine()
    with Session(engine) as s:
        risk = RiskService(PortfolioService(_FakeMarket(), None))
        status = await risk.status(s, 1)  # no config -> limit 0
        assert status.halted is False
        await risk.assert_not_halted(s, 1)  # does not raise


# --- Execute persists order + audit, and honors the kill-switch ---------------
@pytest.mark.asyncio
async def test_execute_persists_order_and_audit_log():
    engine = _engine()
    with Session(engine) as s:
        broker = _FakeBroker()
        trade = TradeService(Settings(anthropic_api_key=""),
                             MarketService(_FakeMarket(), None, _NoopCache()),
                             broker_factory=lambda **kw: broker)
        trade.save_credentials(s, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678"))
        res = await trade.execute(s, 1, TradeRequest(symbol="AAPL", action="BUY",
                                                     quantity=2, ai_context="RSI oversold -> BUY"))
        assert res.accepted is True

        orders = list(s.exec(select(TradeOrder).where(TradeOrder.user_id == 1)).all())
        assert len(orders) == 1 and orders[0].broker_order_id == "ord_1"
        audits = list(s.exec(select(TradeAuditLog).where(TradeAuditLog.user_id == 1)).all())
        assert len(audits) == 1
        assert audits[0].signal_type == "BUY" and "RSI oversold" in audits[0].raw_ai_context


@pytest.mark.asyncio
async def test_execute_blocked_when_kill_switch_active():
    engine = _engine()
    with Session(engine) as s:
        broker = _FakeBroker()

        class _HaltRisk:
            async def assert_not_halted(self, session, user_id):
                raise AppError("halt", status_code=423)

        trade = TradeService(Settings(anthropic_api_key=""),
                             MarketService(_FakeMarket(), None, _NoopCache()),
                             broker_factory=lambda **kw: broker, risk=_HaltRisk())
        trade.save_credentials(s, 1, CredentialCreate(api_key="PKID1234", api_secret="SEC5678"))
        with pytest.raises(AppError) as exc:
            await trade.execute(s, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=1))
        assert exc.value.status_code == 423
        assert broker.placed == []  # never reached the broker
