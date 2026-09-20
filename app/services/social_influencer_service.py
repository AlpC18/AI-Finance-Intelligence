"""Twitter / X Influencer & AI Lab Announcement Intelligence Service.

Processes high-impact social posts (Elon Musk, Sam Altman, Anthropic Claude, Nvidia)
and computes immediate meme, semiconductor, and crypto transmission vectors.
"""
from typing import List, Optional
import structlog

from app.models.deep_event_intelligence import SocialInfluencerImpact

logger = structlog.get_logger("social_influencer_service")


class SocialInfluencerService:
    def analyze_social_post(
        self,
        handle: str,
        post_text: str,
        is_verified: bool = True,
    ) -> SocialInfluencerImpact:
        """Parses a Twitter/X post to extract market impact on TSLA, DOGE, NVDA, AI stocks."""
        h = handle.lower().strip()
        text = post_text.lower().strip()

        impacted_tickers: List[str] = []
        sentiment = "NEUTRAL"
        virality = 5.0
        theme = "General Social Commentary"

        # 1. Elon Musk Posts (@elonmusk)
        if "elonmusk" in h:
            virality = 9.5
            if any(w in text for w in ["doge", "dogecoin", "shib", "to the moon"]):
                impacted_tickers = ["DOGE", "SHIB"]
                sentiment = "HYPER_BULLISH"
                theme = "Elon Musk Dogecoin Meme Surge"
            elif any(w in text for w in ["fsd", "robotaxi", "tesla", "cybertruck", "optimus", "gigafactory"]):
                impacted_tickers = ["TSLA"]
                sentiment = "HYPER_BULLISH" if any(w in text for w in ["breakthrough", "solved", "launch", "record"]) else "MODERATE_BULLISH"
                theme = "Tesla Autonomous FSD & Robotics Narrative"
            elif any(w in text for w in ["xai", "grok", "compute", "cluster", "h100", "h200"]):
                impacted_tickers = ["NVDA", "ORCL", "SMCI"]
                sentiment = "HYPER_BULLISH"
                theme = "xAI Colossus Compute Cluster Hardware Expansion"
            else:
                impacted_tickers = ["TSLA"]
                sentiment = "NEUTRAL"
                theme = "General Elon Musk Post"

        # 2. AI Lab Frontier Model Releases (Anthropic, OpenAI, DeepSeek, Meta)
        elif any(w in h or w in text for w in ["anthropic", "claude", "openai", "altman", "deepseek", "meta ai"]):
            virality = 8.5
            impacted_tickers = ["NVDA", "TSM", "MSFT", "GOOGL", "AVGO"]
            if any(w in text for w in ["new model", "claude 3.7", "gpt-5", "reasoning", "breakthrough", "benchmark"]):
                sentiment = "HYPER_BULLISH"
                theme = "Frontier AI LLM Capability Leap Expanding Semiconductor Compute Demand"
            else:
                sentiment = "MODERATE_BULLISH"
                theme = "AI Lab Ecosystem Update"

        # 3. Nvidia / Jensen Huang (@nvidia, @jensenhuang)
        elif any(w in h for w in ["nvidia", "jensen"]):
            virality = 8.0
            impacted_tickers = ["NVDA", "TSM", "AMD", "SMCI"]
            sentiment = "HYPER_BULLISH" if "blackwell" in text or "demand" in text else "MODERATE_BULLISH"
            theme = "Nvidia AI Acceleration Hardware Demand Update"

        else:
            impacted_tickers = ["SPY"]
            sentiment = "NEUTRAL"
            virality = 2.0
            theme = "Standard Social Mention"

        return SocialInfluencerImpact(
            handle=handle,
            platform="Twitter/X",
            is_verified_account=is_verified,
            post_text=post_text,
            impacted_tickers=impacted_tickers,
            sentiment_bias=sentiment,
            virality_multiplier=virality,
            meme_or_narrative_theme=theme,
        )
