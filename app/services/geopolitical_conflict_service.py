"""Geopolitical Conflict, War & Middle East Macro Transmission Service.

Analyzes military escalation, Iran-Israel conflicts, Strait of Hormuz shipping,
and computes impact on Crude Oil (USO/Brent), Gold (GLD), Defense (LMT/RTX), and SPY.
"""
from typing import Dict, Optional
import structlog

from app.models.deep_event_intelligence import GeopoliticalConflictImpact

logger = structlog.get_logger("geopolitical_service")


class GeopoliticalConflictService:
    def analyze_conflict_headline(self, headline: str, raw_text: str = "") -> GeopoliticalConflictImpact:
        """Parses war and political tension news to calculate commodity and equity shocks."""
        text = f"{headline} {raw_text}".lower()

        # 1. Middle East (Iran / Israel / Hormuz / Red Sea)
        if any(w in text for w in ["iran", "israel", "missile", "strike", "airstrike", "hormuz", "red sea", "gaza", "lebanon", "hezbollah", "houthi"]):
            theater = "MIDDLE_EAST_IRAN_ISRAEL"
            if any(w in text for w in ["missile attack", "strikes", "bombardment", "direct hit", "retaliation launched"]):
                escalation = "DEFCON_1_ACTIVE_MISSILE_STRIKE_WAR"
                oil_shock = "SPIKE_SURGE"
                gold_status = "MAXIMUM_ACCUMULATION"
                defense_outlook = "STRONG_SURGE"
                market_posture = "RISK_OFF_FLIGHT_TO_SAFETY"
                assets = {
                    "USO": "Brent crude spikes on Strait of Hormuz oil transit blockade risk.",
                    "GLD": "Surge in physical gold safe-haven accumulation.",
                    "LMT": "Lockheed Martin surges on US DoD missile replenishment procurement.",
                    "RTX": "Raytheon surges on Patriot and Iron Dome air-defense orders.",
                    "SPY": "Broad equity sell-off on global energy inflation fears.",
                }
            elif any(w in text for w in ["imminent", "threatens", "prepares attack", "warning"]):
                escalation = "DEFCON_2_IMMINENT_RETALIATION_ALERT"
                oil_shock = "MODERATE_RISE"
                gold_status = "MODERATE_INFLOW"
                defense_outlook = "STRONG_SURGE"
                market_posture = "CONTROLLED_DIP"
                assets = {
                    "USO": "Oil risk premium expands by $3-$5 per barrel.",
                    "GLD": "Preemptive safe haven hedging flows.",
                    "LMT": "Defense aerospace sector relative strength outperformance.",
                    "SPY": "Mild flight to cash.",
                }
            else:
                escalation = "DEFCON_3_DIPLOMATIC_STANDOFF_SANCTIONS"
                oil_shock = "STABLE"
                gold_status = "NEUTRAL"
                defense_outlook = "NEUTRAL"
                market_posture = "IGNORING_HEADLINE"
                assets = {"SPY": "Market prices in baseline diplomatic status quo."}

        # 2. Taiwan Strait / Chip Supply Bottleneck
        elif any(w in text for w in ["taiwan", "china military exercise", "taiwan strait", "blockade", "tsmc war"]):
            theater = "TAIWAN_STRAIT"
            escalation = "DEFCON_2_IMMINENT_RETALIATION_ALERT"
            oil_shock = "MODERATE_RISE"
            gold_status = "MAXIMUM_ACCUMULATION"
            defense_outlook = "STRONG_SURGE"
            market_posture = "RISK_OFF_FLIGHT_TO_SAFETY"
            assets = {
                "TSM": "Catastrophic semiconductor fabrication interruption risk.",
                "NVDA": "Severe GPU supply bottleneck fears.",
                "GLD": "Capital preservation flight.",
                "SPY": "Tech heavy equity benchmark drawdown.",
            }

        # 3. Eastern Europe (Ukraine / Russia / NATO)
        else:
            theater = "EASTERN_EUROPE"
            escalation = "DEFCON_3_DIPLOMATIC_STANDOFF_SANCTIONS"
            oil_shock = "MODERATE_RISE"
            gold_status = "MODERATE_INFLOW"
            defense_outlook = "STRONG_SURGE"
            market_posture = "CONTROLLED_DIP"
            assets = {
                "USO": "European energy supply disruption risk.",
                "LMT": "NATO allied defense replenishment orders.",
            }

        return GeopoliticalConflictImpact(
            conflict_theater=theater,
            escalation_status=escalation,
            crude_oil_shock_direction=oil_shock,
            gold_safe_haven_status=gold_status,
            defense_stocks_outlook=defense_outlook,
            broad_market_posture=market_posture,
            affected_assets=assets,
        )
