"""Backtesting request, structured report, and the persisted run history."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field as PField, model_validator
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    # Set when the run was persisted; absent on a report that was never stored.
    # Optional so the field is additive for anything already reading this model.
    id: Optional[int] = None
    created_at: Optional[datetime] = None


class WalkForwardRequest(BacktestRequest):
    """Repeated out-of-sample checks for a fixed strategy configuration."""

    folds: int = PField(default=3, ge=1, le=12)
    test_bars: int = PField(default=30, ge=10, le=1000)


class WalkForwardFold(BaseModel):
    fold: int
    train: BacktestMetrics
    test: BacktestMetrics


class WalkForwardReport(BaseModel):
    symbol: str
    folds: list[WalkForwardFold]
    average_train_return_pct: float
    average_test_return_pct: float
    performance_decay_pct: float
    overfitting_risk: Literal["low", "moderate", "high"]


class BacktestRun(SQLModel, table=True):
    """One saved simulation.

    Immutable once written - a run is a record of what a strategy did on the
    bars available at that moment, and re-running it later on newer bars is a
    DIFFERENT run, not an update of this one. Nothing here is ever mutated,
    which is what makes the denormalisation below safe.

    The headline metrics are stored as columns AND inside ``report_json``. The
    duplication is deliberate: listing and ranking history must not require
    parsing a JSON blob per row, while the curve and the trade list are only
    ever wanted whole, for one run at a time.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, nullable=False)
    symbol: str = Field(index=True)
    strategy: str = ""

    # The request, kept so a run stays reproducible after the fact. Without
    # these a stored result is an unattributable number.
    period: str = "1y"
    initial_capital: float = 10_000.0
    rsi_buy: float = 30.0
    rsi_sell: float = 70.0
    warmup: int = 20

    bars: int = 0
    trades: int = 0
    hit_rate_pct: float = 0.0
    profit_factor: Optional[float] = None  # None == undefined (no losing trades)
    expectancy: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    total_return_pct: float = 0.0
    final_equity: float = 0.0

    report_json: str = ""  # the full BacktestReport snapshot
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class BacktestRunSummary(BaseModel):
    """A history row: enough to rank and choose, without the curve."""

    id: int
    symbol: str
    strategy: str
    period: str
    initial_capital: float
    rsi_buy: float
    rsi_sell: float
    bars: int
    metrics: BacktestMetrics
    created_at: datetime


class BacktestRunPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[BacktestRunSummary] = []


class BacktestComparison(BaseModel):
    """Several runs side by side, plus which one leads on each metric.

    ``best`` maps a metric name to the winning run id. A metric no run can be
    ranked on is simply absent rather than pointing at an arbitrary run.
    """

    runs: list[BacktestRunSummary] = []
    best: dict[str, int] = {}
