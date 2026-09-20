import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models.automation import AutomationRuleCreate
from app.services.automation_service import AutomationService
from app.services.paper_trading_service import PaperTradingService
from app.core.errors import NotFoundError


class _Market:
    async def get_market_data(self, symbol):
        from app.models.market import Indicators, MarketData, Quote
        return MarketData(quote=Quote(symbol=symbol, price=90), indicators=Indicators(rsi_14=20))


@pytest.mark.asyncio
async def test_automation_runs_paper_only_and_honors_cooldown():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        market = _Market(); paper = PaperTradingService(market)
        service = AutomationService(market, paper)
        service.create(session, 1, AutomationRuleCreate(symbol="aapl", trigger="RSI_BELOW", quantity=1, threshold=30))
        first = await service.run(session)
        second = await service.run(session)
        assert first.executed == 1
        assert second.executed == 0
        assert paper.account(session, 1).cash < 100000


def test_automation_rule_validation_and_isolation(client, make_user):
    alice = make_user("auto-a@ex.com"); bob = make_user("auto-b@ex.com")
    bad = client.post("/api/automations", headers=alice, json={"symbol": "AAPL", "trigger": "DCA", "quantity": 1, "threshold": 1})
    assert bad.status_code == 422
    created = client.post("/api/automations", headers=alice, json={"symbol": "AAPL", "trigger": "DCA", "quantity": 1})
    assert created.status_code == 201
    assert client.get("/api/automations", headers=bob).json() == []


@pytest.mark.asyncio
async def test_automation_matches_dca_price_rsi_and_continues_after_one_failure():
    class _FailingPaper:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("isolated fill failure")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        market = _Market()
        matching = AutomationService(market, PaperTradingService(market))
        dca = matching.create(session, 1, AutomationRuleCreate(symbol="AAPL", trigger="DCA", quantity=1))
        price = matching.create(session, 1, AutomationRuleCreate(
            symbol="MSFT", trigger="PRICE_BELOW", quantity=1, threshold=95
        ))
        rsi = matching.create(session, 1, AutomationRuleCreate(
            symbol="NVDA", trigger="RSI_BELOW", quantity=1, threshold=10
        ))
        assert await matching._matches(dca) is True
        assert await matching._matches(price) is True
        assert await matching._matches(rsi) is False
        failed = AutomationService(market, _FailingPaper())
        result = await failed.run(session)
        assert result.executed == 0 and result.skipped == 3
        with pytest.raises(NotFoundError):
            matching.delete(session, 2, dca.id)
        matching.delete(session, 1, dca.id)
        assert all(rule.id != dca.id for rule in matching.list(session, 1))
