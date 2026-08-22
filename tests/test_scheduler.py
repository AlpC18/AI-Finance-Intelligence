"""Alert evaluation logic + scheduler wiring (no network, no real jobs)."""
import pytest

from app.core.config import get_settings
from app.core.scheduler import build_scheduler
from app.models.alert import Alert
from app.models.market import Indicators, MarketData, Quote, Signal
from app.services.alert_service import AlertService


class _StubMarket:
    def __init__(self, price=100.0, rsi=None, signal=None):
        self._price = price
        self._rsi = rsi
        self._signal = signal or Signal(action="HOLD", degraded=True)

    async def get_market_data(self, symbol):
        return MarketData(
            quote=Quote(symbol=symbol, price=self._price),
            indicators=Indicators(rsi_14=self._rsi),
        )

    async def get_signal(self, symbol):
        return self._signal


def _alert(condition, threshold=None):
    return Alert(user_id=1, symbol="AAPL", condition_type=condition, threshold_value=threshold)


@pytest.mark.asyncio
async def test_price_above():
    svc = AlertService(_StubMarket(price=200))
    assert await svc.evaluate(_alert("PRICE_ABOVE", 150)) is True
    assert await svc.evaluate(_alert("PRICE_ABOVE", 250)) is False


@pytest.mark.asyncio
async def test_rsi_below():
    svc = AlertService(_StubMarket(price=100, rsi=25))
    assert await svc.evaluate(_alert("RSI_BELOW", 30)) is True
    assert await svc.evaluate(_alert("RSI_BELOW", 20)) is False


@pytest.mark.asyncio
async def test_ai_strong_buy():
    strong = Signal(action="BUY", confidence=0.9, degraded=False)
    svc = AlertService(_StubMarket(signal=strong))
    assert await svc.evaluate(_alert("AI_SIGNAL_STRONG_BUY")) is True
    weak = AlertService(_StubMarket(signal=Signal(action="BUY", confidence=0.3, degraded=False)))
    assert await weak.evaluate(_alert("AI_SIGNAL_STRONG_BUY")) is False


def test_scheduler_registers_interval_job():
    scheduler = build_scheduler(get_settings())
    job = scheduler.get_job("alert-checks")
    assert job is not None
