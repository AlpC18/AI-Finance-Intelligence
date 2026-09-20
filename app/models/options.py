"""Options and Derivatives pricing, Greeks, and Strategy Wizard schemas."""
from datetime import date
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

OptionType = Literal["call", "put"]
StrategyType = Literal[
    "single",
    "covered_call",
    "protective_put",
    "bull_call_spread",
    "bear_put_spread",
    "straddle",
    "strangle",
    "iron_condor",
    "custom",
]


class OptionGreeks(BaseModel):
    delta: float
    gamma: float
    theta: float  # 1-day theta decay in USD
    vega: float   # per 1% change in IV
    rho: float    # per 1% change in interest rate
    implied_volatility: float


class OptionContract(BaseModel):
    contract_symbol: str
    underlying_symbol: str
    strike: float
    expiration: str  # YYYY-MM-DD
    option_type: OptionType
    bid: float
    ask: float
    last_price: float
    volume: int = 0
    open_interest: int = 0
    greeks: OptionGreeks


class OptionChain(BaseModel):
    underlying_symbol: str
    underlying_price: float
    expirations: List[str]
    calls: List[OptionContract]
    puts: List[OptionContract]


class StrategyLeg(BaseModel):
    option_type: Optional[OptionType] = None  # None -> underlying stock leg
    is_stock_leg: bool = False
    action: Literal["buy", "sell"]
    strike: Optional[float] = None
    expiration: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    premium_or_price: float = Field(gt=0)


class StrategyPayoffPoint(BaseModel):
    underlying_price: float
    profit_loss: float
    roi_pct: float


class OptionsStrategyRequest(BaseModel):
    strategy_type: StrategyType
    underlying_symbol: str
    underlying_price: float = Field(gt=0)
    risk_free_rate: float = Field(default=0.045, ge=0.0)
    legs: List[StrategyLeg]


class OptionsStrategyPayoff(BaseModel):
    strategy_name: str
    underlying_symbol: str
    underlying_price: float
    net_debit_credit: float  # Positive = credit received, negative = debit paid
    max_profit: Optional[float] = None  # None means unlimited
    max_loss: Optional[float] = None    # None means unlimited
    breakeven_points: List[float]
    risk_reward_ratio: Optional[float] = None
    aggregate_greeks: OptionGreeks
    payoff_curve: List[StrategyPayoffPoint]
    summary_thesis: str
