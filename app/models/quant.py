"""Quantitative portfolio analytics: correlation, optimization, and simulation."""
from pydantic import BaseModel, Field


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


class MonteCarloRequest(BaseModel):
    """Bounded historical-bootstrap simulation settings.

    ``target_return_pct`` is the total return target for the selected horizon,
    rather than an annualized target.
    """

    horizon_days: int = Field(default=252, ge=5, le=1260)
    simulations: int = Field(default=10_000, ge=500, le=20_000)
    target_return_pct: float | None = Field(default=None, ge=-100, le=10_000)


class MonteCarloReport(BaseModel):
    """Distribution of simulated portfolio values using correlated daily returns."""

    method: str
    symbols: list[str]
    horizon_days: int
    simulations: int
    starting_value: float
    median_terminal_value: float
    percentile_05_value: float
    percentile_95_value: float
    probability_of_loss_pct: float
    value_at_risk_95_pct: float
    expected_shortfall_95_pct: float
    target_return_pct: float | None
    probability_of_target_pct: float | None
