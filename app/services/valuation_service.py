"""Deterministic Valuation Service (DCF, Reverse DCF, Munger Inversion).

Pure Python deterministic calculations for financial valuation models.
Prevents LLM math hallucinations and enforces value investing discipline.
"""
from decimal import Decimal
import math
from typing import Dict, List, Optional
import structlog

from app.models.valuation import (
    CompleteValuationReport,
    DCFScenario,
    MungerInversionAnalysis,
    QuickKillCheck,
    ReverseDCFResult,
)

logger = structlog.get_logger("valuation_service")


class ValuationService:
    def calculate_dcf(
        self,
        current_price: float,
        shares_outstanding_m: float,
        total_debt_m: float,
        cash_m: float,
        base_fcf_m: float,
        growth_rate_pct: float,
        wacc_pct: float = 9.0,
        terminal_growth_pct: float = 2.5,
        scenario_name: str = "base",
    ) -> DCFScenario:
        """Calculates 5-year DCF + Gordon Growth Terminal Value."""
        wacc = max(0.04, wacc_pct / 100.0)
        g_terminal = min(wacc - 0.005, terminal_growth_pct / 100.0)
        growth = growth_rate_pct / 100.0

        projected_fcf: List[float] = []
        pv_fcf = 0.0
        fcf = base_fcf_m

        for yr in range(1, 6):
            fcf = fcf * (1.0 + growth)
            projected_fcf.append(round(fcf, 2))
            pv_fcf += fcf / ((1.0 + wacc) ** yr)

        # Terminal Value = (FCF_5 * (1 + g)) / (wacc - g)
        fcf_terminal = fcf * (1.0 + g_terminal)
        terminal_value = fcf_terminal / (wacc - g_terminal)
        pv_terminal_value = terminal_value / ((1.0 + wacc) ** 5)

        enterprise_value = pv_fcf + pv_terminal_value
        equity_value = enterprise_value + cash_m - total_debt_m
        fair_value_per_share = max(0.01, equity_value / max(1.0, shares_outstanding_m))
        upside = ((fair_value_per_share - current_price) / current_price) * 100.0

        return DCFScenario(
            scenario_name=scenario_name,
            revenue_growth_pct=growth_rate_pct,
            operating_margin_pct=25.0,
            discount_rate_wacc_pct=wacc_pct,
            terminal_growth_pct=terminal_growth_pct,
            projected_fcf_5y=projected_fcf,
            terminal_value=round(terminal_value, 2),
            enterprise_value=round(enterprise_value, 2),
            equity_value=round(equity_value, 2),
            fair_value_per_share=round(fair_value_per_share, 2),
            upside_downside_pct=round(upside, 2),
        )

    def calculate_reverse_dcf(
        self,
        current_price: float,
        shares_outstanding_m: float,
        total_debt_m: float,
        cash_m: float,
        base_fcf_m: float,
        wacc_pct: float = 9.0,
        terminal_growth_pct: float = 2.5,
        historical_fcf_growth_pct: float = 12.0,
    ) -> ReverseDCFResult:
        """Solves for the implied 5-year FCF growth rate baked into the current market price."""
        target_equity_value = current_price * shares_outstanding_m
        target_ev = target_equity_value + total_debt_m - cash_m

        # Binary search for implied growth rate between -50% and +100%
        low, high = -0.50, 1.00
        implied_g = 0.10

        wacc = max(0.04, wacc_pct / 100.0)
        g_terminal = min(wacc - 0.005, terminal_growth_pct / 100.0)

        for _ in range(50):
            mid = (low + high) / 2.0
            # Calculate EV at mid
            pv_fcf = 0.0
            fcf = base_fcf_m
            for yr in range(1, 6):
                fcf = fcf * (1.0 + mid)
                pv_fcf += fcf / ((1.0 + wacc) ** yr)
            fcf_terminal = fcf * (1.0 + g_terminal)
            tv = fcf_terminal / (wacc - g_terminal)
            pv_tv = tv / ((1.0 + wacc) ** 5)
            ev = pv_fcf + pv_tv

            if ev < target_ev:
                low = mid
            else:
                high = mid
            implied_g = mid

        implied_pct = round(implied_g * 100.0, 2)
        growth_gap = round(implied_pct - historical_fcf_growth_pct, 2)

        if implied_pct < 5.0:
            assessment = "undervalued_low_expectations"
        elif implied_pct < 18.0:
            assessment = "fairly_priced_moderate_growth"
        elif implied_pct >= 18.0:
            assessment = "priced_for_perfection_hyper_growth"
        else:
            assessment = "distressed_negative_expectations"

        return ReverseDCFResult(
            current_price=current_price,
            implied_5y_fcf_growth_rate_pct=implied_pct,
            market_expectation_assessment=assessment,
            historical_fcf_growth_rate_pct=historical_fcf_growth_pct,
            growth_gap_pct=growth_gap,
        )

    def evaluate_munger_inversion(
        self,
        symbol: str,
        base_fcf_m: float,
        debt_to_equity: float,
        interest_coverage: float,
        share_dilution_pct_annual: float,
        pe_ratio: float,
    ) -> MungerInversionAnalysis:
        """Applies Charlie Munger Inversion test and Quick-Kill checklist."""
        kill_reasons: List[str] = []
        failure_modes: List[str] = []

        fcf_pos = base_fcf_m > 0
        if not fcf_pos:
            kill_reasons.append("Negative Free Cash Flow (Cash burning business model)")
            failure_modes.append("Inability to self-fund operations leading to dilutive equity financing")

        int_cov_ok = interest_coverage >= 2.5
        if not int_cov_ok:
            kill_reasons.append(f"Insufficient interest coverage ({interest_coverage:.1f}x < 2.5x threshold)")
            failure_modes.append("Refinancing risk at debt maturity wall during high interest rates")

        dilution_ok = share_dilution_pct_annual < 3.0
        if not dilution_ok:
            kill_reasons.append(f"Excessive annual share dilution ({share_dilution_pct_annual:.1f}%/yr)")
            failure_modes.append("Shareholder value destruction via excessive executive stock option grants")

        debt_ok = debt_to_equity < 2.5
        if not debt_ok:
            kill_reasons.append(f"Elevated debt-to-equity ratio ({debt_to_equity:.2f} > 2.5)")
            failure_modes.append("Leverage amplification triggering financial distress during cyclical downturn")

        is_killed = len(kill_reasons) > 0

        # General failure modes
        if pe_ratio > 40.0:
            failure_modes.append("Multiple contraction if growth decelerates below hyper-growth expectations")
        failure_modes.append("Technological obsolescence and aggressive competitive pricing war")

        quick_kill = QuickKillCheck(
            passed=not is_killed,
            quick_kill_triggered=is_killed,
            kill_reasons=kill_reasons,
            fcf_positive=fcf_pos,
            interest_coverage_healthy=int_cov_ok,
            dilution_rate_acceptable=dilution_ok,
            debt_to_equity_healthy=debt_ok,
        )

        moat = "wide_durable" if (debt_ok and fcf_pos and int_cov_ok and pe_ratio < 30) else (
            "narrow_vulnerable" if fcf_pos else "no_moat"
        )
        cap_alloc_score = 8.5 if not is_killed else 3.5

        # 5-Sentence Mirror Test
        sym = symbol.upper().strip()
        mirror_test = [
            f"1. {sym} generates ${base_fcf_m:.0f}M in annual free cash flow supported by a {moat.replace('_', ' ')} moat.",
            f"2. Balance sheet solvency maintains a {debt_to_equity:.1f}x debt-to-equity and {interest_coverage:.1f}x interest coverage.",
            f"3. Capital allocation has maintained disciplined shareholder return with {share_dilution_pct_annual:.1f}% annual share dilution.",
            f"4. The primary failure mode under inversion is: {failure_modes[0]}.",
            f"5. Verdict: {'Investment grade with sufficient margin of safety.' if not is_killed else 'Aborted under Munger Quick-Kill checklist.'}"
        ]

        verdict = "REJECT_QUICK_KILL" if is_killed else ("INVESTMENT_GRADE" if moat == "wide_durable" else "WATCHLIST_DISCIPLINE")

        return MungerInversionAnalysis(
            symbol=sym,
            catastrophic_failure_modes=failure_modes,
            moat_durability_rating=moat,
            capital_allocation_score_10=cap_alloc_score,
            quick_kill=quick_kill,
            mirror_test_5_sentences=mirror_test,
            verdict=verdict,
        )

    def generate_full_report(
        self,
        symbol: str,
        current_price: float,
        shares_outstanding_m: float = 1000.0,
        total_debt_m: float = 15000.0,
        cash_m: float = 25000.0,
        base_fcf_m: float = 8000.0,
        historical_fcf_growth_pct: float = 12.0,
        debt_to_equity: float = 1.2,
        interest_coverage: float = 6.5,
        share_dilution_pct_annual: float = 0.5,
        pe_ratio: float = 24.0,
    ) -> CompleteValuationReport:
        """Generates comprehensive institutional valuation report."""
        sym = symbol.upper().strip()

        # Three Scenarios: Bear, Base, Bull
        bear = self.calculate_dcf(
            current_price, shares_outstanding_m, total_debt_m, cash_m, base_fcf_m,
            growth_rate_pct=historical_fcf_growth_pct * 0.4, wacc_pct=10.5, terminal_growth_pct=2.0, scenario_name="bear"
        )
        base = self.calculate_dcf(
            current_price, shares_outstanding_m, total_debt_m, cash_m, base_fcf_m,
            growth_rate_pct=historical_fcf_growth_pct, wacc_pct=9.0, terminal_growth_pct=2.5, scenario_name="base"
        )
        bull = self.calculate_dcf(
            current_price, shares_outstanding_m, total_debt_m, cash_m, base_fcf_m,
            growth_rate_pct=historical_fcf_growth_pct * 1.5, wacc_pct=8.0, terminal_growth_pct=3.0, scenario_name="bull"
        )

        rev_dcf = self.calculate_reverse_dcf(
            current_price, shares_outstanding_m, total_debt_m, cash_m, base_fcf_m,
            wacc_pct=9.0, terminal_growth_pct=2.5, historical_fcf_growth_pct=historical_fcf_growth_pct
        )

        munger = self.evaluate_munger_inversion(
            sym, base_fcf_m, debt_to_equity, interest_coverage, share_dilution_pct_annual, pe_ratio
        )

        margin_of_safety = base.upside_downside_pct
        verdict = f"Base Fair Value: ${base.fair_value_per_share:.2f} ({margin_of_safety:+.1f}% margin of safety). Inversion: {munger.verdict}."

        return CompleteValuationReport(
            symbol=sym,
            current_price=current_price,
            shares_outstanding_m=shares_outstanding_m,
            total_debt_m=total_debt_m,
            cash_and_equivalents_m=cash_m,
            base_fcf_m=base_fcf_m,
            wacc_pct=9.0,
            scenarios={"bear": bear, "base": base, "bull": bull},
            reverse_dcf=rev_dcf,
            munger_inversion=munger,
            margin_of_safety_pct=round(margin_of_safety, 2),
            final_valuation_verdict=verdict,
        )
