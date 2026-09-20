"""Portfolio read schemas — all values are derived from the transaction ledger."""
from typing import Optional

from pydantic import BaseModel, Field as PField
from app.core.money import Money


class PositionRisk(BaseModel):
    symbol: str
    quantity: Money
    avg_cost: Money
    current_price: Money
    market_value: Money
    realized_pnl: Money
    unrealized_pnl: Money
    pnl_pct: Money
    weight_pct: Money
    volatility_annual: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    var_95_pct: Optional[float] = None


class RiskReport(BaseModel):
    total_value: Money
    total_realized_pnl: Money
    total_unrealized_pnl: Money
    positions: list[PositionRisk]


class AdviceOut(BaseModel):
    model_config = {"extra": "forbid"}
    narrative: str = PField(min_length=1)
    suggestions: list[str] = []
    citations: list[str] = []  # headlines/sources grounding the advice


class Advice(BaseModel):
    narrative: str = ""
    suggestions: list[str] = []
    citations: list[str] = []
    degraded: bool = False
    disclaimer: str = "Bu bir yatirim tavsiyesi degildir."
