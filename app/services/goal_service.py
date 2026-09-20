"""Goal persistence and evidence-based progress behind one small interface."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Session, select

from app.core.errors import NotFoundError
from app.core.money import HUNDRED, ZERO
from app.models.goals import PortfolioGoal, PortfolioGoalProgress, PortfolioGoalUpdate


class GoalService:
    def __init__(self, performance) -> None:
        self._performance = performance

    def set(self, session: Session, user_id: int, data: PortfolioGoalUpdate) -> PortfolioGoal:
        goal = session.exec(select(PortfolioGoal).where(PortfolioGoal.user_id == user_id)).first()
        if goal is None:
            goal = PortfolioGoal(user_id=user_id, **data.model_dump())
        else:
            goal.target_annual_return_pct = data.target_annual_return_pct
            goal.max_drawdown_pct = data.max_drawdown_pct
            goal.updated_at = datetime.now(timezone.utc)
        session.add(goal); session.commit(); session.refresh(goal)
        return goal

    async def progress(self, session: Session, user_id: int) -> PortfolioGoalProgress:
        goal = session.exec(select(PortfolioGoal).where(PortfolioGoal.user_id == user_id)).first()
        if goal is None:
            raise NotFoundError("Portfoy hedefi bulunamadi.")
        report = await self._performance.report(session, user_id)
        if report.span_days < 2 or not report.start_equity:
            observed = ZERO; status = "insufficient_history"
            note = "Yillik performans icin en az iki gunluk equity gecmisi gerekli."
        else:
            growth = Decimal("1") + report.return_pct / HUNDRED
            observed = (growth ** (Decimal("365") / report.span_days) - Decimal("1")) * HUNDRED if growth > ZERO else Decimal("-100")
            status = "breached" if report.max_drawdown_pct > goal.max_drawdown_pct else (
                "on_track" if observed >= goal.target_annual_return_pct else "behind"
            )
            note = "Gecmis performans hedefe ilerlemeyi gosterir; gelecek getiri tahmini degildir."
        return PortfolioGoalProgress(
            target_annual_return_pct=goal.target_annual_return_pct, max_drawdown_pct=goal.max_drawdown_pct,
            observed_annualized_return_pct=round(observed, 2), observed_max_drawdown_pct=report.max_drawdown_pct,
            status=status, note=note,
        )
