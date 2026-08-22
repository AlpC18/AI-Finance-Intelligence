"""Portfolio kill-switch: daily equity snapshot + drawdown-based trading halt."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.core.errors import AppError
from app.models.risk import (
    EquitySnapshot,
    KillSwitchStatus,
    RiskConfigRead,
    RiskConfigUpdate,
    RiskSetting,
)
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
            daily_loss_limit_pct=setting.daily_loss_limit_pct if setting else 0.0
        )

    def set_config(
        self, session: Session, user_id: int, data: RiskConfigUpdate
    ) -> RiskConfigRead:
        setting = self._find(session, user_id) or RiskSetting(user_id=user_id)
        setting.daily_loss_limit_pct = data.daily_loss_limit_pct
        setting.updated_at = datetime.now(timezone.utc)
        session.add(setting)
        session.commit()
        session.refresh(setting)
        return RiskConfigRead(daily_loss_limit_pct=setting.daily_loss_limit_pct)

    def _find(self, session: Session, user_id: int) -> Optional[RiskSetting]:
        return session.exec(
            select(RiskSetting).where(RiskSetting.user_id == user_id)
        ).first()

    # --- equity + snapshot ---
    async def current_equity(self, session: Session, user_id: int) -> float:
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
            session.commit()
            session.refresh(snap)
        return snap

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
        limit = setting.daily_loss_limit_pct if setting else 0.0
        manual = setting.manual_halt if setting else False
        snap = await self.ensure_daily_snapshot(session, user_id)
        current = await self.current_equity(session, user_id)
        opening = snap.opening_equity
        drawdown = ((current - opening) / opening * 100.0) if opening else 0.0
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
