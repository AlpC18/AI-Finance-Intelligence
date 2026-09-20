import pytest
from app.services.deep_event_intelligence_service import DeepEventIntelligenceService
from app.services.geopolitical_conflict_service import GeopoliticalConflictService
from app.services.social_influencer_service import SocialInfluencerService


def test_elon_musk_dogecoin_and_tesla_tweets():
    svc = SocialInfluencerService()

    # Dogecoin tweet
    doge_post = svc.analyze_social_post(handle="@elonmusk", post_text="Dogecoin to the moon! Much currency, such wow.")
    assert "DOGE" in doge_post.impacted_tickers
    assert doge_post.sentiment_bias == "HYPER_BULLISH"
    assert doge_post.virality_multiplier >= 9.0

    # Tesla Robotaxi tweet
    tsla_post = svc.analyze_social_post(handle="@elonmusk", post_text="Tesla FSD v13 breakthrough solves unsupervised autonomy worldwide.")
    assert "TSLA" in tsla_post.impacted_tickers
    assert tsla_post.sentiment_bias == "HYPER_BULLISH"


def test_ai_lab_claude_release_and_nvda_transmission():
    svc = SocialInfluencerService()
    claude_post = svc.analyze_social_post(handle="@AnthropicAI", post_text="Introducing Claude 3.7 Sonnet: unprecedented reasoning benchmark leap.")

    assert "NVDA" in claude_post.impacted_tickers
    assert "TSM" in claude_post.impacted_tickers
    assert claude_post.sentiment_bias == "HYPER_BULLISH"
    assert "Semiconductor" in claude_post.meme_or_narrative_theme


def test_geopolitical_iran_israel_middle_east_shock():
    svc = GeopoliticalConflictService()
    headline = "Iran launches massive ballistic missile attack against Israel; airstrikes reported across region."

    res = svc.analyze_conflict_headline(headline)

    assert res.conflict_theater == "MIDDLE_EAST_IRAN_ISRAEL"
    assert res.escalation_status == "DEFCON_1_ACTIVE_MISSILE_STRIKE_WAR"
    assert res.crude_oil_shock_direction == "SPIKE_SURGE"
    assert res.gold_safe_haven_status == "MAXIMUM_ACCUMULATION"
    assert res.defense_stocks_outlook == "STRONG_SURGE"
    assert res.broad_market_posture == "RISK_OFF_FLIGHT_TO_SAFETY"
    assert "USO" in res.affected_assets
    assert "LMT" in res.affected_assets


def test_surprise_delta_and_source_credibility():
    svc = DeepEventIntelligenceService()

    # 1. Surprise Delta (+25% beat)
    delta = svc.calculate_surprise_delta("Quarterly EPS", expected_consensus=1.00, actual_print=1.25)
    assert delta.surprise_delta_pct == 25.0
    assert delta.market_reaction_verdict == "MASSIVE_POSITIVE_SURPRISE"

    # 2. Source Credibility Tier 1 vs Tier 4
    tier1 = svc.evaluate_source_credibility("Bloomberg Terminal")
    assert tier1.tier == "TIER_1_OFFICIAL_EDGAR_REUTERS_BLOOMBERG"
    assert tier1.credibility_weight == 1.00
    assert tier1.execution_gate_action == "ALLOW_IMMEDIATE_EXECUTION"

    tier4 = svc.evaluate_source_credibility("Anonymous Twitter Account @CryptoWhalePump")
    assert tier4.tier == "TIER_4_SOCIAL_TWITTER_UNVERIFIED"
    assert tier4.execution_gate_action == "QUARANTINE_BLOCK_ORDER"


def test_omni_event_integrated_pipeline():
    svc = DeepEventIntelligenceService()
    headline = "Elon Musk tweets xAI deploying 200k Nvidia H200 GPU compute cluster."

    report = svc.analyze_omni_event(headline=headline, source_name="Bloomberg", handle="@elonmusk")

    assert report.credibility.credibility_weight == 1.00
    assert report.social_influencer is not None
    assert "NVDA" in report.social_influencer.impacted_tickers
    assert len(report.chain_reaction_graph) >= 3
    assert len(report.historical_analogues) > 0
    assert "BULL_CALL_SPREAD" in report.options_playbook.recommended_strategy_name
