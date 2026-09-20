"""Deep paper-execution module: fills, fees, slippage, cash and holdings."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Session, select

from app.core.errors import AppError
from app.core.money import HUNDRED, ZERO, to_decimal
from app.models.paper import (
    PaperAccount, PaperAccountCreate, PaperAccountRead, PaperFill, PaperFillRead, PaperOrderRequest,
)


class PaperTradingService:
    """A self-contained simulator whose interface never reaches a real broker."""

    def __init__(self, market, *, slippage_bps: Decimal = Decimal("5"), fee_bps: Decimal = Decimal("1")) -> None:
        self._market = market
        self._slippage_bps = slippage_bps
        self._fee_bps = fee_bps

    def account(self, session: Session, user_id: int) -> PaperAccountRead:
        account = self._account(session, user_id)
        fills = list(session.exec(
            select(PaperFill).where(PaperFill.user_id == user_id).order_by(PaperFill.created_at.desc()).limit(100)
        ).all())
        realized = _realized_pnl(reversed(fills))
        return PaperAccountRead(
            currency=account.currency, starting_cash=account.starting_cash, cash=account.cash,
            buying_power=account.cash, realized_pnl=round(realized, 2),
            fills=[PaperFillRead.model_validate(fill, from_attributes=True) for fill in fills],
        )

    def reset(self, session: Session, user_id: int, data: PaperAccountCreate) -> PaperAccountRead:
        account = self._account(session, user_id)
        account.starting_cash = data.starting_cash
        account.cash = data.starting_cash
        account.updated_at = datetime.now(timezone.utc)
        for fill in session.exec(select(PaperFill).where(PaperFill.user_id == user_id)).all():
            session.delete(fill)
        session.add(account)
        session.commit()
        return self.account(session, user_id)

    async def execute(self, session: Session, user_id: int, order: PaperOrderRequest) -> PaperAccountRead:
        account = self._account(session, user_id)
        quote = to_decimal((await self._market.get_market_data(order.symbol)).quote.price)
        direction = Decimal("1") if order.side == "buy" else Decimal("-1")
        fill_price = quote * (Decimal("1") + direction * self._slippage_bps / Decimal("10000"))
        notional = fill_price * order.quantity
        fee = notional * self._fee_bps / Decimal("10000")
        if order.side == "buy":
            if notional + fee > account.cash:
                raise AppError("Paper hesapta yeterli nakit yok.", status_code=422, reason="paper_buying_power")
            account.cash -= notional + fee
        else:
            if order.quantity > _quantity(session, user_id, order.symbol):
                raise AppError("Paper hesapta yeterli pozisyon yok.", status_code=422, reason="paper_oversell")
            account.cash += notional - fee
        session.add(PaperFill(
            user_id=user_id, symbol=order.symbol.upper().strip(), side=order.side,
            quantity=order.quantity, reference_price=quote, fill_price=fill_price,
            fee=fee, slippage_bps=self._slippage_bps,
        ))
        account.updated_at = datetime.now(timezone.utc)
        session.add(account)
        session.commit()
        return self.account(session, user_id)

    @staticmethod
    def _account(session: Session, user_id: int) -> PaperAccount:
        account = session.exec(select(PaperAccount).where(PaperAccount.user_id == user_id)).first()
        if account is None:
            account = PaperAccount(user_id=user_id)
            session.add(account)
            session.commit()
            session.refresh(account)
        return account


def _quantity(session: Session, user_id: int, symbol: str) -> Decimal:
    total = ZERO
    for fill in session.exec(select(PaperFill).where(PaperFill.user_id == user_id, PaperFill.symbol == symbol.upper().strip())).all():
        total += fill.quantity if fill.side == "buy" else -fill.quantity
    return total


def _realized_pnl(fills) -> Decimal:
    state: dict[str, dict[str, Decimal]] = {}
    for fill in fills:
        slot = state.setdefault(fill.symbol, {"quantity": ZERO, "cost": ZERO, "realized": ZERO})
        if fill.side == "buy":
            total_cost = slot["quantity"] * slot["cost"] + fill.quantity * fill.fill_price + fill.fee
            slot["quantity"] += fill.quantity
            slot["cost"] = total_cost / slot["quantity"] if slot["quantity"] else ZERO
        else:
            slot["realized"] += (fill.fill_price - slot["cost"]) * fill.quantity - fill.fee
            slot["quantity"] -= fill.quantity
            if not slot["quantity"]:
                slot["cost"] = ZERO
    return sum((slot["realized"] for slot in state.values()), ZERO)
