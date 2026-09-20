"""Multi-Agent Decision & Debate Service.

Orchestrates 4 specialized analyst agents + 1 synthesizer:
1. Macro Analyst: Evaluates interest rates, market regime, sector momentum.
2. Fundamental Analyst: Evaluates valuation multiples, debt ratios, revenue growth.
3. Technical Analyst: Evaluates trend, momentum oscillators, support & resistance.
4. Risk Manager: Strictly evaluates downside protection, drawdown risk, and holds VETO power.
5. Consensus Synthesizer: Weighs individual viewpoints, resolves conflicts, and generates execution plans.
"""
from typing import Optional
import structlog

from app.core.config import Settings
from app.models.multi_agent import (
    ActionablePlan,
    AgentOpinion,
    MultiAgentConsensus,
    MultiAgentDebateRequest,
)

logger = structlog.get_logger("multi_agent_service")


class MultiAgentService:
    def __init__(self, settings: Settings, ai_service: Optional[object] = None) -> None:
        self._settings = settings
        self._ai_service = ai_service

    async def deliberate(
        self,
        symbol: str,
        quote: dict,
        indicators: dict,
        fundamentals: Optional[dict] = None,
        events: Optional[list] = None,
        risk_tolerance: str = "moderate",
    ) -> MultiAgentConsensus:
        """Run the multi-agent deliberation cycle."""
        sym = symbol.upper().strip()
        price = float(quote.get("price") or quote.get("close") or 100.0)
        rsi = float(indicators.get("rsi") or 50.0)
        macd_hist = float(indicators.get("macd_hist") or 0.0)
        sma20 = float(indicators.get("sma20") or price)
        sma50 = float(indicators.get("sma50") or price)
        pe = float((fundamentals or {}).get("pe_ratio") or 22.0)
        debt_to_equity = float((fundamentals or {}).get("debt_to_equity") or 1.1)

        # 1. Macro Analyst
        macro_opinion = self._eval_macro(sym, quote, events)

        # 2. Fundamental Analyst
        fund_opinion = self._eval_fundamental(sym, pe, debt_to_equity, fundamentals)

        # 3. Technical Analyst
        tech_opinion = self._eval_technical(sym, price, rsi, macd_hist, sma20, sma50)

        # 4. Risk Manager (Has VETO authority)
        risk_opinion = self._eval_risk(
            sym, price, rsi, risk_tolerance, macro_opinion, tech_opinion
        )

        # 5. Consensus Synthesis
        opinions = {
            "macro_analyst": macro_opinion,
            "fundamental_analyst": fund_opinion,
            "technical_analyst": tech_opinion,
            "risk_manager": risk_opinion,
        }

        consensus = self._synthesize_consensus(
            sym, price, opinions, risk_tolerance
        )
        return consensus

    def _eval_macro(self, symbol: str, quote: dict, events: Optional[list]) -> AgentOpinion:
        # Check event risk
        high_impact_events = [e for e in (events or []) if e.get("impact") == "HIGH"]
        if high_impact_events:
            event_titles = ", ".join(e.get("title", "") for e in high_impact_events[:2])
            return AgentOpinion(
                role="macro_analyst",
                name="Dr. Marcus Vance (Macro & Rates)",
                action="HOLD",
                confidence=0.65,
                thesis=f"Elevated macro uncertainty detected due to pending high-impact catalysts: {event_titles}.",
                key_metrics={"macro_risk_level": "ELEVATED", "regime": "Defensive"},
                risks_flagged=["High-impact scheduled event risk", "Volatility expansion likely"],
            )

        change_pct = float(quote.get("change_pct") or 0.0)
        if change_pct > 1.5:
            return AgentOpinion(
                role="macro_analyst",
                name="Dr. Marcus Vance (Macro & Rates)",
                action="BUY",
                confidence=0.72,
                thesis="Macro regime is risk-on with positive liquidity tailwinds and constructive sector momentum.",
                key_metrics={"macro_risk_level": "MODERATE", "regime": "Expansionary"},
                risks_flagged=[],
            )
        elif change_pct < -2.0:
            return AgentOpinion(
                role="macro_analyst",
                name="Dr. Marcus Vance (Macro & Rates)",
                action="SELL",
                confidence=0.70,
                thesis="Broad macro drag and risk-off rotation currently penalizing beta assets.",
                key_metrics={"macro_risk_level": "HIGH", "regime": "Contractionary"},
                risks_flagged=["Systemic drawdown pressure", "Capital flight to defensive assets"],
            )
        return AgentOpinion(
            role="macro_analyst",
            name="Dr. Marcus Vance (Macro & Rates)",
            action="HOLD",
            confidence=0.60,
            thesis="Neutral macro environment. Yield curves and liquidity conditions are balanced.",
            key_metrics={"macro_risk_level": "NEUTRAL", "regime": "Consolidation"},
            risks_flagged=[],
        )

    def _eval_fundamental(
        self, symbol: str, pe: float, debt_to_equity: float, fundamentals: Optional[dict]
    ) -> AgentOpinion:
        metrics = {
            "p_e_ratio": f"{pe:.1f}",
            "debt_to_equity": f"{debt_to_equity:.2f}",
        }
        if pe < 18.0 and debt_to_equity < 1.5:
            return AgentOpinion(
                role="fundamental_analyst",
                name="Elena Rostova (Value & Capital Structure)",
                action="BUY",
                confidence=0.78,
                thesis=f"Attractive valuation with healthy balance sheet (P/E {pe:.1f} vs industry, D/E {debt_to_equity:.2f}).",
                key_metrics=metrics,
                risks_flagged=[],
            )
        elif pe > 45.0 or debt_to_equity > 3.0:
            risks = []
            if pe > 45.0:
                risks.append(f"Excessive multiple valuation stretch (P/E: {pe:.1f})")
            if debt_to_equity > 3.0:
                risks.append(f"Highly leveraged balance sheet (D/E: {debt_to_equity:.2f})")
            return AgentOpinion(
                role="fundamental_analyst",
                name="Elena Rostova (Value & Capital Structure)",
                action="SELL",
                confidence=0.74,
                thesis="Valuation multiple leaves minimal margin of safety with elevated debt overhang.",
                key_metrics=metrics,
                risks_flagged=risks,
            )
        return AgentOpinion(
            role="fundamental_analyst",
            name="Elena Rostova (Value & Capital Structure)",
            action="HOLD",
            confidence=0.62,
            thesis=f"Fairly valued relative to current earnings power and cash generation profile (P/E {pe:.1f}).",
            key_metrics=metrics,
            risks_flagged=[],
        )

    def _eval_technical(
        self, symbol: str, price: float, rsi: float, macd_hist: float, sma20: float, sma50: float
    ) -> AgentOpinion:
        metrics = {
            "rsi": f"{rsi:.1f}",
            "macd_histogram": f"{macd_hist:.3f}",
            "sma20_diff_pct": f"{((price - sma20) / sma20 * 100):+.2f}%",
        }
        if rsi < 35.0 and macd_hist >= 0.0:
            return AgentOpinion(
                role="technical_analyst",
                name="Kai Tanaka (Technical & Price Action)",
                action="BUY",
                confidence=0.82,
                thesis=f"Oversold bounce setup with bullish MACD histogram divergence (RSI {rsi:.1f}).",
                key_metrics=metrics,
                risks_flagged=[],
            )
        elif rsi > 70.0 and macd_hist < 0.0:
            return AgentOpinion(
                role="technical_analyst",
                name="Kai Tanaka (Technical & Price Action)",
                action="SELL",
                confidence=0.80,
                thesis=f"Overbought exhaustion near resistance with negative momentum divergence (RSI {rsi:.1f}).",
                key_metrics=metrics,
                risks_flagged=["Exhaustion gap risk", "Momentum deceleration"],
            )
        elif price > sma20 > sma50:
            return AgentOpinion(
                role="technical_analyst",
                name="Kai Tanaka (Technical & Price Action)",
                action="BUY",
                confidence=0.73,
                thesis="Strong bullish moving average alignment with constructive consolidation above 20-day SMA.",
                key_metrics=metrics,
                risks_flagged=[],
            )
        elif price < sma20 < sma50:
            return AgentOpinion(
                role="technical_analyst",
                name="Kai Tanaka (Technical & Price Action)",
                action="SELL",
                confidence=0.71,
                thesis="Bearish moving average stack and downward trending channel.",
                key_metrics=metrics,
                risks_flagged=["Falling knife risk", "Resistance overhead"],
            )
        return AgentOpinion(
            role="technical_analyst",
            name="Kai Tanaka (Technical & Price Action)",
            action="HOLD",
            confidence=0.55,
            thesis="Rangebound price action without a high-probability directional breakout trigger.",
            key_metrics=metrics,
            risks_flagged=[],
        )

    def _eval_risk(
        self,
        symbol: str,
        price: float,
        rsi: float,
        risk_tolerance: str,
        macro: AgentOpinion,
        tech: AgentOpinion,
    ) -> AgentOpinion:
        risks = []
        is_veto = False
        veto_reason = None

        # Risk Manager vetoes if market is violently overbought/oversold under conservative risk,
        # or if macro has severe risk
        if rsi > 80.0:
            is_veto = True
            veto_reason = f"Extreme parabolic RSI ({rsi:.1f}) exceeds risk safety boundary; vetoing long entries."
            risks.append(veto_reason)
        elif macro.key_metrics.get("macro_risk_level") == "HIGH" and risk_tolerance == "conservative":
            is_veto = True
            veto_reason = "Conservative risk mandate mandates capital preservation during HIGH macro risk regime."
            risks.append(veto_reason)

        action = "HOLD" if is_veto else ("BUY" if tech.action == "BUY" and macro.action != "SELL" else "HOLD")
        confidence = 0.90 if is_veto else 0.70

        return AgentOpinion(
            role="risk_manager",
            name="Chief Risk Officer (Veto Authority)",
            action=action,
            confidence=confidence,
            thesis="Strict capital preservation and volatility budgeting audit." + (f" VETO TRIGGERED: {veto_reason}" if is_veto else " Risk limits within acceptable bounds."),
            key_metrics={
                "risk_tolerance": risk_tolerance.upper(),
                "veto_active": str(is_veto).upper(),
                "max_drawdown_limit": "2.5%" if risk_tolerance == "conservative" else "5.0%",
            },
            risks_flagged=risks,
            veto=is_veto,
            veto_reason=veto_reason,
        )

    def _synthesize_consensus(
        self,
        symbol: str,
        price: float,
        opinions: dict,
        risk_tolerance: str,
    ) -> MultiAgentConsensus:
        risk_op: AgentOpinion = opinions["risk_manager"]
        macro_op: AgentOpinion = opinions["macro_analyst"]
        fund_op: AgentOpinion = opinions["fundamental_analyst"]
        tech_op: AgentOpinion = opinions["technical_analyst"]

        # If risk manager vetoed, result MUST be HOLD or SELL
        if risk_op.veto:
            action = "HOLD"
            consensus_strength = "vetoed"
            final_conf = min(0.40, risk_op.confidence * 0.5)
            summary = (
                f"RISK VETO ENFORCED by Chief Risk Officer: {risk_op.veto_reason} "
                "Deliberation concluded with defensive HOLD to protect capital."
            )
            stop_loss = round(price * 0.97, 2)
            take_profit = round(price * 1.05, 2)
            sizing = 0.0
        else:
            votes = [macro_op.action, fund_op.action, tech_op.action]
            buy_count = votes.count("BUY")
            sell_count = votes.count("SELL")

            if buy_count >= 2:
                action = "BUY"
                consensus_strength = "unanimous" if buy_count == 3 else "majority"
                avg_conf = (macro_op.confidence + fund_op.confidence + tech_op.confidence) / 3.0
                final_conf = round(avg_conf, 2)
                summary = (
                    f"Bullish consensus ({buy_count}/3 analysts positive). "
                    f"Technical breakout supported by {fund_op.thesis[:80]}..."
                )
                stop_loss = round(price * 0.95, 2)
                take_profit = round(price * 1.10, 2)
                sizing = 7.5 if risk_tolerance == "aggressive" else (5.0 if risk_tolerance == "moderate" else 2.5)
            elif sell_count >= 2:
                action = "SELL"
                consensus_strength = "unanimous" if sell_count == 3 else "majority"
                avg_conf = (macro_op.confidence + fund_op.confidence + tech_op.confidence) / 3.0
                final_conf = round(avg_conf, 2)
                summary = (
                    f"Bearish consensus ({sell_count}/3 analysts negative). "
                    "Downside risks dominate valuation and technical momentum."
                )
                stop_loss = round(price * 1.05, 2)
                take_profit = round(price * 0.90, 2)
                sizing = 0.0
            else:
                action = "HOLD"
                consensus_strength = "split"
                final_conf = 0.50
                summary = (
                    "Split committee debate without clear directional conviction. "
                    "Macro, fundamental, and technical signals are diverging."
                )
                stop_loss = round(price * 0.96, 2)
                take_profit = round(price * 1.06, 2)
                sizing = 2.0

        rr_ratio = None
        if stop_loss and take_profit and price > 0:
            risk_amt = abs(price - stop_loss)
            reward_amt = abs(take_profit - price)
            if risk_amt > 0:
                rr_ratio = round(reward_amt / risk_amt, 2)

        plan = ActionablePlan(
            suggested_entry=price,
            suggested_stop_loss=stop_loss,
            suggested_take_profit=take_profit,
            suggested_position_size_pct=sizing,
            time_horizon="swing_1_5d",
            risk_reward_ratio=rr_ratio,
        )

        return MultiAgentConsensus(
            symbol=symbol,
            final_action=action,
            final_confidence=final_conf,
            consensus_strength=consensus_strength,
            risk_veto_applied=risk_op.veto,
            opinions=opinions,
            deliberation_summary=summary,
            actionable_plan=plan,
            model_used="multi-agent-committee-v2",
        )
