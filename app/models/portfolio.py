"""Portfolio read schemas — all values are derived from the transaction ledger."""
from typing import Optional

from pydantic import BaseModel, Field as PField


class PositionRisk(BaseModel):
    symbol: str
    quantity: float
    avg_cost: float
    current_price: float
    market_value: float
    realized_pnl: float
    unrealized_pnl: float
    pnl_pct: float
    weight_pct: float
    volatility_annual: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    var_95_pct: Optional[float] = None


class RiskReport(BaseModel):
    total_value: float
    total_realized_pnl: float
    total_unrealized_pnl: float
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
