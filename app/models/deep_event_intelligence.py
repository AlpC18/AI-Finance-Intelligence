"""Omni-Source Geopolitical, Social Media & Deep Event Intelligence schemas.

Unifies:
1. Twitter / X Influencer Sentiment (Elon Musk, AI Lab Claude/OpenAI releases)
2. Geopolitical Conflict & Middle East Shock (Iran-Israel, Oil routes, Defense)
3. Surprise Delta Engine (Consensus Expected vs Actual Print)
4. Source Credibility & Anti-Spoofing Filter (Tier-1 to Tier-4)
5. 2nd & 3rd Order Supply Chain Knowledge Graph
6. Historical Analogue Memory & Cumulative Abnormal Return (CAR)
7. Event-Driven Options Strategy Synthesizer
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

SourceTier = Literal[
    "TIER_1_OFFICIAL_EDGAR_REUTERS_BLOOMBERG",
    "TIER_2_MAJOR_PRESS_WSJ_FT_CNBC",
    "TIER_3_FINANCIAL_BLOGS_COINDESK",
    "TIER_4_SOCIAL_TWITTER_UNVERIFIED",
]

ConflictEscalationLevel = Literal[
    "DEFCON_1_ACTIVE_MISSILE_STRIKE_WAR",
    "DEFCON_2_IMMINENT_RETALIATION_ALERT",
    "DEFCON_3_DIPLOMATIC_STANDOFF_SANCTIONS",
    "DEFCON_4_ROUTINE_GEOPOLITICAL_TENSION",
]


class SocialInfluencerImpact(BaseModel):
    handle: str  # e.g. @elonmusk, @sama, @AnthropicAI
    platform: str = "Twitter/X"
    is_verified_account: bool
    post_text: str
    impacted_tickers: List[str]
    sentiment_bias: Literal["HYPER_BULLISH", "MODERATE_BULLISH", "NEUTRAL", "BEARISH_FUD"]
    virality_multiplier: float = Field(ge=1.0, le=10.0)
    meme_or_narrative_theme: str


class GeopoliticalConflictImpact(BaseModel):
    conflict_theater: Literal["MIDDLE_EAST_IRAN_ISRAEL", "EASTERN_EUROPE", "TAIWAN_STRAIT", "RED_SEA_HORMUZ_SHIPPING"]
    escalation_status: ConflictEscalationLevel
    crude_oil_shock_direction: Literal["SPIKE_SURGE", "MODERATE_RISE", "STABLE", "DROP"]
    gold_safe_haven_status: Literal["MAXIMUM_ACCUMULATION", "MODERATE_INFLOW", "NEUTRAL"]
    defense_stocks_outlook: Literal["STRONG_SURGE", "NEUTRAL", "LAGGING"]
    broad_market_posture: Literal["RISK_OFF_FLIGHT_TO_SAFETY", "CONTROLLED_DIP", "IGNORING_HEADLINE"]
    affected_assets: Dict[str, str]  # e.g. USO, LMT, RTX, GLD, SPY


class SurpriseDeltaReport(BaseModel):
    metric_name: str  # e.g. "Fed Rate Decision (bps)", "EPS ($)", "CPI YoY (%)"
    expected_consensus: float
    actual_print: float
    surprise_delta_pct: float
    market_reaction_verdict: Literal[
        "MASSIVE_POSITIVE_SURPRISE",
        "MODERATE_BEAT",
        "IN_LINE_PRICED_IN",
        "HAWKISH_DISAPPOINTMENT_MISS",
        "CATASTROPHIC_MISS"
    ]


class SourceCredibilityReport(BaseModel):
    source_name: str
    tier: SourceTier
    credibility_weight: float = Field(ge=0.0, le=1.0)
    spoofing_or_fake_news_risk: Literal["LOW_VERIFIED", "ELEVATED_UNCONFIRMED", "HIGH_SUSPICION"]
    execution_gate_action: Literal["ALLOW_IMMEDIATE_EXECUTION", "REQUIRE_CROSS_CONFIRMATION", "QUARANTINE_BLOCK_ORDER"]


class ChainReactionNode(BaseModel):
    order_degree: Literal["1st_Order_Direct", "2nd_Order_Supply_Chain", "3rd_Order_Macro_Geopolitical"]
    asset_or_sector: str
    direction: Literal["BULLISH", "BEARISH", "VOLATILITY_SPIKE"]
    transmission_rationale: str


class HistoricalAnalogueCase(BaseModel):
    historical_event_name: str
    occurrence_date: str
    target_asset: str
    t_plus_1h_return_pct: float
    t_plus_24h_return_pct: float
    t_plus_7d_return_pct: float
    key_similarity_note: str


class EventOptionsStrategy(BaseModel):
    recommended_strategy_name: Literal[
        "LONG_STRADDLE_VOLATILITY_EXPANSION",
        "BULL_CALL_SPREAD_IV_CRUSH_PROTECTED",
        "BEAR_PUT_SPREAD_DOWNSIDE_HEDGE",
        "COLLAR_PROTECTIVE_FLOOR",
        "IRON_CONDOR_POST_EVENT_FADE"
    ]
    legs_breakdown: List[str]
    iv_crush_vulnerability: Literal["HIGH_RISK_AVOID_NAKED_CALLS", "PROTECTED_SPREAD_STRUCTURE", "LOW_IV_EXPANSION_PLAY"]
    max_risk_profile: str


class OmniEventIntelligenceReport(BaseModel):
    event_title: str
    timestamp_utc: str
    credibility: SourceCredibilityReport
    social_influencer: Optional[SocialInfluencerImpact] = None
    geopolitical: Optional[GeopoliticalConflictImpact] = None
    surprise_delta: Optional[SurpriseDeltaReport] = None
    chain_reaction_graph: List[ChainReactionNode]
    historical_analogues: List[HistoricalAnalogueCase]
    options_playbook: EventOptionsStrategy
    final_execution_mandate: str
