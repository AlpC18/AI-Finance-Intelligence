from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from app.models.goals import PortfolioGoalUpdate
from app.models.rebalance import AllocationTarget, RebalanceRequest
from app.models.risk import EquitySnapshot
from app.models.transaction import Transaction
from app.services.goal_service import GoalService
from app.services.performance_service import PerformanceService
from app.services.portfolio_service import PortfolioService
from app.services.rebalance_service import RebalanceService
from app.core.errors import AppError, NotFoundError


class _Market:
    async def get_history(self, symbol, period="6mo"):
        return pd.DataFrame({"Close": np.array([100, 100])})


@pytest.mark.asyncio
async def test_goal_progress_and_rebalance_plan_are_evidence_based():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Transaction(user_id=1, symbol="AAPL", action="BUY", quantity=10, price=90))
        today = datetime.now(timezone.utc).date()
        session.add(EquitySnapshot(user_id=1, snapshot_date=(today - timedelta(days=10)).isoformat(), opening_equity=900))
        session.add(EquitySnapshot(user_id=1, snapshot_date=today.isoformat(), opening_equity=1000))
        session.commit()
        portfolio = PortfolioService(_Market(), None)
        goals = GoalService(PerformanceService(portfolio))
        goals.set(session, 1, PortfolioGoalUpdate(target_annual_return_pct=10, max_drawdown_pct=20))
        progress = await goals.progress(session, 1)
        assert progress.status in {"on_track", "behind", "breached"}
        plan = await RebalanceService(_Market(), portfolio).plan(session, 1, RebalanceRequest(
            targets=[AllocationTarget(symbol="AAPL", target_weight_pct=50), AllocationTarget(symbol="MSFT", target_weight_pct=50)]
        ))
        assert any(action.symbol == "MSFT" and action.side == "buy" for action in plan.actions)


class _Performance:
    def __init__(self, report):
        self.report_value = report

    async def report(self, session, user_id):
        return self.report_value


@pytest.mark.asyncio
async def test_goals_update_and_all_progress_states():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        perf = _Performance(SimpleNamespace(
            span_days=1, start_equity=Decimal("100"), return_pct=Decimal("0"),
            max_drawdown_pct=Decimal("0"),
        ))
        goals = GoalService(perf)
        first = goals.set(session, 1, PortfolioGoalUpdate(target_annual_return_pct=10, max_drawdown_pct=20))
        second = goals.set(session, 1, PortfolioGoalUpdate(target_annual_return_pct=20, max_drawdown_pct=10))
        assert first.id == second.id
        assert (await goals.progress(session, 1)).status == "insufficient_history"

        perf.report_value = SimpleNamespace(
            span_days=10, start_equity=Decimal("100"), return_pct=Decimal("10"),
            max_drawdown_pct=Decimal("11"),
        )
        assert (await goals.progress(session, 1)).status == "breached"
        perf.report_value.max_drawdown_pct = Decimal("1")
        assert (await goals.progress(session, 1)).status == "on_track"
        perf.report_value.return_pct = Decimal("0")
        assert (await goals.progress(session, 1)).status == "behind"
        with pytest.raises(NotFoundError):
            await goals.progress(session, 2)


@pytest.mark.asyncio
async def test_rebalance_rejects_empty_book_and_omits_zero_deltas():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        portfolio = PortfolioService(_Market(), None)
        service = RebalanceService(_Market(), portfolio)
        target = RebalanceRequest(targets=[AllocationTarget(symbol="AAPL", target_weight_pct=100)])
        with pytest.raises(AppError):
            await service.plan(session, 1, target)
        session.add(Transaction(user_id=1, symbol="AAPL", action="BUY", quantity=10, price=100))
        session.commit()
        assert (await service.plan(session, 1, target)).actions == []
