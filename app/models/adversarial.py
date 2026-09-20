"""Simulated Adversarial Red-Team / Blue-Team Desk schemas (TradingAgents).

Cross-examines every trade proposal with an aggressive Red-Team bear audit
to eliminate LLM confirmation bias and false-positive echo chambers.
"""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class BlueTeamArgument(BaseModel):
    proposing_team: str = "Blue Team (Long Prosecution)"
    upside_catalysts: List[str]
    technical_triggers: List[str]
    growth_thesis: str
    target_price: float
    conviction_score: float = Field(ge=0.0, le=100.0)


class RedTeamChallenge(BaseModel):
    opposing_team: str = "Red Team (Adversarial Bear Audit)"
    bear_counter_thesis: str
    identified_failure_modes: List[str]
    valuation_vulnerabilities: List[str]
    liquidity_and_macro_risks: List[str]
    severity_score: float = Field(ge=0.0, le=100.0)  # Higher means harsher criticism


class AdversarialAuditReport(BaseModel):
    symbol: str
    action_proposed: Literal["BUY", "SELL"]
    proposal_survived: bool
    audit_verdict: Literal["APPROVED_PROCEED_TO_RISK", "REJECTED_RED_TEAM_KILL", "APPROVED_WITH_TIGHTER_STOPS"]
    survival_score_100: float = Field(ge=0.0, le=100.0)
    blue_team: BlueTeamArgument
    red_team: RedTeamChallenge
    arbiter_ruling: str
    mandated_risk_mitigations: List[str] = Field(default_factory=list)
