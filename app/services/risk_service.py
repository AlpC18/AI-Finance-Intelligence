"""Portfolio kill-switch: daily equity snapshot + drawdown-based trading halt."""
from __future__ import annotations

from decimal import Decimal

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from app.core.money import HUNDRED, ZERO, to_decimal
from app.core.errors import AppError
from app.models.risk import (
    EquitySnapshot,
    KillSwitchStatus,
    RiskConfigRead,
    RiskConfigUpdate,
    RiskSetting,
)
from app.models.order import TradeOrder
from app.services.portfolio_service import PortfolioService


class RiskService:
    """Computes daily drawdown from an opening-equity snapshot and halts trading.

    Equity is proxied as (open-position market value + cumulative realized P&L),
    fully derived from the ledger + market prices, so it needs no external state.
    """

    def __init__(self, portfolio: PortfolioService) -> None:
        self._portfolio = portfolio

    # --- config ---
    def get_config(self, session: Session, user_id: int) -> RiskConfigRead:
        setting = self._find(session, user_id)
        return RiskConfigRead(
            daily_loss_limit_pct=setting.daily_loss_limit_pct if setting else ZERO,
            max_daily_trades=setting.max_daily_trades if setting else 0,
            max_position_weight_pct=setting.max_position_weight_pct if setting else ZERO,
            require_protective_stop=setting.require_protective_stop if setting else False,
        )

    def set_config(
        self, session: Session, user_id: int, data: RiskConfigUpdate
    ) -> RiskConfigRead:
        setting = self._find(session, user_id) or RiskSetting(user_id=user_id)
        setting.daily_loss_limit_pct = data.daily_loss_limit_pct
        setting.max_daily_trades = data.max_daily_trades
        setting.max_position_weight_pct = data.max_position_weight_pct
        setting.require_protective_stop = data.require_protective_stop
        setting.updated_at = datetime.now(timezone.utc)
        session.add(setting)
        session.commit()
        session.refresh(setting)
        return RiskConfigRead(
            daily_loss_limit_pct=setting.daily_loss_limit_pct,
            max_daily_trades=setting.max_daily_trades,
            max_position_weight_pct=setting.max_position_weight_pct,
            require_protective_stop=setting.require_protective_stop,
        )

    async def assert_order_allowed(
        self, session: Session, user_id: int, *, symbol: str, side: str,
        quantity: Decimal, price: Decimal, has_protective_stop: bool,
    ) -> None:
        """Apply account-level limits at one deep execution seam.

        All new orders pass through this interface, so changing a limit never
        requires duplicating checks in HTTP routes, broker adapters, or future
        paper-trading adapters.
        """
        setting = self._find(session, user_id)
        if setting is None:
            return
        if setting.require_protective_stop and not has_protective_stop:
            raise AppError("Bu hesap koruyucu stop gerektiriyor.", status_code=422, reason="protective_stop")
        if setting.max_daily_trades:
            start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            count = session.exec(
                select(func.count()).select_from(TradeOrder).where(
                    TradeOrder.user_id == user_id, TradeOrder.created_at >= start
                )
            ).one()
            if count >= setting.max_daily_trades:
                raise AppError("Gunluk emir limiti doldu.", status_code=422, reason="daily_trade_limit")
        if side != "buy" or not setting.max_position_weight_pct:
            return
        report = await self._portfolio.risk_report(session, user_id)
        current = next((p.market_value for p in report.positions if p.symbol == symbol.upper()), ZERO)
        proposed_position = current + quantity * price
        proposed_total = report.total_value + quantity * price
        weight = proposed_position / proposed_total * HUNDRED if proposed_total else ZERO
        if weight > setting.max_position_weight_pct:
            raise AppError(
                f"{symbol.upper()} agirligi %{weight:.2f}; hesap limiti %{setting.max_position_weight_pct:.2f}.",
                status_code=422, reason="position_concentration",
            )

    def _find(self, session: Session, user_id: int) -> Optional[RiskSetting]:
        return session.exec(
            select(RiskSetting).where(RiskSetting.user_id == user_id)
        ).first()

    # --- equity + snapshot ---
    async def current_equity(self, session: Session, user_id: int) -> Decimal:
        report = await self._portfolio.risk_report(session, user_id)
        return round(report.total_value + report.total_realized_pnl, 2)

    async def ensure_daily_snapshot(
        self, session: Session, user_id: int
    ) -> EquitySnapshot:
        today = datetime.now(timezone.utc).date().isoformat()
        snap = session.exec(
            select(EquitySnapshot).where(
                EquitySnapshot.user_id == user_id,
                EquitySnapshot.snapshot_date == today,
            )
        ).first()
        if snap is None:
            snap = EquitySnapshot(
                user_id=user_id,
                snapshot_date=today,
                opening_equity=await self.current_equity(session, user_id),
            )
            session.add(snap)
            try:
                session.commit()
                session.refresh(snap)
            except IntegrityError:
                # A concurrent request created today's canonical opening value
                # first.  Roll back our failed insert and use that row; never
                # let two racing requests choose different bases for the
                # drawdown halt.
                session.rollback()
                snap = session.exec(
                    select(EquitySnapshot).where(
                        EquitySnapshot.user_id == user_id,
                        EquitySnapshot.snapshot_date == today,
                    )
                ).one()
        return snap

    # --- automatic (drawdown) halt ---
    def users_with_automatic_limits(self, session: Session) -> list[int]:
        """Users whose daily-loss limit is actually armed.

        A limit of 0 means the automatic halt is off, and sweeping accounts that
        cannot trip is the difference between a cheap periodic check and one
        that walks every user on the platform.
        """
        rows = session.exec(
            select(RiskSetting.user_id).where(RiskSetting.daily_loss_limit_pct > 0)
        ).all()
        return list(rows)

    def already_tripped_today(self, session: Session, user_id: int) -> bool:
        setting = self._find(session, user_id)
        return bool(setting and setting.auto_halt_tripped_on == _today())

    def mark_auto_halt_tripped(self, session: Session, user_id: int) -> None:
        """Record that the automatic halt fired today, so the sweep acts ONCE.

        Deliberately not written as ``manual_halt``: that field carries operator
        intent, and a resume would then be indistinguishable from clearing an
        automatic trip. The drawdown condition re-evaluates itself from equity
        every sweep and needs no latch to stay halted - this only stops the
        FLATTEN from repeating every interval.
        """
        setting = self._find(session, user_id) or RiskSetting(user_id=user_id)
        setting.auto_halt_tripped_on = _today()
        setting.updated_at = datetime.now(timezone.utc)
        session.add(setting)
        session.commit()

    # --- kill-switch ---
    def set_manual_halt(self, session: Session, user_id: int, enabled: bool) -> None:
        """Operator kill-switch toggle — force-halt or resume automated trading."""
        setting = self._find(session, user_id) or RiskSetting(user_id=user_id)
        setting.manual_halt = enabled
        setting.updated_at = datetime.now(timezone.utc)
        session.add(setting)
        session.commit()

    async def status(self, session: Session, user_id: int) -> KillSwitchStatus:
        setting = self._find(session, user_id)
        limit = setting.daily_loss_limit_pct if setting else ZERO
        manual = setting.manual_halt if setting else False
        snap = await self.ensure_daily_snapshot(session, user_id)
        current = await self.current_equity(session, user_id)
        opening = snap.opening_equity
        # Exact: this is the comparison that decides whether an account is
        # halted and its working orders flattened.
        drawdown = ((current - opening) / opening * HUNDRED) if opening else ZERO
        dd_halt = limit > 0 and drawdown <= -limit
        halted = manual or dd_halt
        reason = "manual" if manual else ("daily_loss_limit" if dd_halt else "")
        return KillSwitchStatus(
            halted=halted,
            manual_halt=manual,
            reason=reason,
            daily_loss_limit_pct=limit,
            opening_equity=round(opening, 2),
            current_equity=round(current, 2),
            drawdown_pct=round(drawdown, 2),
        )

    async def assert_not_halted(self, session: Session, user_id: int) -> None:
        st = await self.status(session, user_id)
        if st.halted:
            detail = (
                "Manuel durdurma aktif."
                if st.manual_halt
                else f"gunluk zarar %{abs(st.drawdown_pct):.2f} >= limit "
                f"%{st.daily_loss_limit_pct:.2f}."
            )
            raise AppError(
                f"Kill-switch aktif: {detail} Otomatik islem durduruldu.",
                status_code=423,
                reason="risk_halt",
            )


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()
