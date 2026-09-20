"""Macroeconomic, Regulatory, and Catalyst News Intelligence Service.

Extracts deep financial transmission mechanisms from breaking news headlines:
- Fed Rate Hikes/Cuts, Inflation (CPI/PCE), Hawkish/Dovish tone
- SEC/DoJ lawsuits, court verdicts, regulatory settlements
- Crypto ETF inflows, token unlocks, protocol exploits
- Generates tactical portfolio postures and multi-asset directional impacts.
"""
import re
from typing import List, Optional
import structlog

from app.models.macro_news_impact import (
    BatchNewsDigestReport,
    NewsEventImpactAnalysis,
    NewsEventType,
    SingleAssetTransmission,
)

logger = structlog.get_logger("macro_news_analyzer")


class MacroNewsAnalyzerService:
    def analyze_event(self, headline: str, raw_content: str = "") -> NewsEventImpactAnalysis:
        """Parses a news headline/article and computes multi-asset market transmission."""
        text = f"{headline} {raw_content}".lower()

        # 1. Detect Event Type & Macro Tone
        if any(w in text for w in ["fed", "federal reserve", "powell", "fomc", "interest rate", "rate cut", "rate hike", "basis points", "cpi", "inflation"]):
            return self._analyze_fed_macro_event(headline, text)

        elif any(w in text for w in ["lawsuit", "court", "judge", "settlement", "ruling", "doj", "sued", "fraud", "verdict", "injunction"]) or re.search(r"\bsec\b", text):
            return self._analyze_regulatory_legal_event(headline, text)

        elif any(w in text for w in ["defi", "crypto", "etf", "bitcoin", "ethereum", "binance", "coinbase", "halving", "token unlock", "hack", "exploit"]):
            return self._analyze_crypto_catalyst_event(headline, text)

        elif any(w in text for w in ["earnings", "revenue beat", "eps miss", "guidance cut", "profit warning", "quarterly results"]):
            return self._analyze_earnings_event(headline, text)

        elif any(w in text for w in ["tariff", "sanction", "war", "geopolitical", "trade deal", "crude oil shock"]):
            return self._analyze_geopolitical_event(headline, text)

        # Default standard market headline
        return NewsEventImpactAnalysis(
            headline=headline,
            event_type="MACRO_INFLATION_CPI_JOBS",
            macro_tone="NEUTRAL_BALANCED",
            urgency_level="STANDARD",
            confidence_score_pct=65.0,
            primary_takeaway="Standard market commentary with balanced macroeconomic implications.",
            affected_assets=[
                SingleAssetTransmission(
                    ticker_or_asset="SPY",
                    direction="NEUTRAL",
                    expected_volatility_pct=0.8,
                    transmission_channel_explanation="Broad equity benchmark absorbs baseline market flow.",
                )
            ],
            recommended_portfolio_posture="NO_ACTION_REQUIRED",
        )

    def _analyze_fed_macro_event(self, headline: str, text: str) -> NewsEventImpactAnalysis:
        is_cut = any(w in text for w in ["rate cut", "cuts rate", "slashes rates", "dovish", "easing", "inflation cools", "cpi drops"])
        is_hike = any(w in text for w in ["rate hike", "hikes rate", "hawkish", "tightening", "cpi surges", "inflation accelerates"])

        if is_cut:
            tone = "DOVISH"
            takeaway = "Dovish monetary policy stance stimulates equity valuation multiples and risk-on crypto liquidity."
            cb_impl = "Lower cost of capital decreases discount rate (WACC), driving growth tech (QQQ) and crypto expansion."
            posture = "RISK_ON_EXPAND_EQUITY_CRYPTO"
            affected = [
                SingleAssetTransmission(ticker_or_asset="QQQ", direction="STRONG_BULLISH", expected_volatility_pct=2.2, transmission_channel_explanation="Lower discount rate directly expands tech valuation multiples."),
                SingleAssetTransmission(ticker_or_asset="BTC", direction="STRONG_BULLISH", expected_volatility_pct=4.5, transmission_channel_explanation="Global M2 liquidity expansion fuels high-beta digital asset inflows."),
                SingleAssetTransmission(ticker_or_asset="DXY", direction="MODERATE_BEARISH", expected_volatility_pct=1.0, transmission_channel_explanation="Yield spread narrowing weakens US Dollar relative to basket."),
                SingleAssetTransmission(ticker_or_asset="US10Y", direction="STRONG_BEARISH", expected_volatility_pct=3.0, transmission_channel_explanation="Treasury yields fall as bond prices rally on easing."),
            ]
        elif is_hike:
            tone = "HAWKISH"
            takeaway = "Hawkish policy signals higher-for-longer interest rates, compressing equity multiples and draining speculative liquidity."
            cb_impl = "Higher terminal rate increases borrowing cost and strengthens USD, causing capital contraction."
            posture = "RISK_OFF_FLIGHT_TO_CASH_BONDS"
            affected = [
                SingleAssetTransmission(ticker_or_asset="QQQ", direction="STRONG_BEARISH", expected_volatility_pct=2.5, transmission_channel_explanation="High-duration growth tech shares suffer multiple compression."),
                SingleAssetTransmission(ticker_or_asset="BTC", direction="STRONG_BEARISH", expected_volatility_pct=5.0, transmission_channel_explanation="Liquidity withdrawal induces deleveraging across crypto ecosystem."),
                SingleAssetTransmission(ticker_or_asset="DXY", direction="STRONG_BULLISH", expected_volatility_pct=1.2, transmission_channel_explanation="Higher US interest rate differential attracts capital into USD."),
                SingleAssetTransmission(ticker_or_asset="US10Y", direction="STRONG_BULLISH", expected_volatility_pct=3.5, transmission_channel_explanation="Sovereign yields rise on aggressive rate trajectory."),
            ]
        else:
            tone = "NEUTRAL_BALANCED"
            takeaway = "Federal Reserve holds policy rate steady, emphasizing data-dependency."
            cb_impl = "Policy remains restrictive but stable; watching upcoming CPI and labor prints."
            posture = "NO_ACTION_REQUIRED"
            affected = [
                SingleAssetTransmission(ticker_or_asset="SPY", direction="NEUTRAL", expected_volatility_pct=1.0, transmission_channel_explanation="Market prices in baseline plateau scenario."),
                SingleAssetTransmission(ticker_or_asset="BTC", direction="NEUTRAL", expected_volatility_pct=2.0, transmission_channel_explanation="Consolidation within existing trading range."),
            ]

        return NewsEventImpactAnalysis(
            headline=headline,
            event_type="FED_CENTRAL_BANK_DECISION",
            macro_tone=tone,
            urgency_level="BREAKING_URGENT",
            confidence_score_pct=92.0,
            primary_takeaway=takeaway,
            central_bank_implications=cb_impl,
            affected_assets=affected,
            recommended_portfolio_posture=posture,
            automated_hedging_mandate="Rebalance target equity duration according to interest rate regime." if is_hike else None,
        )

    def _analyze_regulatory_legal_event(self, headline: str, text: str) -> NewsEventImpactAnalysis:
        is_victory = any(w in text for w in ["wins", "dismissed", "victory", "approved", "cleared", "favorable ruling"])
        is_loss = any(w in text for w in ["guilty", "fine", "sued", "injunction", "criminal charges", "ban", "illegal"])

        target_ticker = "COIN" if "coinbase" in text else ("XRP" if "ripple" in text else ("GOOGL" if "google" in text or "antitrust" in text else "CRYPTO_SECTOR"))

        if is_victory:
            severity = "BENIGN_DISMISSED"
            takeaway = f"Legal victory/dismissal eliminates regulatory overhang for {target_ticker}."
            posture = "TACTICAL_LONG_SPECIFIC_CATALYST"
            affected = [
                SingleAssetTransmission(ticker_or_asset=target_ticker, direction="STRONG_BULLISH", expected_volatility_pct=8.5, transmission_channel_explanation="Elimination of existential regulatory cloud triggers massive short squeeze."),
                SingleAssetTransmission(ticker_or_asset="BTC", direction="MODERATE_BULLISH", expected_volatility_pct=3.0, transmission_channel_explanation="Regulatory clarity boosts institutional risk appetite."),
            ]
        elif is_loss:
            severity = "BUSINESS_MODEL_RISK" if any(w in text for w in ["structural", "breakup", "ban", "fraud"]) else "FINES_ABSORBABLE"
            takeaway = f"Regulatory enforcement action or adverse court ruling impacts {target_ticker} operations."
            posture = "DELTA_HEDGE_VOLATILITY"
            affected = [
                SingleAssetTransmission(ticker_or_asset=target_ticker, direction="STRONG_BEARISH", expected_volatility_pct=9.0, transmission_channel_explanation="Legal uncertainty and prospective operational fines trigger institutional divestment."),
                SingleAssetTransmission(ticker_or_asset="SPY", direction="MODERATE_BEARISH", expected_volatility_pct=1.2, transmission_channel_explanation="Headline regulatory risk introduces broad market friction."),
            ]
        else:
            severity = "FINES_ABSORBABLE"
            takeaway = "Ongoing regulatory proceeding enters cross-examination phase."
            posture = "NO_ACTION_REQUIRED"
            affected = [SingleAssetTransmission(ticker_or_asset=target_ticker, direction="NEUTRAL", expected_volatility_pct=3.0, transmission_channel_explanation="Market awaiting binding judicial order.")]

        return NewsEventImpactAnalysis(
            headline=headline,
            event_type="REGULATORY_LAWSUIT_RULING",
            macro_tone="NEUTRAL_BALANCED" if is_victory else "CRISIS_PANIC",
            urgency_level="BREAKING_URGENT",
            confidence_score_pct=88.0,
            primary_takeaway=takeaway,
            legal_verdict_severity=severity,
            affected_assets=affected,
            recommended_portfolio_posture=posture,
            automated_hedging_mandate=f"Enforce protective put options or reduce sizing on {target_ticker}." if is_loss else None,
        )

    def _analyze_crypto_catalyst_event(self, headline: str, text: str) -> NewsEventImpactAnalysis:
        is_bull = any(w in text for w in ["etf approved", "record inflow", "institutional adoption", "reserve asset"])
        is_bear = any(w in text for w in ["hack", "exploit", "stolen", "ban", "freeze", "insolvent"])

        if is_bull:
            takeaway = "Institutional crypto ETF inflows or structural adoption catalyst."
            posture = "RISK_ON_EXPAND_EQUITY_CRYPTO"
            affected = [
                SingleAssetTransmission(ticker_or_asset="BTC", direction="STRONG_BULLISH", expected_volatility_pct=5.5, transmission_channel_explanation="Direct institutional spot demand absorption."),
                SingleAssetTransmission(ticker_or_asset="ETH", direction="STRONG_BULLISH", expected_volatility_pct=6.5, transmission_channel_explanation="Ecosystem beta expansion and DeFi liquidity surge."),
                SingleAssetTransmission(ticker_or_asset="COIN", direction="STRONG_BULLISH", expected_volatility_pct=7.0, transmission_channel_explanation="Exchange fee revenue and custody AUM surge."),
            ]
        elif is_bear:
            takeaway = "Crypto protocol exploit, exchange liquidity distress, or enforcement clampdown."
            posture = "DELTA_HEDGE_VOLATILITY"
            affected = [
                SingleAssetTransmission(ticker_or_asset="BTC", direction="STRONG_BEARISH", expected_volatility_pct=6.0, transmission_channel_explanation="Contagion fears trigger rapid risk-off capital withdrawal."),
                SingleAssetTransmission(ticker_or_asset="ETH", direction="STRONG_BEARISH", expected_volatility_pct=8.0, transmission_channel_explanation="DeFi TVL unwinding and smart contract risk escalation."),
            ]
        else:
            takeaway = "Routine crypto market flow and network metric updates."
            posture = "NO_ACTION_REQUIRED"
            affected = [SingleAssetTransmission(ticker_or_asset="BTC", direction="NEUTRAL", expected_volatility_pct=2.5, transmission_channel_explanation="Baseline on-chain activity.")]

        return NewsEventImpactAnalysis(
            headline=headline,
            event_type="CRYPTO_ETF_AND_REGULATORY",
            macro_tone="DOVISH" if is_bull else "CRISIS_PANIC",
            urgency_level="HIGH_PRIORITY",
            confidence_score_pct=85.0,
            primary_takeaway=takeaway,
            affected_assets=affected,
            recommended_portfolio_posture=posture,
        )

    def _analyze_earnings_event(self, headline: str, text: str) -> NewsEventImpactAnalysis:
        is_beat = any(w in text for w in ["beat", "record revenue", "raises guidance", "surges", "buyback"])
        is_miss = any(w in text for w in ["miss", "cuts guidance", "profit drop", "disappoints", "slumps"])

        ticker = "TECH_EQUITIES"
        # Extract potential ticker if uppercase 2-5 letter word exists
        match = re.search(r"\b[A-Z]{2,5}\b", headline)
        if match:
            ticker = match.group(0)

        if is_beat:
            takeaway = f"Earnings beat and constructive forward guidance for {ticker}."
            posture = "TACTICAL_LONG_SPECIFIC_CATALYST"
            affected = [
                SingleAssetTransmission(ticker_or_asset=ticker, direction="STRONG_BULLISH", expected_volatility_pct=5.5, transmission_channel_explanation="Fundamental EPS expansion supports valuation."),
                SingleAssetTransmission(ticker_or_asset="SPY", direction="MODERATE_BULLISH", expected_volatility_pct=0.9, transmission_channel_explanation="Corporate profit resilience reinforces equity indices."),
            ]
        elif is_miss:
            takeaway = f"Earnings disappointment and decelerating forward guidance for {ticker}."
            posture = "DELTA_HEDGE_VOLATILITY"
            affected = [
                SingleAssetTransmission(ticker_or_asset=ticker, direction="STRONG_BEARISH", expected_volatility_pct=7.5, transmission_channel_explanation="Guidance downgrade forces Wall Street analysts to cut price targets."),
                SingleAssetTransmission(ticker_or_asset="SPY", direction="MODERATE_BEARISH", expected_volatility_pct=1.1, transmission_channel_explanation="Margin compression risk across constituent stocks."),
            ]
        else:
            takeaway = "Earnings announcement within expected consensus range."
            posture = "NO_ACTION_REQUIRED"
            affected = [SingleAssetTransmission(ticker_or_asset=ticker, direction="NEUTRAL", expected_volatility_pct=2.0, transmission_channel_explanation="Neutral reaction.")]

        return NewsEventImpactAnalysis(
            headline=headline,
            event_type="EARNINGS_SHOCK_GUIDANCE",
            macro_tone="NEUTRAL_BALANCED",
            urgency_level="HIGH_PRIORITY",
            confidence_score_pct=86.0,
            primary_takeaway=takeaway,
            affected_assets=affected,
            recommended_portfolio_posture=posture,
        )

    def _analyze_geopolitical_event(self, headline: str, text: str) -> NewsEventImpactAnalysis:
        takeaway = "Geopolitical trade tariffs, sanctions, or international supply disruption."
        return NewsEventImpactAnalysis(
            headline=headline,
            event_type="GEOPOLITICAL_SANCTION_TARIFF",
            macro_tone="CRISIS_PANIC",
            urgency_level="BREAKING_URGENT",
            confidence_score_pct=80.0,
            primary_takeaway=takeaway,
            affected_assets=[
                SingleAssetTransmission(ticker_or_asset="SPY", direction="MODERATE_BEARISH", expected_volatility_pct=1.8, transmission_channel_explanation="Supply chain friction and tariff costs compress corporate gross margins."),
                SingleAssetTransmission(ticker_or_asset="GLD", direction="STRONG_BULLISH", expected_volatility_pct=2.2, transmission_channel_explanation="Safe-haven gold accumulation during geopolitical tension."),
                SingleAssetTransmission(ticker_or_asset="BTC", direction="MODERATE_BULLISH", expected_volatility_pct=4.0, transmission_channel_explanation="Non-sovereign digital gold alternative narrative."),
            ],
            recommended_portfolio_posture="RISK_OFF_FLIGHT_TO_CASH_BONDS",
        )

    def generate_digest(self, headlines: List[str]) -> BatchNewsDigestReport:
        """Analyzes a collection of incoming headlines into a synthesized macro report."""
        if not headlines:
            headlines = ["Federal Reserve maintains interest rate pause, monitoring inflation progress."]

        analyzed = [self.analyze_event(h) for h in headlines]

        # Calculate dominant theme and net sentiment based on portfolio postures
        bull_count = sum(1 for a in analyzed if a.recommended_portfolio_posture in {"RISK_ON_EXPAND_EQUITY_CRYPTO", "TACTICAL_LONG_SPECIFIC_CATALYST"})
        bear_count = sum(1 for a in analyzed if a.recommended_portfolio_posture in {"RISK_OFF_FLIGHT_TO_CASH_BONDS", "DELTA_HEDGE_VOLATILITY"})
        total = max(1, len(analyzed))

        net_sentiment = round((bull_count - bear_count) / total, 2)

        tickers = list({x.ticker_or_asset for a in analyzed for x in a.affected_assets if x.ticker_or_asset not in {"SPY", "QQQ"}})
        if not tickers:
            tickers = ["BTC", "ETH", "NVDA", "AAPL"]

        theme = "Macro Monetary Easing & Tech Expansion" if net_sentiment > 0.2 else (
            "Hawkish Policy Tightening & Regulatory Friction" if net_sentiment < -0.2 else "Balanced Data-Dependent Rangebound Market"
        )

        return BatchNewsDigestReport(
            total_articles_analyzed=len(analyzed),
            dominant_market_theme=theme,
            net_macro_sentiment_score=net_sentiment,
            high_impact_events=analyzed,
            top_affected_tickers=tickers[:6],
            systemic_risk_alert=(net_sentiment < -0.5),
        )
