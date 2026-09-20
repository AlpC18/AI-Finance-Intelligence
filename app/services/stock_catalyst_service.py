"""Stock Catalyst & Individual Equity Trajectory Service.

Specialized engine for determining the directional magnitude, time horizon,
and peer contagion of company-specific financial news.
"""
from typing import List, Optional
import structlog

from app.models.equity_catalyst import (
    EquityCatalystType,
    ImpactTimeHorizon,
    PeerSectorContagion,
    PriceImpactMagnitude,
    StockCatalystAnalysis,
    TacticalPlaybookAction,
)

logger = structlog.get_logger("stock_catalyst_service")


class StockCatalystAnalyzerService:
    def analyze_stock_news(
        self,
        symbol: str,
        headline: str,
        raw_content: str = "",
        current_price: float = 100.0,
    ) -> StockCatalystAnalysis:
        """Parses company headline and computes expected price move, playbook, and peer contagion."""
        sym = symbol.upper().strip()
        text = f"{headline} {raw_content}".lower()

        # 1. FDA & Biotech Clinical Trials
        if any(w in text for w in ["fda", "phase 3", "clinical trial", "drug approval", "pdufa", "complete response letter", "fast track", "crl"]):
            return self._analyze_fda_biotech(sym, headline, text)

        # 2. Forensic Short Seller / Fraud / Executive Crisis
        elif any(w in text for w in ["short seller", "hindenburg", "fraud", "accounting investigation", "sec investigation", "doj probe", "whistleblower", "ceo resigns unexpectedly"]):
            return self._analyze_forensic_crisis(sym, headline, text)

        # 3. M&A Takeover & Activist Campaign
        elif any(w in text for w in ["buyout", "takeover", "acquire", "merger", "activist stake", "elliott management", "tender offer", "board seats"]):
            return self._analyze_m_and_a(sym, headline, text)

        # 4. Major Commercial Contracts & Partnerships
        elif any(w in text for w in ["signs multi-billion", "defense contract", "hyperscaler deal", "exclusive partnership", "selected by"]):
            return self._analyze_major_contracts(sym, headline, text)

        # 5. Capital Structure: Dilution vs Buyback
        elif any(w in text for w in ["buyback authorization", "share repurchase", "secondary offering", "dilution", "convertible notes", "shares sold"]):
            return self._analyze_capital_structure(sym, headline, text)

        # 6. Default: Earnings & Guidance Shocks
        return self._analyze_earnings_guidance(sym, headline, text)

    def _analyze_fda_biotech(self, sym: str, headline: str, text: str) -> StockCatalystAnalysis:
        is_approval = any(w in text for w in ["approves", "phase 3 success", "positive results", "meets primary endpoint", "fast track"])
        is_rejection = any(w in text for w in ["rejects", "failed", "crl", "complete response letter", "clinical hold", "misses endpoint"])

        if is_approval:
            mag: PriceImpactMagnitude = "MASSIVE_SURGE_PLUS_15_PCT"
            est_move = 22.5
            horizon: ImpactTimeHorizon = "STRUCTURAL_MULTI_QUARTER_SHIFT"
            playbook: TacticalPlaybookAction = "AGGRESSIVE_LONG_GAP_AND_GO"
            summary = "FDA approval unlocks new commercial market revenue and validates therapeutic pipeline."
            mechanics = "TAM expansion and discounted future cash flow revisions drastically raise intrinsic DCF target."
            peers = [
                PeerSectorContagion(peer_symbol="XBI", contagion_direction="BULLISH_SYMPATHY", expected_sympathy_move_pct=2.5, rationale="Biotech sector sentiment lift.")
            ]
        elif is_rejection:
            mag = "CATASTROPHIC_CRASH_MINUS_15_PCT"
            est_move = -35.0
            horizon = "STRUCTURAL_MULTI_QUARTER_SHIFT"
            playbook = "IMMEDIATE_EXIT_AND_STOP_LOSS"
            summary = "FDA rejection or clinical trial failure eliminates key pipeline revenue asset."
            mechanics = "Write-down of R&D capitalized assets and multi-year delay in commercialization timeline."
            peers = [
                PeerSectorContagion(peer_symbol="COMPETITOR", contagion_direction="COMPETITIVE_TAKEAWAY_GAIN", expected_sympathy_move_pct=5.0, rationale="Rival gains unchallenged market exclusivity.")
            ]
        else:
            mag = "MILD_POSITIVE_PLUS_1_TO_5_PCT"
            est_move = 3.0
            horizon = "INTRADAY_GAP_ONLY"
            playbook = "WAIT_FOR_VOLATILITY_CRUSH"
            summary = "Routine clinical development update."
            mechanics = "Incremental progress without definitive regulatory milestone."
            peers = []

        return StockCatalystAnalysis(
            symbol=sym,
            headline=headline,
            catalyst_type="FDA_BIOTECH_CLINICAL_TRIAL",
            price_impact_magnitude=mag,
            estimated_price_move_pct=est_move,
            time_horizon=horizon,
            confidence_score_pct=95.0,
            core_driver_summary=summary,
            fundamental_mechanics=mechanics,
            tactical_playbook=playbook,
            options_iv_impact="IV_CRUSH_AFTER_EVENT",
            peer_contagion_effects=peers,
            risk_factors=["Post-approval commercial adoption pace", "Pricing and reimbursement hurdles"],
        )

    def _analyze_forensic_crisis(self, sym: str, headline: str, text: str) -> StockCatalystAnalysis:
        mag: PriceImpactMagnitude = "MODERATE_DROP_MINUS_5_TO_15_PCT"
        est_move = -14.0
        if any(w in text for w in ["fraud", "hindenburg", "doj probe"]):
            mag = "CATASTROPHIC_CRASH_MINUS_15_PCT"
            est_move = -24.0

        return StockCatalystAnalysis(
            symbol=sym,
            headline=headline,
            catalyst_type="FORENSIC_FRAUD_EXECUTIVE_CRISIS",
            price_impact_magnitude=mag,
            estimated_price_move_pct=est_move,
            time_horizon="SWING_MOMENTUM_1_TO_4_WEEKS",
            confidence_score_pct=90.0,
            core_driver_summary=f"Short-seller forensic report or regulatory investigation questions {sym} financial integrity.",
            fundamental_mechanics="Institutional funds liquidate due to ESG and governance compliance mandates.",
            tactical_playbook="BUY_PROTECTIVE_PUT_OPTIONS",
            options_iv_impact="IV_EXPANSION_SPIKE",
            peer_contagion_effects=[
                PeerSectorContagion(peer_symbol="SECTOR_ETF", contagion_direction="BEARISH_SYMPATHY", expected_sympathy_move_pct=-1.5, rationale="Accounting scrutiny extends across industry peers.")
            ],
            risk_factors=["Potential financial restatements", "Class action litigation and debt covenant default"],
        )

    def _analyze_m_and_a(self, sym: str, headline: str, text: str) -> StockCatalystAnalysis:
        is_buyout = any(w in text for w in ["buyout", "acquire", "takeover", "tender offer"])
        if is_buyout:
            mag: PriceImpactMagnitude = "MASSIVE_SURGE_PLUS_15_PCT"
            est_move = 18.0
            playbook: TacticalPlaybookAction = "FADE_OVERBOUGHT_EUPHORIA_SHORT" if "hostile" in text else "AGGRESSIVE_LONG_GAP_AND_GO"
            summary = f"Acquisition tender offer priced at significant premium above {sym} current market price."
            mechanics = "Arbitrage spread pins stock price near proposed cash/stock offer value."
        else:
            mag = "MODERATE_SURGE_PLUS_5_TO_15_PCT"
            est_move = 8.5
            playbook = "BUY_THE_DIP_ON_OVERREACTION"
            summary = f"Activist investor builds stake in {sym} advocating strategic review and cost discipline."
            mechanics = "Activist pressure forces shareholder-friendly capital return and margin enhancement."

        return StockCatalystAnalysis(
            symbol=sym,
            headline=headline,
            catalyst_type="M_AND_A_TAKEOVER_ACTIVIST",
            price_impact_magnitude=mag,
            estimated_price_move_pct=est_move,
            time_horizon="SWING_MOMENTUM_1_TO_4_WEEKS",
            confidence_score_pct=92.0,
            core_driver_summary=summary,
            fundamental_mechanics=mechanics,
            tactical_playbook=playbook,
            options_iv_impact="IV_EXPANSION_SPIKE",
            peer_contagion_effects=[
                PeerSectorContagion(peer_symbol="PEER_CONSOLIDATION", contagion_direction="BULLISH_SYMPATHY", expected_sympathy_move_pct=4.0, rationale="Sector M&A multiples re-rate higher.")
            ],
            risk_factors=["FTC/DoJ antitrust regulatory challenges", "Deal financing terms"],
        )

    def _analyze_major_contracts(self, sym: str, headline: str, text: str) -> StockCatalystAnalysis:
        return StockCatalystAnalysis(
            symbol=sym,
            headline=headline,
            catalyst_type="MAJOR_CONTRACT_PARTNERSHIP",
            price_impact_magnitude="MODERATE_SURGE_PLUS_5_TO_15_PCT",
            estimated_price_move_pct=9.5,
            time_horizon="STRUCTURAL_MULTI_QUARTER_SHIFT",
            confidence_score_pct=88.0,
            core_driver_summary=f"Secured mega-commercial contract providing multi-year revenue visibility for {sym}.",
            fundamental_mechanics="Backlog growth guarantees future earnings visibility and raises FY revenue guidance floor.",
            tactical_playbook="AGGRESSIVE_LONG_GAP_AND_GO",
            options_iv_impact="STABLE_IV",
            peer_contagion_effects=[
                PeerSectorContagion(peer_symbol="COMPETITOR", contagion_direction="BEARISH_SYMPATHY", expected_sympathy_move_pct=-3.0, rationale="Lost tender bid reduces market share.")
            ],
            risk_factors=["Execution timeline and contract margin profitability"],
        )

    def _analyze_capital_structure(self, sym: str, headline: str, text: str) -> StockCatalystAnalysis:
        is_buyback = any(w in text for w in ["buyback authorization", "share repurchase"])
        if is_buyback:
            mag: PriceImpactMagnitude = "MILD_POSITIVE_PLUS_1_TO_5_PCT"
            est_move = 4.2
            playbook: TacticalPlaybookAction = "BUY_THE_DIP_ON_OVERREACTION"
            summary = f"{sym} authorizes major share buyback program, reducing shares outstanding."
            mechanics = "EPS accretive reduction of share float creates steady corporate demand floor."
        else:
            mag = "MODERATE_DROP_MINUS_5_TO_15_PCT"
            est_move = -7.5
            playbook = "WAIT_FOR_VOLATILITY_CRUSH"
            summary = f"{sym} announces secondary public equity offering or convertible debt, diluting shareholders."
            mechanics = "Supply expansion dilutes existing EPS and creates short-term institutional selling overhang."

        return StockCatalystAnalysis(
            symbol=sym,
            headline=headline,
            catalyst_type="CAPITAL_DILUTION_OR_BUYBACK",
            price_impact_magnitude=mag,
            estimated_price_move_pct=est_move,
            time_horizon="SWING_MOMENTUM_1_TO_4_WEEKS",
            confidence_score_pct=90.0,
            core_driver_summary=summary,
            fundamental_mechanics=mechanics,
            tactical_playbook=playbook,
            options_iv_impact="STABLE_IV",
            peer_contagion_effects=[],
            risk_factors=["Dilution discount pricing vs market spot"],
        )

    def _analyze_earnings_guidance(self, sym: str, headline: str, text: str) -> StockCatalystAnalysis:
        is_beat = any(w in text for w in ["beat", "record revenue", "raises guidance", "surges", "upgraded"])
        is_miss = any(w in text for w in ["miss", "cuts guidance", "profit warning", "disappoints", "slump"])

        if is_beat:
            mag: PriceImpactMagnitude = "MODERATE_SURGE_PLUS_5_TO_15_PCT"
            est_move = 8.8
            playbook: TacticalPlaybookAction = "AGGRESSIVE_LONG_GAP_AND_GO"
            summary = f"{sym} beats quarterly estimates and raises forward annual guidance."
            mechanics = "Operating leverage accelerates earnings growth ahead of Wall Street consensus."
        elif is_miss:
            mag = "MODERATE_DROP_MINUS_5_TO_15_PCT"
            est_move = -11.5
            playbook = "IMMEDIATE_EXIT_AND_STOP_LOSS"
            summary = f"{sym} reports disappointing quarterly results and downgrades forward guidance."
            mechanics = "Gross margin compression and slowing customer acquisition reduce forward discounted cash flows."
        else:
            mag = "NEUTRAL_RANGEBOUND"
            est_move = 0.5
            playbook = "WAIT_FOR_VOLATILITY_CRUSH"
            summary = f"{sym} earnings performance tracks in-line with consensus expectations."
            mechanics = "No significant divergence from existing market pricing expectations."

        return StockCatalystAnalysis(
            symbol=sym,
            headline=headline,
            catalyst_type="EARNINGS_AND_GUIDANCE_SHOCK",
            price_impact_magnitude=mag,
            estimated_price_move_pct=est_move,
            time_horizon="SWING_MOMENTUM_1_TO_4_WEEKS",
            confidence_score_pct=89.0,
            core_driver_summary=summary,
            fundamental_mechanics=mechanics,
            tactical_playbook=playbook,
            options_iv_impact="IV_CRUSH_AFTER_EVENT",
            peer_contagion_effects=[
                PeerSectorContagion(peer_symbol="SECTOR_PEER", contagion_direction="BULLISH_SYMPATHY" if is_beat else "BEARISH_SYMPATHY", expected_sympathy_move_pct=2.0 if is_beat else -2.0, rationale="Industry demand benchmark reflection.")
            ],
            risk_factors=["Broader macro multiple compression", "Post-earnings IV crush"],
        )
