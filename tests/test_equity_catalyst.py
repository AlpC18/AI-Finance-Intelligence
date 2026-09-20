import pytest
from app.services.stock_catalyst_service import StockCatalystAnalyzerService


def test_fda_approval_catalyst_surge():
    svc = StockCatalystAnalyzerService()
    headline = "FDA approves CRSP gene-editing therapy for sickle cell following breakthrough Phase 3 success."

    res = svc.analyze_stock_news(symbol="CRSP", headline=headline)

    assert res.symbol == "CRSP"
    assert res.catalyst_type == "FDA_BIOTECH_CLINICAL_TRIAL"
    assert res.price_impact_magnitude == "MASSIVE_SURGE_PLUS_15_PCT"
    assert res.estimated_price_move_pct > 15.0
    assert res.tactical_playbook == "AGGRESSIVE_LONG_GAP_AND_GO"
    assert res.time_horizon == "STRUCTURAL_MULTI_QUARTER_SHIFT"
    assert len(res.peer_contagion_effects) > 0


def test_fda_rejection_crl_crash():
    svc = StockCatalystAnalyzerService()
    headline = "FDA rejects BIOT drug candidate and issues Complete Response Letter citing efficacy misses."

    res = svc.analyze_stock_news(symbol="BIOT", headline=headline)

    assert res.symbol == "BIOT"
    assert res.price_impact_magnitude == "CATASTROPHIC_CRASH_MINUS_15_PCT"
    assert res.estimated_price_move_pct < -20.0
    assert res.tactical_playbook == "IMMEDIATE_EXIT_AND_STOP_LOSS"


def test_hindenburg_short_seller_forensic_crisis():
    svc = StockCatalystAnalyzerService()
    headline = "Short seller Hindenburg Research releases forensic report alleging accounting fraud at SMCI."

    res = svc.analyze_stock_news(symbol="SMCI", headline=headline)

    assert res.catalyst_type == "FORENSIC_FRAUD_EXECUTIVE_CRISIS"
    assert res.price_impact_magnitude == "CATASTROPHIC_CRASH_MINUS_15_PCT"
    assert res.options_iv_impact == "IV_EXPANSION_SPIKE"
    assert res.tactical_playbook == "BUY_PROTECTIVE_PUT_OPTIONS"


def test_m_and_a_buyout_takeover():
    svc = StockCatalystAnalyzerService()
    headline = "Tech conglomerate submits $30B takeover buyout offer to acquire EA at $175 per share."

    res = svc.analyze_stock_news(symbol="EA", headline=headline)

    assert res.catalyst_type == "M_AND_A_TAKEOVER_ACTIVIST"
    assert res.price_impact_magnitude == "MASSIVE_SURGE_PLUS_15_PCT"
    assert res.estimated_price_move_pct > 10.0


def test_major_commercial_hyperscaler_contract():
    svc = StockCatalystAnalyzerService()
    headline = "PLTR signs multi-billion AI defense contract with US Department of Defense."

    res = svc.analyze_stock_news(symbol="PLTR", headline=headline)

    assert res.catalyst_type == "MAJOR_CONTRACT_PARTNERSHIP"
    assert res.price_impact_magnitude == "MODERATE_SURGE_PLUS_5_TO_15_PCT"
    assert res.tactical_playbook == "AGGRESSIVE_LONG_GAP_AND_GO"
    assert res.time_horizon == "STRUCTURAL_MULTI_QUARTER_SHIFT"


def test_earnings_guidance_upgrade():
    svc = StockCatalystAnalyzerService()
    headline = "NVDA reports Q2 revenue beat and raises full-year profit guidance by 35% on Blackwell demand."

    res = svc.analyze_stock_news(symbol="NVDA", headline=headline)

    assert res.catalyst_type == "EARNINGS_AND_GUIDANCE_SHOCK"
    assert res.price_impact_magnitude == "MODERATE_SURGE_PLUS_5_TO_15_PCT"
    assert res.estimated_price_move_pct > 5.0
