"""End-to-end: AI signal -> order execution -> reconciliation -> ledger -> equity.

Pytest E2E (browserless). The same flow is Playwright-drivable against these HTTP
routes; here we exercise the full backend path through the app + a fake broker.
"""
import asyncio

import numpy as np
import pandas as pd
from sqlmodel import Session

from app.core.config import Settings
from app.core.deps import (
    get_reconciliation_service,
    get_risk_service,
    get_trade_service,
)
from app.models.broker import BrokerAccount, BrokerOrder, BrokerPosition
from app.services.market_service import MarketService
from app.services.portfolio_service import PortfolioService
from app.services.reconciliation_service import TradeReconciliationService
from app.services.risk_service import RiskService
from app.services.trade_service import TradeService


class _Mkt:
    async def get_history(self, symbol, period="6mo"):
        return pd.DataFrame({"Close": np.linspace(100, 105, 30)})

    async def get_price(self, symbol):
        return 105.0


class _Cache:
    async def get_or_set(self, key, factory, ttl=None):
        return await factory()


class _Broker:
    def __init__(self, **_):
        self.placed = []

    async def get_account(self):
        return BrokerAccount(account_number="PA", status="ACTIVE",
                             cash=100000, buying_power=100000)

    async def place_order(self, order):
        self.placed.append(order)
        return BrokerOrder(id="ord_9", symbol=order.symbol, side=order.side,
                           quantity=order.quantity, order_type=order.order_type,
                           status="accepted", filled_quantity=0.0)

    async def get_order(self, order_id):  # fills on the first reconciliation poll
        return BrokerOrder(id=order_id, symbol="AAPL", side="buy", quantity=2,
                           order_type="market", status="filled",
                           filled_quantity=2, filled_avg_price=105.0)

    async def get_positions(self):
        return [BrokerPosition(symbol="AAPL", quantity=2)]


def test_e2e_signal_to_ledger_to_equity(client, auth_headers):
    broker = _Broker()
    portfolio = PortfolioService(_Mkt(), None)
    risk = RiskService(portfolio)
    trade = TradeService(Settings(anthropic_api_key=""), MarketService(_Mkt(), None, _Cache()),
                         broker_factory=lambda **kw: broker, risk=risk)
    recon = TradeReconciliationService(portfolio)

    app = client.app
    app.dependency_overrides[get_trade_service] = lambda: trade
    app.dependency_overrides[get_risk_service] = lambda: risk
    app.dependency_overrides[get_reconciliation_service] = lambda: recon
    engine = app.state.test_engine

    # 1) store broker credentials (encrypted at rest)
    assert client.post("/api/trade/credentials", headers=auth_headers,
                       json={"api_key": "PKID1234", "api_secret": "SEC5678"}).status_code == 201

    # 2) AI signal -> execute paper order
    res = client.post("/api/trade/execute", headers=auth_headers, json={
        "symbol": "AAPL", "action": "BUY", "quantity": 2, "confidence": 0.9,
        "ai_context": "RSI oversold + bullish news -> BUY",
    })
    assert res.status_code == 200, res.text
    assert res.json()["accepted"] is True

    orders = client.get("/api/trade/orders", headers=auth_headers).json()
    assert len(orders) == 1 and orders[0]["reconciled"] is False

    # 3) reconciliation sweep polls the fill and writes it into the ledger
    with Session(engine) as s:
        written = asyncio.run(
            recon.reconcile_open_orders(s, lambda uid: trade.provider_for_user(s, uid))
        )
    assert written == 1

    # 4) ledger entry reflects the executed fill (price + quantity)
    txs = client.get("/api/portfolio/transactions", headers=auth_headers).json()
    assert any(
        t["symbol"] == "AAPL" and t["action"] == "BUY"
        and t["quantity"] == 2 and t["price"] == 105.0
        for t in txs
    )
    assert client.get("/api/trade/orders", headers=auth_headers).json()[0]["reconciled"] is True

    # 5) equity updates from the new ledger position; positions are now in sync
    ks = client.get("/api/trade/kill-switch", headers=auth_headers).json()
    assert ks["current_equity"] >= 200  # ~2 shares * 105
    assert client.get("/api/trade/reconciliation", headers=auth_headers).json()["in_sync"] is True
