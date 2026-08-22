"""Alert CRUD + evaluation against live market data / AI signals."""
from sqlmodel import Session, select

from app.core.errors import NotFoundError
from app.models.alert import Alert, AlertCreate
from app.services.market_service import MarketService

_STRONG_BUY_CONFIDENCE = 0.7


class AlertService:
    def __init__(self, market: MarketService) -> None:
        self._market = market

    # --- CRUD (scoped to owner) ---
    def create(self, session: Session, data: AlertCreate, user_id: int) -> Alert:
        alert = Alert(
            user_id=user_id,
            symbol=data.symbol.upper().strip(),
            condition_type=data.condition_type,
            threshold_value=data.threshold_value,
            is_active=data.is_active,
        )
        session.add(alert)
        session.commit()
        session.refresh(alert)
        return alert

    def list(self, session: Session, user_id: int) -> list[Alert]:
        stmt = select(Alert).where(Alert.user_id == user_id)
        return list(session.exec(stmt).all())

    def delete(self, session: Session, alert_id: int, user_id: int) -> None:
        alert = session.get(Alert, alert_id)
        if alert is None or alert.user_id != user_id:
            raise NotFoundError(f"Uyari bulunamadi: {alert_id}")
        session.delete(alert)
        session.commit()

    # --- Evaluation (used by the background worker) ---
    async def evaluate(self, alert: Alert) -> bool:
        ct = alert.condition_type
        thr = alert.threshold_value

        if ct == "AI_SIGNAL_STRONG_BUY":
            signal = await self._market.get_signal(alert.symbol)
            return (
                signal.action == "BUY"
                and not signal.degraded
                and signal.confidence >= _STRONG_BUY_CONFIDENCE
            )

        data = await self._market.get_market_data(alert.symbol)
        if ct == "PRICE_ABOVE":
            return thr is not None and data.quote.price > thr
        if ct == "PRICE_BELOW":
            return thr is not None and data.quote.price < thr
        if ct == "RSI_BELOW":
            rsi = data.indicators.rsi_14
            return thr is not None and rsi is not None and rsi < thr
        return False
