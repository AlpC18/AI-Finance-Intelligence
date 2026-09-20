"""Equity Specific Catalysts & Stock Trajectory Intelligence schemas.

Analyzes company-specific news driving stock price surges, crashes, or sector contagion:
- Earnings & Guidance Upgrades/Downgrades
- FDA Drug Approvals, Phase 3 Trials, and Rejections
- M&A Takeovers, Buyout Premiums, and Activist Campaigns
- Mega Commercial Cloud/AI/Defense Contracts & Supplier Deals
- Share Buyback Authorizations vs Secondary Dilution
- Forensic Short Seller Reports (Hindenburg-style) & Executive Scandals
- Product Supercycles & Supply Chain Bottlenecks
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

EquityCatalystType = Literal[
    "EARNINGS_AND_GUIDANCE_SHOCK",
    "FDA_BIOTECH_CLINICAL_TRIAL",
    "M_AND_A_TAKEOVER_ACTIVIST",
    "MAJOR_CONTRACT_PARTNERSHIP",
    "CAPITAL_DILUTION_OR_BUYBACK",
    "FORENSIC_FRAUD_EXECUTIVE_CRISIS",
    "PRODUCT_SUPERCYCLE_SUPPLY_CHAIN",
]

PriceImpactMagnitude = Literal[
    "MASSIVE_SURGE_PLUS_15_PCT",
    "MODERATE_SURGE_PLUS_5_TO_15_PCT",
    "MILD_POSITIVE_PLUS_1_TO_5_PCT",
    "NEUTRAL_RANGEBOUND",
    "MILD_DROP_MINUS_1_TO_5_PCT",
    "MODERATE_DROP_MINUS_5_TO_15_PCT",
    "CATASTROPHIC_CRASH_MINUS_15_PCT",
]

ImpactTimeHorizon = Literal[
    "INTRADAY_GAP_ONLY",
    "SWING_MOMENTUM_1_TO_4_WEEKS",
    "STRUCTURAL_MULTI_QUARTER_SHIFT",
]

TacticalPlaybookAction = Literal[
    "AGGRESSIVE_LONG_GAP_AND_GO",
    "BUY_THE_DIP_ON_OVERREACTION",
    "FADE_OVERBOUGHT_EUPHORIA_SHORT",
    "IMMEDIATE_EXIT_AND_STOP_LOSS",
    "BUY_PROTECTIVE_PUT_OPTIONS",
    "WAIT_FOR_VOLATILITY_CRUSH",
]


class PeerSectorContagion(BaseModel):
    peer_symbol: str
    contagion_direction: Literal["BULLISH_SYMPATHY", "BEARISH_SYMPATHY", "COMPETITIVE_TAKEAWAY_GAIN"]
    expected_sympathy_move_pct: float
    rationale: str


class StockCatalystAnalysis(BaseModel):
    symbol: str
    headline: str
    catalyst_type: EquityCatalystType
    price_impact_magnitude: PriceImpactMagnitude
    estimated_price_move_pct: float
    time_horizon: ImpactTimeHorizon
    confidence_score_pct: float = Field(ge=0.0, le=100.0)
    core_driver_summary: str
    fundamental_mechanics: str
    tactical_playbook: TacticalPlaybookAction
    options_iv_impact: Literal["IV_EXPANSION_SPIKE", "IV_CRUSH_AFTER_EVENT", "STABLE_IV"]
    peer_contagion_effects: List[PeerSectorContagion] = Field(default_factory=list)
    risk_factors: List[str] = Field(default_factory=list)
