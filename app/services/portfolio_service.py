"""Portfolio service: holdings & P&L reconstructed from the transaction ledger."""
import numpy as np
from sqlmodel import Session, select

from app.core.errors import AppError
from app.models.portfolio import Advice, PositionRisk, RiskReport
from app.models.transaction import Holding, Transaction, TransactionCreate
from app.providers.base import MarketDataProvider
from app.services.ai_service import AIService


class PortfolioService:
    def __init__(self, provider: MarketDataProvider, ai: AIService) -> None:
        self._provider = provider
        self._ai = ai

    # --- Ledger writes ---
    def record_transaction(
        self, session: Session, data: TransactionCreate, user_id: int
    ) -> Transaction:
        symbol = data.symbol.upper().strip()
        if data.action == "SELL":
            held = self._current_quantity(session, user_id, symbol)
            if data.quantity > held + 1e-9:
                raise AppError(
                    f"Yetersiz bakiye: {symbol} icin {held} adet var, "
                    f"{data.quantity} satilamaz.",
                    status_code=400,
                )
        tx = Transaction(
            user_id=user_id,
            symbol=symbol,
            action=data.action,
            quantity=data.quantity,
            price=data.price,
        )
        session.add(tx)
        session.commit()
        session.refresh(tx)
        return tx

    def list_transactions(self, session: Session, user_id: int) -> list[Transaction]:
        stmt = (
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.timestamp)
        )
        return list(session.exec(stmt).all())

    # --- Reconstruction (single source of truth) ---
    def reconstruct_holdings(self, session: Session, user_id: int) -> list[Holding]:
        state: dict[str, dict[str, float]] = {}
        for tx in self.list_transactions(session, user_id):
            _apply_transaction(state, tx.symbol, tx.action, tx.quantity, tx.price)
        return [
            Holding(
                symbol=sym,
                quantity=round(s["qty"], 8),
                avg_cost=round(s["avg"], 6),
                realized_pnl=round(s["realized"], 2),
            )
            for sym, s in state.items()
        ]

    def active_holdings(self, session: Session, user_id: int) -> list[Holding]:
        return [h for h in self.reconstruct_holdings(session, user_id) if h.quantity > 0]

    def _current_quantity(self, session: Session, user_id: int, symbol: str) -> float:
        for h in self.reconstruct_holdings(session, user_id):
            if h.symbol == symbol:
                return h.quantity
        return 0.0

    # --- Risk (quant, instant, ledger-derived) ---
    async def risk_report(self, session: Session, user_id: int) -> RiskReport:
        return (await self._build_report(session, user_id))[0]

    async def risk_advice(self, session: Session, user_id: int) -> Advice:
        report, payload = await self._build_report(session, user_id)
        symbols = [p.symbol for p in report.positions]
        out = await self._ai.portfolio_advice(payload, symbols=symbols)
        if out is None:
            return Advice(
                narrative="AI devre disi/gecici hata - yalnizca sayisal risk metrikleri.",
                degraded=True,
            )
        return Advice(
            narrative=out.narrative,
            suggestions=out.suggestions,
            citations=out.citations,
        )

    async def _build_report(
        self, session: Session, user_id: int
    ) -> tuple[RiskReport, dict]:
        holdings = self.reconstruct_holdings(session, user_id)
        total_realized = round(sum(h.realized_pnl for h in holdings), 2)
        risks: list[PositionRisk] = []
        total_value = 0.0
        total_unrealized = 0.0
        for h in holdings:
            if h.quantity <= 0:
                continue
            hist = await self._provider.get_history(h.symbol, period="6mo")
            close = hist["Close"]
            price = float(close.iloc[-1])
            market_value = price * h.quantity
            unrealized = (price - h.avg_cost) * h.quantity
            total_value += market_value
            total_unrealized += unrealized
            pnl_pct = (
                (price - h.avg_cost) / h.avg_cost * 100 if h.avg_cost else 0.0
            )
            risks.append(
                PositionRisk(
                    symbol=h.symbol,
                    quantity=h.quantity,
                    avg_cost=h.avg_cost,
                    current_price=round(price, 4),
                    market_value=round(market_value, 2),
                    realized_pnl=h.realized_pnl,
                    unrealized_pnl=round(unrealized, 2),
                    pnl_pct=round(pnl_pct, 2),
                    weight_pct=0.0,
                    volatility_annual=_annual_vol(close),
                    max_drawdown_pct=_max_drawdown_pct(close),
                    var_95_pct=_var_95_pct(close),
                )
            )
        for r in risks:
            r.weight_pct = round(
                (r.market_value / total_value * 100) if total_value else 0.0, 2
            )
        report = RiskReport(
            total_value=round(total_value, 2),
            total_realized_pnl=total_realized,
            total_unrealized_pnl=round(total_unrealized, 2),
            positions=risks,
        )
        return report, report.model_dump()


def _apply_transaction(
    state: dict[str, dict[str, float]],
    symbol: str,
    action: str,
    quantity: float,
    price: float,
) -> None:
    """Average-cost accounting. Mutates `state` in place for one transaction."""
    s = state.setdefault(symbol, {"qty": 0.0, "avg": 0.0, "realized": 0.0})
    if action == "BUY":
        total_cost = s["qty"] * s["avg"] + quantity * price
        s["qty"] += quantity
        s["avg"] = total_cost / s["qty"] if s["qty"] else 0.0
    else:  # SELL
        sell_qty = min(quantity, s["qty"])  # clamp defensively against oversell
        s["realized"] += (price - s["avg"]) * sell_qty
        s["qty"] -= sell_qty
        if s["qty"] <= 1e-9:
            s["qty"] = 0.0
            s["avg"] = 0.0


def _returns(close) -> np.ndarray:
    return close.pct_change().dropna().to_numpy()


def _annual_vol(close) -> float | None:
    r = _returns(close)
    return round(float(np.std(r) * np.sqrt(252)), 4) if r.size >= 2 else None


def _max_drawdown_pct(close) -> float | None:
    v = close.to_numpy()
    if v.size < 2:
        return None
    peak = np.maximum.accumulate(v)
    return round(float(((v - peak) / peak).min() * 100), 2)


def _var_95_pct(close) -> float | None:
    r = _returns(close)
    return round(float(np.percentile(r, 5) * 100), 2) if r.size >= 20 else None
