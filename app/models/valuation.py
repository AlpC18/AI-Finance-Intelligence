"""Deterministic Valuation, DCF, and Munger Inversion schemas (ai-berkshire & FinRobot).

Eliminates LLM arithmetic hallucinations by enforcing pure Python Decimal calculations
for Discounted Cash Flow (DCF), Reverse DCF, WACC, and Munger Inversion screening.
"""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class DCFScenario(BaseModel):
    scenario_name: Literal["bear", "base", "bull"]
    revenue_growth_pct: float
    operating_margin_pct: float
    discount_rate_wacc_pct: float
    terminal_growth_pct: float
    projected_fcf_5y: List[float]
    terminal_value: float
    enterprise_value: float
    equity_value: float
    fair_value_per_share: float
    upside_downside_pct: float


class ReverseDCFResult(BaseModel):
    current_price: float
    implied_5y_fcf_growth_rate_pct: float
    market_expectation_assessment: Literal[
        "undervalued_low_expectations",
        "fairly_priced_moderate_growth",
        "priced_for_perfection_hyper_growth",
        "distressed_negative_expectations"
    ]
    historical_fcf_growth_rate_pct: float
    growth_gap_pct: float  # Implied growth minus historical growth


class QuickKillCheck(BaseModel):
    passed: bool
    quick_kill_triggered: bool
    kill_reasons: List[str] = Field(default_factory=list)
    fcf_positive: bool
    interest_coverage_healthy: bool
    dilution_rate_acceptable: bool
    debt_to_equity_healthy: bool


class MungerInversionAnalysis(BaseModel):
    symbol: str
    inversion_question: str = "How does this business permanently lose capital or go bankrupt?"
    catastrophic_failure_modes: List[str]
    moat_durability_rating: Literal["wide_durable", "narrow_vulnerable", "no_moat"]
    capital_allocation_score_10: float
    quick_kill: QuickKillCheck
    mirror_test_5_sentences: List[str]
    verdict: Literal["INVESTMENT_GRADE", "REJECT_QUICK_KILL", "WATCHLIST_DISCIPLINE"]


class CompleteValuationReport(BaseModel):
    symbol: str
    current_price: float
    shares_outstanding_m: float
    total_debt_m: float
    cash_and_equivalents_m: float
    base_fcf_m: float
    wacc_pct: float
    scenarios: Dict[str, DCFScenario]
    reverse_dcf: ReverseDCFResult
    munger_inversion: MungerInversionAnalysis
    margin_of_safety_pct: float
    final_valuation_verdict: str
