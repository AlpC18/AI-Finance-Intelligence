import pytest
from app.core.config import Settings
from app.services.multi_agent_service import MultiAgentService


@pytest.mark.asyncio
async def test_multi_agent_deliberation_bullish_consensus():
    settings = Settings()
    svc = MultiAgentService(settings)

    quote = {"price": 180.0, "change_pct": 2.5}
    indicators = {"rsi": 32.0, "macd_hist": 0.5, "sma20": 175.0, "sma50": 170.0}
    fundamentals = {"pe_ratio": 16.0, "debt_to_equity": 0.8}
    events = []

    consensus = await svc.deliberate(
        symbol="AAPL",
        quote=quote,
        indicators=indicators,
        fundamentals=fundamentals,
        events=events,
        risk_tolerance="moderate",
    )

    assert consensus.symbol == "AAPL"
    assert consensus.final_action == "BUY"
    assert consensus.consensus_strength in {"unanimous", "majority"}
    assert consensus.risk_veto_applied is False
    assert consensus.actionable_plan.suggested_stop_loss is not None
    assert consensus.actionable_plan.suggested_take_profit is not None
    assert "macro_analyst" in consensus.opinions
    assert "technical_analyst" in consensus.opinions
    assert "fundamental_analyst" in consensus.opinions
    assert "risk_manager" in consensus.opinions


@pytest.mark.asyncio
async def test_multi_agent_risk_manager_veto():
    settings = Settings()
    svc = MultiAgentService(settings)

    # Parabolic overbought conditions (RSI 85)
    quote = {"price": 250.0, "change_pct": 4.0}
    indicators = {"rsi": 85.0, "macd_hist": 1.2, "sma20": 230.0, "sma50": 210.0}
    fundamentals = {"pe_ratio": 15.0, "debt_to_equity": 0.5}

    consensus = await svc.deliberate(
        symbol="NVDA",
        quote=quote,
        indicators=indicators,
        fundamentals=fundamentals,
        events=[],
        risk_tolerance="moderate",
    )

    assert consensus.risk_veto_applied is True
    assert consensus.final_action == "HOLD"
    assert consensus.consensus_strength == "vetoed"
    assert consensus.opinions["risk_manager"].veto is True
    assert "VETO" in consensus.deliberation_summary
