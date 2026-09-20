import pytest
from app.models.options import OptionsStrategyRequest, StrategyLeg
from app.services.options_pricing_service import OptionsPricingService


def test_black_scholes_price_and_greeks():
    svc = OptionsPricingService()

    # ATM Call: Spot 100, Strike 100, 1 Year, r=5%, Vol=20%
    price, greeks = svc.calculate_bsm_price_and_greeks(
        s=100.0, k=100.0, t=1.0, r=0.05, sigma=0.20, option_type="call"
    )

    # Theoretical BSM ATM Call is ~$10.45
    assert 9.5 < price < 11.5
    # Call Delta should be ~0.63
    assert 0.50 < greeks.delta < 0.70
    assert greeks.gamma > 0
    assert greeks.theta < 0  # Time decay is negative
    assert greeks.vega > 0   # Positive volatility sensitivity

    # ATM Put
    put_price, put_greeks = svc.calculate_bsm_price_and_greeks(
        s=100.0, k=100.0, t=1.0, r=0.05, sigma=0.20, option_type="put"
    )
    assert 5.0 < put_price < 7.0
    assert -0.50 < put_greeks.delta < -0.30


def test_option_chain_generation():
    svc = OptionsPricingService()
    chain = svc.generate_option_chain("AAPL", underlying_price=150.0)

    assert chain.underlying_symbol == "AAPL"
    assert len(chain.calls) > 0
    assert len(chain.puts) > 0
    assert chain.calls[0].greeks.delta is not None


def test_covered_call_strategy_payoff():
    svc = OptionsPricingService()
    req = OptionsStrategyRequest(
        strategy_type="covered_call",
        underlying_symbol="AAPL",
        underlying_price=150.0,
        legs=[
            StrategyLeg(is_stock_leg=True, action="buy", quantity=100, premium_or_price=150.0),
            StrategyLeg(option_type="call", action="sell", strike=160.0, quantity=1, premium_or_price=4.5),
        ],
    )

    payoff = svc.calculate_strategy_payoff(req)
    assert payoff.underlying_symbol == "AAPL"
    assert payoff.max_profit is not None
    assert len(payoff.payoff_curve) > 0
    assert len(payoff.breakeven_points) >= 1
