import numpy as np
import pandas as pd
import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from app.core.errors import AppError
from app.models.paper import PaperAccountCreate, PaperOrderRequest
from app.services.paper_trading_service import PaperTradingService


class _Market:
    async def get_market_data(self, symbol):
        from app.models.market import MarketData, Quote, Indicators
        return MarketData(quote=Quote(symbol=symbol, price=100), indicators=Indicators())


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


@pytest.mark.asyncio
async def test_paper_account_applies_slippage_fee_cash_and_realized_pnl():
    with _session() as session:
        service = PaperTradingService(_Market())
        await service.execute(session, 1, PaperOrderRequest(symbol="AAPL", side="buy", quantity=10))
        after_buy = service.account(session, 1)
        assert after_buy.cash < 99000 and after_buy.fills[0].fill_price > 100
        await service.execute(session, 1, PaperOrderRequest(symbol="AAPL", side="sell", quantity=10))
        after_sell = service.account(session, 1)
        assert len(after_sell.fills) == 2 and after_sell.realized_pnl < 0


@pytest.mark.asyncio
async def test_paper_account_rejects_oversell_and_can_reset():
    with _session() as session:
        service = PaperTradingService(_Market())
        with pytest.raises(AppError) as exc:
            await service.execute(session, 1, PaperOrderRequest(symbol="AAPL", side="sell", quantity=1))
        assert exc.value.reason == "paper_oversell"
        reset = service.reset(session, 1, PaperAccountCreate(starting_cash=500))
        assert reset.cash == 500 and reset.fills == []
