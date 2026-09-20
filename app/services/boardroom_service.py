"""Named Legendary Investor Boardroom Service.

Simulates boardroom debates among Warren Buffett, Charlie Munger, Cathie Wood,
Michael Burry, Bill Ackman, and Ray Dalio to synthesize multi-perspective conviction.
"""
from typing import Dict, List, Optional
import structlog

from app.models.boardroom import (
    BoardroomDebateSummary,
    InvestorEvaluation,
    InvestorVote,
)

logger = structlog.get_logger("boardroom_service")


class BoardroomService:
    def convene_boardroom(
        self,
        symbol: str,
        price: float,
        pe_ratio: float,
        debt_to_equity: float,
        fcf_yield_pct: float,
        revenue_growth_3y_pct: float,
        roic_pct: float = 18.0,
        rsi: float = 50.0,
        vix: float = 16.5,
    ) -> BoardroomDebateSummary:
        """Runs the boardroom investment committee simulation."""
        sym = symbol.upper().strip()

        # 1. Warren Buffett
        buffett = self._eval_buffett(sym, pe_ratio, debt_to_equity, roic_pct, fcf_yield_pct)

        # 2. Charlie Munger
        munger = self._eval_munger(sym, pe_ratio, debt_to_equity, roic_pct)

        # 3. Cathie Wood
        cathie = self._eval_cathie_wood(sym, revenue_growth_3y_pct, pe_ratio)

        # 4. Michael Burry
        burry = self._eval_michael_burry(sym, pe_ratio, debt_to_equity, fcf_yield_pct, rsi)

        # 5. Bill Ackman
        ackman = self._eval_bill_ackman(sym, fcf_yield_pct, debt_to_equity, roic_pct)

        # 6. Ray Dalio
        dalio = self._eval_ray_dalio(sym, vix, debt_to_equity)

        opinions = {
            "Warren Buffett": buffett,
            "Charlie Munger": munger,
            "Cathie Wood": cathie,
            "Michael Burry": burry,
            "Bill Ackman": ackman,
            "Ray Dalio": dalio,
        }

        # Tabulate factions
        bulls = [inv for inv, evaln in opinions.items() if evaln.vote in {"STRONG_BUY", "BUY"}]
        bears = [inv for inv, evaln in opinions.items() if evaln.vote == "AVOID_OR_SHORT"]

        # Consensus Vote
        if len(bulls) >= 4:
            consensus_vote: InvestorVote = "STRONG_BUY" if len(bulls) >= 5 else "BUY"
            alloc_pct = 12.5 if consensus_vote == "STRONG_BUY" else 8.0
        elif len(bears) >= 3:
            consensus_vote = "AVOID_OR_SHORT"
            alloc_pct = 0.0
        else:
            consensus_vote = "HOLD"
            alloc_pct = 4.0

        avg_conviction = sum(e.conviction_score_100 for e in opinions.values()) / len(opinions)
        alignment_score = round(max(len(bulls), len(bears)) / len(opinions) * 100.0, 1)

        thesis = (
            f"Boardroom committee concluded with {consensus_vote} ({len(bulls)}/6 Bull vs {len(bears)}/6 Bear). "
            f"Buffett & Munger focal point: ROIC {roic_pct:.1f}%, while Burry flagged D/E of {debt_to_equity:.2f}."
        )

        return BoardroomDebateSummary(
            symbol=sym,
            current_price=price,
            investor_opinions=opinions,
            bull_faction_champions=bulls,
            bear_faction_champions=bears,
            boardroom_consensus_vote=consensus_vote,
            boardroom_alignment_score=alignment_score,
            synthesized_thesis=thesis,
            recommended_portfolio_weight_pct=alloc_pct,
        )

    def _eval_buffett(self, sym: str, pe: float, de: float, roic: float, fcf_yield: float) -> InvestorEvaluation:
        focal = {"ROIC": f"{roic:.1f}%", "P/E": f"{pe:.1f}", "FCF Yield": f"{fcf_yield:.1f}%"}
        if roic >= 15.0 and de <= 1.2 and pe <= 25.0:
            return InvestorEvaluation(
                investor_name="Warren Buffett",
                firm_heritage="Berkshire Hathaway",
                core_philosophy="Durable competitive moat, high return on capital, purchased at a sensible price.",
                vote="BUY",
                conviction_score_100=85.0,
                primary_thesis=f"Wonderful business earning {roic:.1f}% on capital with conservative balance sheet.",
                focal_metrics=focal,
                key_objections=[],
            )
        elif pe > 38.0:
            return InvestorEvaluation(
                investor_name="Warren Buffett",
                firm_heritage="Berkshire Hathaway",
                core_philosophy="Durable competitive moat, high return on capital, purchased at a sensible price.",
                vote="HOLD",
                conviction_score_100=60.0,
                primary_thesis=f"Quality company but valuation multiple ({pe:.1f}x) provides inadequate margin of safety.",
                focal_metrics=focal,
                key_objections=["Valuation stretch", "Low earnings yield"],
            )
        return InvestorEvaluation(
            investor_name="Warren Buffett",
            firm_heritage="Berkshire Hathaway",
            core_philosophy="Durable competitive moat, high return on capital, purchased at a sensible price.",
            vote="HOLD",
            conviction_score_100=55.0,
            primary_thesis="Business fundamentals are adequate; awaiting deeper margin of safety.",
            focal_metrics=focal,
            key_objections=[],
        )

    def _eval_munger(self, sym: str, pe: float, de: float, roic: float) -> InvestorEvaluation:
        focal = {"Inversion_Risk": "Debt & Dilution", "ROIC": f"{roic:.1f}%"}
        if de > 2.0:
            return InvestorEvaluation(
                investor_name="Charlie Munger",
                firm_heritage="Daily Journal & Berkshire",
                core_philosophy="Invert, always invert. Avoid catastrophic stupidity and excessive leverage.",
                vote="AVOID_OR_SHORT",
                conviction_score_100=88.0,
                primary_thesis=f"Excessive debt leverage ({de:.2f}x) violates foundational financial anti-fragility.",
                focal_metrics=focal,
                key_objections=["Leverage trap", "Refinancing risk"],
            )
        elif roic > 18.0:
            return InvestorEvaluation(
                investor_name="Charlie Munger",
                firm_heritage="Daily Journal & Berkshire",
                core_philosophy="Invert, always invert. Avoid catastrophic stupidity and excessive leverage.",
                vote="BUY",
                conviction_score_100=82.0,
                primary_thesis="Compounding machine with strong pricing power and elite capital efficiency.",
                focal_metrics=focal,
                key_objections=[],
            )
        return InvestorEvaluation(
            investor_name="Charlie Munger",
            firm_heritage="Daily Journal & Berkshire",
            core_philosophy="Invert, always invert. Avoid catastrophic stupidity and excessive leverage.",
            vote="HOLD",
            conviction_score_100=60.0,
            primary_thesis="No obvious catastrophe, but not a no-brainer compounding fortress.",
            focal_metrics=focal,
            key_objections=[],
        )

    def _eval_cathie_wood(self, sym: str, rev_growth: float, pe: float) -> InvestorEvaluation:
        focal = {"3Y_Revenue_CAGR": f"{rev_growth:.1f}%", "TAM_Disruption": "Exponential"}
        if rev_growth >= 25.0:
            return InvestorEvaluation(
                investor_name="Cathie Wood",
                firm_heritage="ARK Invest",
                core_philosophy="Exponential technologies, Wright's Law cost declines, massive addressable markets.",
                vote="STRONG_BUY",
                conviction_score_100=92.0,
                primary_thesis=f"Hyper-growth trajectory ({rev_growth:.1f}% CAGR) pioneering S-curve market adoption.",
                focal_metrics=focal,
                key_objections=[],
            )
        elif rev_growth < 8.0:
            return InvestorEvaluation(
                investor_name="Cathie Wood",
                firm_heritage="ARK Invest",
                core_philosophy="Exponential technologies, Wright's Law cost declines, massive addressable markets.",
                vote="AVOID_OR_SHORT",
                conviction_score_100=75.0,
                primary_thesis="Legacy business model lacking disruptive technological innovation catalyst.",
                focal_metrics=focal,
                key_objections=["Lacks innovation vector", "Low growth"],
            )
        return InvestorEvaluation(
            investor_name="Cathie Wood",
            firm_heritage="ARK Invest",
            core_philosophy="Exponential technologies, Wright's Law cost declines, massive addressable markets.",
            vote="HOLD",
            conviction_score_100=50.0,
            primary_thesis="Moderate innovation tailwind; monitoring technological inflection point.",
            focal_metrics=focal,
            key_objections=[],
        )

    def _eval_michael_burry(self, sym: str, pe: float, de: float, fcf_yield: float, rsi: float) -> InvestorEvaluation:
        focal = {"P/E": f"{pe:.1f}", "FCF_Yield": f"{fcf_yield:.1f}%", "RSI": f"{rsi:.1f}"}
        if pe > 45.0 and rsi > 70.0:
            return InvestorEvaluation(
                investor_name="Michael Burry",
                firm_heritage="Scion Asset Management",
                core_philosophy="Deep value, accounting forensics, asymmetric short opportunities, macro imbalances.",
                vote="AVOID_OR_SHORT",
                conviction_score_100=90.0,
                primary_thesis=f"Priced for perfection (P/E {pe:.1f}x) with overbought speculative euphoria.",
                focal_metrics=focal,
                key_objections=["Multiple contraction catalyst", "Speculative bubble risk"],
            )
        elif pe < 12.0 and fcf_yield > 8.0:
            return InvestorEvaluation(
                investor_name="Michael Burry",
                firm_heritage="Scion Asset Management",
                core_philosophy="Deep value, accounting forensics, asymmetric short opportunities, macro imbalances.",
                vote="STRONG_BUY",
                conviction_score_100=86.0,
                primary_thesis=f"Heavily overlooked deep value with {fcf_yield:.1f}% free cash flow yield.",
                focal_metrics=focal,
                key_objections=[],
            )
        return InvestorEvaluation(
            investor_name="Michael Burry",
            firm_heritage="Scion Asset Management",
            core_philosophy="Deep value, accounting forensics, asymmetric short opportunities, macro imbalances.",
            vote="HOLD",
            conviction_score_100=65.0,
            primary_thesis="Balanced risk-reward; no compelling asymmetric short or long catalyst.",
            focal_metrics=focal,
            key_objections=[],
        )

    def _eval_bill_ackman(self, sym: str, fcf_yield: float, de: float, roic: float) -> InvestorEvaluation:
        focal = {"FCF_Yield": f"{fcf_yield:.1f}%", "ROIC": f"{roic:.1f}%"}
        if fcf_yield >= 5.0 and roic >= 14.0 and de <= 1.8:
            return InvestorEvaluation(
                investor_name="Bill Ackman",
                firm_heritage="Pershing Square",
                core_philosophy="Simple, predictable, high free cash flow generative businesses with strong pricing power.",
                vote="BUY",
                conviction_score_100=80.0,
                primary_thesis="Cash generative tollbooth business with substantial pricing power and management optionality.",
                focal_metrics=focal,
                key_objections=[],
            )
        return InvestorEvaluation(
            investor_name="Bill Ackman",
            firm_heritage="Pershing Square",
            core_philosophy="Simple, predictable, high free cash flow generative businesses with strong pricing power.",
            vote="HOLD",
            conviction_score_100=55.0,
            primary_thesis="Does not meet high FCF hurdle rate for an activist core holding.",
            focal_metrics=focal,
            key_objections=[],
        )

    def _eval_ray_dalio(self, sym: str, vix: float, de: float) -> InvestorEvaluation:
        focal = {"VIX_Regime": f"{vix:.1f}", "Debt_Burden": f"{de:.2f}"}
        if vix > 30.0:
            return InvestorEvaluation(
                investor_name="Ray Dalio",
                firm_heritage="Bridgewater Associates",
                core_philosophy="Economic machine, debt deleveraging cycles, risk parity across economic environments.",
                vote="HOLD",
                conviction_score_100=75.0,
                primary_thesis="High macro volatility regime; priority is asset diversification and capital preservation.",
                focal_metrics=focal,
                key_objections=["Macro volatility shock", "Elevated systemic correlation"],
            )
        return InvestorEvaluation(
            investor_name="Ray Dalio",
            firm_heritage="Bridgewater Associates",
            core_philosophy="Economic machine, debt deleveraging cycles, risk parity across economic environments.",
            vote="BUY" if de < 1.5 else "HOLD",
            conviction_score_100=70.0,
            primary_thesis="Macro environment is balanced; debt dynamics remain manageable within current cycle.",
            focal_metrics=focal,
            key_objections=[],
        )
