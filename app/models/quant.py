"""Quantitative portfolio analytics: correlation matrix + MPT optimization output."""
from pydantic import BaseModel


class CorrelationMatrix(BaseModel):
    """N x N Pearson correlation of daily returns across active holdings."""

    symbols: list[str]
    matrix: list[list[float]]


class OptimizedWeight(BaseModel):
    symbol: str
    current_weight: float
    target_weight: float


class OptimizationReport(BaseModel):
    """Max-Sharpe target allocation vs. the current book."""

    method: str
    symbols: list[str]
    weights: list[OptimizedWeight]
    expected_annual_return: float
    annual_volatility: float
    sharpe_ratio: float
    concentration_hhi: float  # Herfindahl index of target weights (lower = diversified)
    samples_evaluated: int
