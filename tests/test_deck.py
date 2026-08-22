"""UI contracts: kill-switch toggle, unified trade deck, machine-readable schemas."""
import numpy as np
import pandas as pd

from app.core.config import Settings
from app.core.deps import get_risk_service, get_trade_service
from app.services.market_service import MarketService
from app.services.portfolio_service import PortfolioService
from app.services.risk_service import RiskService
from app.services.trade_service import TradeService


class _Mkt:
    async def get_history(self, symbol, period="6mo"):
        return pd.DataFrame({"Close": np.linspace(100, 110, 30)})

    async def get_price(self, symbol):
        return 110.0


class _Cache:
    async def get_or_set(self, key, factory, ttl=None):
        return await factory()


def _wire(client):
    portfolio = PortfolioService(_Mkt(), None)
    risk = RiskService(portfolio)
    trade = TradeService(Settings(anthropic_api_key=""), MarketService(_Mkt(), None, _Cache()), risk=risk)
    client.app.dependency_overrides[get_risk_service] = lambda: risk
    client.app.dependency_overrides[get_trade_service] = lambda: trade


def test_meta_contracts_are_exposed(client):
    body = client.get("/api/meta/contracts").json()
    assert body["ws"]["insights_path"] == "/ws/insights/{symbol}"
    for key in ("ws_frames", "kill_switch", "trade_request", "backtest_request", "backtest_report"):
        assert key in body


def test_kill_switch_manual_toggle(client, auth_headers):
    _wire(client)
    on = client.post("/api/trade/kill-switch", headers=auth_headers, json={"enabled": True})
    assert on.status_code == 200
    st = on.json()
    assert st["halted"] is True and st["manual_halt"] is True and st["reason"] == "manual"

    off = client.post("/api/trade/kill-switch", headers=auth_headers, json={"enabled": False})
    assert off.json()["halted"] is False


def test_trade_deck_unified_payload(client, auth_headers):
    _wire(client)
    body = client.get("/api/trade/deck", headers=auth_headers).json()
    for key in ("has_credentials", "open_orders", "kill_switch", "risk_config",
                "max_order_notional", "min_trade_confidence", "trade_enabled", "ws"):
        assert key in body
    assert body["has_credentials"] is False
    assert body["open_orders"] == 0
    assert body["ws"]["insights_path"] == "/ws/insights/{symbol}"
