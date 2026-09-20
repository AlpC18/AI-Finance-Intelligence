"""Macroeconomic, Regulatory, and Catalyst News Intelligence schemas.

Specialized structured models for analyzing breaking financial news events:
- Federal Reserve / Central Bank interest rate decisions and CPI/FOMC releases
- SEC / DoJ / Regulatory lawsuits, court rulings, and antitrust verdicts
- Crypto ETF inflows, token unlocks, exchange enforcement actions, and exploits
- Multi-asset directional transmission across Equities, Crypto, Yields, and DXY
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

NewsEventType = Literal[
    "FED_CENTRAL_BANK_DECISION",
    "REGULATORY_LAWSUIT_RULING",
    "MACRO_INFLATION_CPI_JOBS",
    "CRYPTO_ETF_AND_REGULATORY",
    "EARNINGS_SHOCK_GUIDANCE",
    "GEOPOLITICAL_SANCTION_TARIFF",
]

MacroRegimeTone = Literal["HAWKISH", "DOVISH", "NEUTRAL_BALANCED", "CRISIS_PANIC"]

AssetImpactDirection = Literal["STRONG_BULLISH", "MODERATE_BULLISH", "NEUTRAL", "MODERATE_BEARISH", "STRONG_BEARISH"]

LegalThreatSeverity = Literal["BENIGN_DISMISSED", "FINES_ABSORBABLE", "BUSINESS_MODEL_RISK", "EXISTENTIAL_CRISIS"]


class SingleAssetTransmission(BaseModel):
    ticker_or_asset: str  # e.g. BTC, ETH, SPY, QQQ, NVDA, COIN, DXY, US10Y
    direction: AssetImpactDirection
    expected_volatility_pct: float
    transmission_channel_explanation: str


class NewsEventImpactAnalysis(BaseModel):
    headline: str
    event_type: NewsEventType
    macro_tone: MacroRegimeTone
    urgency_level: Literal["BREAKING_URGENT", "HIGH_PRIORITY", "STANDARD"]
    confidence_score_pct: float = Field(ge=0.0, le=100.0)
    primary_takeaway: str
    central_bank_implications: Optional[str] = None
    legal_verdict_severity: Optional[LegalThreatSeverity] = None
    affected_assets: List[SingleAssetTransmission]
    recommended_portfolio_posture: Literal[
        "RISK_ON_EXPAND_EQUITY_CRYPTO",
        "RISK_OFF_FLIGHT_TO_CASH_BONDS",
        "DELTA_HEDGE_VOLATILITY",
        "TACTICAL_LONG_SPECIFIC_CATALYST",
        "NO_ACTION_REQUIRED"
    ]
    automated_hedging_mandate: Optional[str] = None


class BatchNewsDigestReport(BaseModel):
    total_articles_analyzed: int
    dominant_market_theme: str
    net_macro_sentiment_score: float = Field(ge=-1.0, le=1.0)
    high_impact_events: List[NewsEventImpactAnalysis]
    top_affected_tickers: List[str]
    systemic_risk_alert: bool = False
