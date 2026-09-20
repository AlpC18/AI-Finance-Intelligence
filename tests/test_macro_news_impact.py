import pytest
from app.services.macro_news_analyzer_service import MacroNewsAnalyzerService


def test_fed_rate_cut_dovish_transmission():
    svc = MacroNewsAnalyzerService()
    headline = "Federal Reserve cuts interest rates by 50 basis points as inflation cools rapidly."

    analysis = svc.analyze_event(headline)

    assert analysis.event_type == "FED_CENTRAL_BANK_DECISION"
    assert analysis.macro_tone == "DOVISH"
    assert analysis.recommended_portfolio_posture == "RISK_ON_EXPAND_EQUITY_CRYPTO"

    tickers = {a.ticker_or_asset: a for a in analysis.affected_assets}
    assert "QQQ" in tickers
    assert "BTC" in tickers
    assert "DXY" in tickers
    assert tickers["QQQ"].direction == "STRONG_BULLISH"
    assert tickers["BTC"].direction == "STRONG_BULLISH"
    assert tickers["DXY"].direction == "MODERATE_BEARISH"


def test_fed_rate_hike_hawkish_transmission():
    svc = MacroNewsAnalyzerService()
    headline = "Federal Reserve hikes rate by 25 bps; Powell delivers hawkish higher-for-longer warning."

    analysis = svc.analyze_event(headline)

    assert analysis.event_type == "FED_CENTRAL_BANK_DECISION"
    assert analysis.macro_tone == "HAWKISH"
    assert analysis.recommended_portfolio_posture == "RISK_OFF_FLIGHT_TO_CASH_BONDS"

    tickers = {a.ticker_or_asset: a for a in analysis.affected_assets}
    assert tickers["QQQ"].direction == "STRONG_BEARISH"
    assert tickers["BTC"].direction == "STRONG_BEARISH"
    assert tickers["DXY"].direction == "STRONG_BULLISH"


def test_sec_regulatory_lawsuit_dismissal_and_victory():
    svc = MacroNewsAnalyzerService()
    headline = "Judge dismisses SEC lawsuit against Coinbase; massive legal victory for crypto industry."

    analysis = svc.analyze_event(headline)

    assert analysis.event_type == "REGULATORY_LAWSUIT_RULING"
    assert analysis.legal_verdict_severity == "BENIGN_DISMISSED"
    assert analysis.recommended_portfolio_posture == "TACTICAL_LONG_SPECIFIC_CATALYST"

    tickers = {a.ticker_or_asset: a for a in analysis.affected_assets}
    assert "COIN" in tickers
    assert tickers["COIN"].direction == "STRONG_BULLISH"


def test_crypto_protocol_hack_and_contagion():
    svc = MacroNewsAnalyzerService()
    headline = "Major DeFi protocol suffers $250M exploit; security bridge funds frozen."

    analysis = svc.analyze_event(headline)

    assert analysis.event_type == "CRYPTO_ETF_AND_REGULATORY"
    assert analysis.macro_tone == "CRISIS_PANIC"
    assert analysis.recommended_portfolio_posture == "DELTA_HEDGE_VOLATILITY"


def test_batch_news_digest_and_theme_aggregation():
    svc = MacroNewsAnalyzerService()
    headlines = [
        "Federal Reserve cuts interest rates by 25 bps in policy easing cycle.",
        "Bitcoin ETF records $850M single-day institutional net inflow.",
        "Tech giant NVDA beats quarterly revenue expectations by 18%.",
    ]

    digest = svc.generate_digest(headlines)

    assert digest.total_articles_analyzed == 3
    assert digest.net_macro_sentiment_score > 0.3
    assert "Easing" in digest.dominant_market_theme or "Macro" in digest.dominant_market_theme
    assert len(digest.high_impact_events) == 3
    assert digest.systemic_risk_alert is False
