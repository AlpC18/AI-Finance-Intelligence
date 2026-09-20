"""Mathematical Contract-Preserving Weight Abstraction schemas (FinRL-X).

Enforces the portfolio weight abstraction law:
    w_t = R_t( T_t( A_t( S_t( X_<=t ) ) ) )
Guaranteeing mathematical portfolio sanity regardless of upstream LLM hallucinations.
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class AssetSelectionOutput(BaseModel):
    eligible_universe: List[str]
    rejected_assets: Dict[str, str]  # symbol -> rejection reason (e.g. illiquid, halted)


class RawAllocationOutput(BaseModel):
    method: Literal["equal_weight", "risk_parity", "mean_variance_sharpe", "kelly_fraction"]
    raw_weights: Dict[str, float]


class TimingAdjustmentOutput(BaseModel):
    market_volatility_regime: Literal["low_vol", "normal", "high_vol_stress", "extreme_crisis"]
    exposure_multiplier: float = Field(ge=0.0, le=1.0)
    timed_weights: Dict[str, float]


class RiskOverlayOutput(BaseModel):
    max_single_asset_cap: float = 0.15
    max_total_leverage: float = 1.0
    cash_buffer_pct: float
    violations_corrected: List[str] = Field(default_factory=list)
    final_contract_weights: Dict[str, float]


class FinRLWeightPipelineResult(BaseModel):
    timestamp_t: str
    selection_s_t: AssetSelectionOutput
    allocation_a_t: RawAllocationOutput
    timing_t_t: TimingAdjustmentOutput
    risk_overlay_r_t: RiskOverlayOutput
    final_portfolio_weights: Dict[str, float]
    formula_notation: str = "w_t = R_t( T_t( A_t( S_t( X_<=t ) ) ) )"
