"""Multi-Agent Decision & Debate schemas.

Enables collaborative multi-agent trading deliberation where specialized agent
personas (Macro, Fundamental, Technical, Risk Manager) evaluate market data,
debate risks, and reach consensus with strict Risk Manager veto authority.
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

AgentRole = Literal[
    "macro_analyst",
    "fundamental_analyst",
    "technical_analyst",
    "risk_manager",
    "synthesizer",
]

ConsensusStrength = Literal["unanimous", "majority", "split", "vetoed"]
MarketAction = Literal["BUY", "SELL", "HOLD"]


class AgentOpinion(BaseModel):
    role: AgentRole
    name: str
    action: MarketAction
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    key_metrics: Dict[str, str] = Field(default_factory=dict)
    risks_flagged: List[str] = Field(default_factory=list)
    veto: bool = False
    veto_reason: Optional[str] = None


class MultiAgentDebateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    include_web_search: bool = False
    risk_tolerance: Literal["conservative", "moderate", "aggressive"] = "moderate"
    custom_context: Optional[str] = None


class ActionablePlan(BaseModel):
    suggested_entry: Optional[float] = None
    suggested_stop_loss: Optional[float] = None
    suggested_take_profit: Optional[float] = None
    suggested_position_size_pct: float = Field(ge=0.0, le=100.0, default=5.0)
    time_horizon: Literal["intraday", "swing_1_5d", "position_weeks", "long_term"] = "swing_1_5d"
    risk_reward_ratio: Optional[float] = None


class MultiAgentConsensus(BaseModel):
    symbol: str
    final_action: MarketAction
    final_confidence: float = Field(ge=0.0, le=1.0)
    consensus_strength: ConsensusStrength
    risk_veto_applied: bool = False
    opinions: Dict[str, AgentOpinion]
    deliberation_summary: str
    actionable_plan: ActionablePlan
    model_used: str = "multi-agent-ensemble"
