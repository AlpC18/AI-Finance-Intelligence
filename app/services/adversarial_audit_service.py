"""Adversarial Red-Team / Blue-Team Audit Service.

Conducts adversarial cross-examination before trade proposals are dispatched
to the portfolio manager or broker.
"""
from typing import Optional
import structlog

from app.models.adversarial import (
    AdversarialAuditReport,
    BlueTeamArgument,
    RedTeamChallenge,
)

logger = structlog.get_logger("adversarial_audit_service")


class AdversarialAuditService:
    def audit_trade_proposal(
        self,
        symbol: str,
        action: str,
        price: float,
        rsi: float,
        pe_ratio: float,
        debt_to_equity: float,
        recent_news_sentiment: float = 0.0,
    ) -> AdversarialAuditReport:
        """Runs adversarial red-team audit on a proposed trade."""
        sym = symbol.upper().strip()

        # 1. Blue Team Case (Bull Prosecution)
        upside_catalysts = [
            "Positive sector momentum and relative strength",
            "Constructive moving average support consolidation",
        ]
        if recent_news_sentiment > 0.1:
            upside_catalysts.append("Favorable headline and earnings sentiment backdrop")

        blue_conviction = min(95.0, max(30.0, 70.0 + (recent_news_sentiment * 30.0)))
        target_p = round(price * (1.10 if action == "BUY" else 0.90), 2)

        blue_team = BlueTeamArgument(
            upside_catalysts=upside_catalysts,
            technical_triggers=[f"RSI level at {rsi:.1f}", "Breakout setup across primary timeframes"],
            growth_thesis=f"High-conviction {action} setup targeting ${target_p:.2f} with favorable momentum.",
            target_price=target_p,
            conviction_score=round(blue_conviction, 1),
        )

        # 2. Red Team Challenge (Bear Opposition)
        failure_modes = []
        val_risks = []
        macro_risks = ["Systemic beta correlation and broad market drawdown risk"]

        red_severity = 30.0

        if pe_ratio > 35.0:
            val_risks.append(f"Excessive P/E valuation stretch ({pe_ratio:.1f}) leaves no margin of error")
            red_severity += 25.0

        if debt_to_equity > 2.0:
            failure_modes.append(f"Elevated balance sheet debt leverage ({debt_to_equity:.2f})")
            red_severity += 20.0

        if rsi > 72.0:
            failure_modes.append(f"Overextended momentum (RSI {rsi:.1f}) vulnerable to sharp mean reversion")
            red_severity += 20.0
        elif rsi < 30.0 and action == "SELL":
            failure_modes.append("Selling into oversold capitulation with high bounce risk")
            red_severity += 20.0

        if not val_risks:
            val_risks.append("Valuation in line with industry peers")

        red_severity = min(95.0, round(red_severity, 1))

        red_team = RedTeamChallenge(
            bear_counter_thesis=(
                f"Red Team Audit Flags: {len(failure_modes)} critical failure modes and "
                f"{len(val_risks)} valuation vulnerability points."
            ),
            identified_failure_modes=failure_modes if failure_modes else ["General equity market volatility"],
            valuation_vulnerabilities=val_risks,
            liquidity_and_macro_risks=macro_risks,
            severity_score=red_severity,
        )

        # 3. Arbiter Ruling & Survival Score
        survival_score = max(0.0, min(100.0, blue_conviction - (red_severity * 0.6) + 20.0))
        survival_score = round(survival_score, 1)

        mitigations = []
        if red_severity >= 60.0:
            mitigations.append("Mandatory 50% position sizing reduction")
            mitigations.append("Tighten Stop-Loss to max 2.5% below entry")

        if survival_score >= 65.0:
            verdict = "APPROVED_PROCEED_TO_RISK"
            survived = True
            ruling = f"Proposal survived adversarial audit with score {survival_score}/100. Bull thesis withstands Red Team scrutiny."
        elif survival_score >= 45.0:
            verdict = "APPROVED_WITH_TIGHTER_STOPS"
            survived = True
            ruling = f"Proposal conditionally approved with score {survival_score}/100. Mandated risk mitigations must be enforced."
        else:
            verdict = "REJECTED_RED_TEAM_KILL"
            survived = False
            ruling = f"Proposal KILLED by Red Team (Survival score: {survival_score}/100). Severe failure modes ({', '.join(failure_modes[:2])}) violate safety boundaries."

        logger.info(
            "adversarial_audit_completed",
            symbol=sym,
            action=action,
            survival_score=survival_score,
            verdict=verdict,
        )

        return AdversarialAuditReport(
            symbol=sym,
            action_proposed=action,
            proposal_survived=survived,
            audit_verdict=verdict,
            survival_score_100=survival_score,
            blue_team=blue_team,
            red_team=red_team,
            arbiter_ruling=ruling,
            mandated_risk_mitigations=mitigations,
        )
