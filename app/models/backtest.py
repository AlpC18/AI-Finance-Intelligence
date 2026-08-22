"""Backtesting request + structured report (metrics summary + equity curve)."""
from typing import Optional

from pydantic import BaseModel, Field as PField, model_validator


class BacktestRequest(BaseModel):
    symbol: str = PField(min_length=1, max_length=20)
    period: str = "1y"  # provider range (e.g. 1mo | 6mo | 1y | 5d)
    initial_capital: float = PField(default=10_000.0, gt=0)
    rsi_buy: float = PField(default=30.0, ge=1.0, le=99.0)
    rsi_sell: float = PField(default=70.0, ge=1.0, le=99.0)
    warmup: int = PField(default=20, ge=1, le=200)

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> "BacktestRequest":
        if self.rsi_buy >= self.rsi_sell:
            raise ValueError("rsi_buy must be strictly less than rsi_sell")
        return self


class EquityPoint(BaseModel):
    index: int
    equity: float


class BacktestTrade(BaseModel):
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    exit_index: int


class BacktestMetrics(BaseModel):
    trades: int
    hit_rate_pct: float
    profit_factor: Optional[float] = None  # None == undefined (no losing trades)
    expectancy: float
    max_drawdown_pct: float
    sharpe_ratio: float
    total_return_pct: float
    final_equity: float


class BacktestReport(BaseModel):
    symbol: str
    bars: int
    strategy: str
    metrics: BacktestMetrics
    equity_curve: list[EquityPoint]
    trades: list[BacktestTrade]
