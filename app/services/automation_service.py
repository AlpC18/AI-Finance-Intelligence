"""One deep module for paper-only rule persistence and guarded execution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.core.errors import NotFoundError
from app.core.money import to_decimal
from app.models.automation import (
    AutomationRuleCreate, AutomationRunResult, PaperAutomationRule,
)
from app.models.paper import PaperOrderRequest


class AutomationService:
    def __init__(self, market, paper) -> None:
        self._market = market
        self._paper = paper

    def create(self, session: Session, user_id: int, data: AutomationRuleCreate) -> PaperAutomationRule:
        rule = PaperAutomationRule(user_id=user_id, symbol=data.symbol.upper().strip(),
                                   trigger=data.trigger, quantity=data.quantity,
                                   threshold=data.threshold, cooldown_minutes=data.cooldown_minutes)
        session.add(rule); session.commit(); session.refresh(rule)
        return rule

    def list(self, session: Session, user_id: int) -> list[PaperAutomationRule]:
        return list(session.exec(select(PaperAutomationRule).where(
            PaperAutomationRule.user_id == user_id
        ).order_by(PaperAutomationRule.created_at.desc())).all())

    def delete(self, session: Session, user_id: int, rule_id: int) -> None:
        rule = session.get(PaperAutomationRule, rule_id)
        if rule is None or rule.user_id != user_id:
            raise NotFoundError("Otomasyon kurali bulunamadi.")
        session.delete(rule); session.commit()

    async def run(self, session: Session) -> AutomationRunResult:
        evaluated = executed = skipped = 0
        now = datetime.now(timezone.utc)
        rules = list(session.exec(select(PaperAutomationRule).where(
            PaperAutomationRule.enabled == True  # noqa: E712
        )).all())
        for rule in rules:
            if rule.last_triggered_at and _utc(rule.last_triggered_at) > now - timedelta(minutes=rule.cooldown_minutes):
                skipped += 1; continue
            evaluated += 1
            try:
                if not await self._matches(rule):
                    skipped += 1; continue
                await self._paper.execute(session, rule.user_id, PaperOrderRequest(
                    symbol=rule.symbol, side="buy", quantity=rule.quantity,
                ))
                rule.last_triggered_at = now
                session.add(rule); session.commit()
                executed += 1
            except Exception:  # An individual rule must never block other accounts.
                skipped += 1
        return AutomationRunResult(evaluated=evaluated, executed=executed, skipped=skipped)

    async def _matches(self, rule: PaperAutomationRule) -> bool:
        if rule.trigger == "DCA":
            return True
        data = await self._market.get_market_data(rule.symbol)
        if rule.trigger == "PRICE_BELOW":
            return to_decimal(data.quote.price) < rule.threshold
        return data.indicators.rsi_14 is not None and to_decimal(data.indicators.rsi_14) < rule.threshold


def _utc(value: datetime) -> datetime:
    """SQLite returns naive timestamps; normalise them at the comparison seam."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
