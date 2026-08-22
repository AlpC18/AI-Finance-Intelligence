"""Market schemas: raw quant (instant) vs strict LLM-output (validated)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

SignalAction = Literal["BUY", "SELL", "HOLD"]


class Quote(BaseModel):
    symbol: str
    price: float
    currency: str = "USD"
    change_pct: Optional[float] = None


class Indicators(BaseModel):
    rsi_14: Optional[float] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    ema_12: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    volatility_annual: Optional[float] = None


class Fundamentals(BaseModel):
    """Fundamental valuation metrics (best-effort; any field may be None)."""

    pe_ratio: Optional[float] = None          # trailing P/E
    ev_ebitda: Optional[float] = None         # enterprise value / EBITDA
    debt_to_equity: Optional[float] = None
    short_ratio: Optional[float] = None
    earnings_date: Optional[str] = None       # next earnings date (ISO), if known


class MarketData(BaseModel):
    """Returned instantly by /api/market/{symbol} — no AI in the hot path."""

    quote: Quote
    indicators: Indicators
    fundamentals: Optional[Fundamentals] = None


class SignalOut(BaseModel):
    """STRICT validation target for Claude output. Rejects malformed/extra keys."""

    model_config = {"extra": "forbid"}
    action: SignalAction
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    citations: list[str] = []  # headlines/sources the model grounded its view on


class Signal(BaseModel):
    action: SignalAction = "HOLD"
    confidence: float = 0.0
    rationale: str = ""
    citations: list[str] = []
    degraded: bool = False
    disclaimer: str = "Bu bir yatirim tavsiyesi degildir."
