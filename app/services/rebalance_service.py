"""Compute suggested target trades without any broker-side execution."""
from decimal import Decimal

from sqlmodel import Session

from app.core.errors import AppError
from app.core.money import HUNDRED, ZERO, to_decimal
from app.models.rebalance import RebalanceAction, RebalancePlan, RebalanceRequest


class RebalanceService:
    def __init__(self, provider, portfolio) -> None:
        self._provider = provider
        self._portfolio = portfolio

    async def plan(self, session: Session, user_id: int, request: RebalanceRequest) -> RebalancePlan:
        holdings = {h.symbol: h for h in self._portfolio.active_holdings(session, user_id)}
        targets = {t.symbol.upper().strip(): t.target_weight_pct for t in request.targets}
        symbols = sorted(set(holdings) | set(targets))
        prices = {}
        for symbol in symbols:
            hist = await self._provider.get_history(symbol, period="5d")
            prices[symbol] = to_decimal(float(hist["Close"].iloc[-1]))
        values = {s: prices[s] * holdings[s].quantity if s in holdings else ZERO for s in symbols}
        total = sum(values.values(), ZERO)
        if total <= ZERO:
            raise AppError("Rebalance icin aktif pozisyon gerekli.", status_code=422)
        actions: list[RebalanceAction] = []
        for symbol in symbols:
            current_weight = values[symbol] / total * HUNDRED
            target_weight = targets.get(symbol, ZERO)
            delta = total * target_weight / HUNDRED - values[symbol]
            if not delta:
                continue
            actions.append(RebalanceAction(
                symbol=symbol, side="buy" if delta > ZERO else "sell",
                quantity=round(abs(delta) / prices[symbol], 8), reference_price=round(prices[symbol], 8),
                current_weight_pct=round(current_weight, 2), target_weight_pct=target_weight,
                notional_delta=round(abs(delta), 2),
            ))
        return RebalancePlan(total_value=round(total, 2), actions=actions)
