"""Named Legendary Investor Boardroom schemas (ai-hedge-fund & ai-berkshire).

Models the investment philosophies of iconic fund managers:
- Warren Buffett & Charlie Munger: Quality value, moat durability, ROIC, capital discipline.
- Cathie Wood: Hyper-growth disruptive innovation, TAM expansion, S-curve adoption.
- Michael Burry: Contrarian deep value, debt maturity walls, forensic accounting anomalies, short opportunities.
- Bill Ackman: High FCF margin, brand royalty, activist management catalysts.
- Ray Dalio: Macro debt cycles, liquidity regimes, All-Weather allocation.
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

InvestorName = Literal[
    "Warren Buffett",
    "Charlie Munger",
    "Cathie Wood",
    "Michael Burry",
    "Bill Ackman",
    "Ray Dalio",
]

InvestorVote = Literal["STRONG_BUY", "BUY", "HOLD", "AVOID_OR_SHORT"]


class InvestorEvaluation(BaseModel):
    investor_name: InvestorName
    firm_heritage: str
    core_philosophy: str
    vote: InvestorVote
    conviction_score_100: float = Field(ge=0.0, le=100.0)
    primary_thesis: str
    focal_metrics: Dict[str, str] = Field(default_factory=dict)
    key_objections: List[str] = Field(default_factory=list)


class BoardroomDebateSummary(BaseModel):
    symbol: str
    current_price: float
    investor_opinions: Dict[str, InvestorEvaluation]
    bull_faction_champions: List[str]
    bear_faction_champions: List[str]
    boardroom_consensus_vote: InvestorVote
    boardroom_alignment_score: float = Field(ge=0.0, le=100.0)
    synthesized_thesis: str
    recommended_portfolio_weight_pct: float = Field(ge=0.0, le=100.0)
