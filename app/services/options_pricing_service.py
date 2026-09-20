"""Options Pricing & Greeks Service (Black-Scholes-Merton + Strategy Wizard).

Calculates exact analytic Greeks (Delta, Gamma, Theta, Vega, Rho), implied
volatility, synthetic option chains, and multi-leg strategy payoff distributions.
"""
from datetime import datetime, date
import math
from typing import List, Optional, Tuple
import structlog

from app.models.options import (
    OptionChain,
    OptionContract,
    OptionGreeks,
    OptionsStrategyPayoff,
    OptionsStrategyRequest,
    StrategyLeg,
    StrategyPayoffPoint,
)

logger = structlog.get_logger("options_pricing_service")

SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / SQRT_2PI


class OptionsPricingService:
    def calculate_bsm_price_and_greeks(
        self,
        s: float,          # Underlying Spot Price
        k: float,          # Strike Price
        t: float,          # Time to Expiration in Years
        r: float = 0.045,  # Risk-free interest rate
        sigma: float = 0.30,  # Implied Volatility (annualized)
        option_type: str = "call",
    ) -> Tuple[float, OptionGreeks]:
        """Calculates exact Black-Scholes price and Greeks for a European option."""
        if t <= 0.0001:
            # At expiration payoff
            if option_type == "call":
                val = max(0.0, s - k)
                delta = 1.0 if s > k else 0.0
            else:
                val = max(0.0, k - s)
                delta = -1.0 if s < k else 0.0
            return val, OptionGreeks(
                delta=round(delta, 4),
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                rho=0.0,
                implied_volatility=round(sigma, 4),
            )

        sigma = max(0.001, sigma)
        sqrt_t = math.sqrt(t)
        d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t

        nd1 = _norm_cdf(d1)
        nd2 = _norm_cdf(d2)
        n_prime_d1 = _norm_pdf(d1)
        discount = math.exp(-r * t)

        gamma = n_prime_d1 / (s * sigma * sqrt_t)
        vega = (s * sqrt_t * n_prime_d1) / 100.0  # per 1% change in sigma

        if option_type.lower() == "call":
            price = s * nd1 - k * discount * nd2
            delta = nd1
            theta = (-(s * n_prime_d1 * sigma) / (2.0 * sqrt_t) - r * k * discount * nd2) / 365.0
            rho = (k * t * discount * nd2) / 100.0
        else:
            n_minus_d1 = _norm_cdf(-d1)
            n_minus_d2 = _norm_cdf(-d2)
            price = k * discount * n_minus_d2 - s * n_minus_d1
            delta = nd1 - 1.0
            theta = (-(s * n_prime_d1 * sigma) / (2.0 * sqrt_t) + r * k * discount * n_minus_d2) / 365.0
            rho = (-k * t * discount * n_minus_d2) / 100.0

        greeks = OptionGreeks(
            delta=round(delta, 4),
            gamma=round(gamma, 4),
            theta=round(theta, 4),
            vega=round(vega, 4),
            rho=round(rho, 4),
            implied_volatility=round(sigma, 4),
        )
        return max(0.01, round(price, 2)), greeks

    def generate_option_chain(
        self,
        symbol: str,
        underlying_price: float,
        iv: float = 0.35,
        risk_free_rate: float = 0.045,
    ) -> OptionChain:
        """Generates realistic multi-strike and multi-expiry option chain."""
        sym = symbol.upper().strip()
        expirations = ["2026-09-18", "2026-10-16", "2026-12-18", "2027-01-15"]
        
        # Strike step based on price level
        step = 5.0 if underlying_price > 100 else (2.5 if underlying_price > 50 else 1.0)
        base_strike = round(underlying_price / step) * step
        strikes = [round(base_strike + i * step, 2) for i in range(-5, 6)]

        calls: List[OptionContract] = []
        puts: List[OptionContract] = []

        # Assume 30 days for nearest expiry (~0.082 years)
        t_years = 30.0 / 365.0
        exp_str = expirations[0]

        for strike in strikes:
            # Call
            call_price, call_greeks = self.calculate_bsm_price_and_greeks(
                s=underlying_price,
                k=strike,
                t=t_years,
                r=risk_free_rate,
                sigma=iv,
                option_type="call",
            )
            spread = max(0.05, round(call_price * 0.02, 2))
            call_contract = OptionContract(
                contract_symbol=f"{sym}{exp_str.replace('-', '')[2:]}C{int(strike * 1000):08d}",
                underlying_symbol=sym,
                strike=strike,
                expiration=exp_str,
                option_type="call",
                bid=max(0.01, round(call_price - spread / 2.0, 2)),
                ask=round(call_price + spread / 2.0, 2),
                last_price=call_price,
                volume=int(abs(1000 - (strike - underlying_price) * 20)),
                open_interest=int(abs(5000 - (strike - underlying_price) * 50)),
                greeks=call_greeks,
            )
            calls.append(call_contract)

            # Put
            put_price, put_greeks = self.calculate_bsm_price_and_greeks(
                s=underlying_price,
                k=strike,
                t=t_years,
                r=risk_free_rate,
                sigma=iv,
                option_type="put",
            )
            p_spread = max(0.05, round(put_price * 0.02, 2))
            put_contract = OptionContract(
                contract_symbol=f"{sym}{exp_str.replace('-', '')[2:]}P{int(strike * 1000):08d}",
                underlying_symbol=sym,
                strike=strike,
                expiration=exp_str,
                option_type="put",
                bid=max(0.01, round(put_price - p_spread / 2.0, 2)),
                ask=round(put_price + p_spread / 2.0, 2),
                last_price=put_price,
                volume=int(abs(800 - (underlying_price - strike) * 20)),
                open_interest=int(abs(4000 - (underlying_price - strike) * 50)),
                greeks=put_greeks,
            )
            puts.append(put_contract)

        return OptionChain(
            underlying_symbol=sym,
            underlying_price=underlying_price,
            expirations=expirations,
            calls=calls,
            puts=puts,
        )

    def calculate_strategy_payoff(
        self, request: OptionsStrategyRequest
    ) -> OptionsStrategyPayoff:
        """Calculates multi-leg profit/loss payoff curve across underlying prices."""
        s = request.underlying_price
        sym = request.underlying_symbol.upper().strip()

        # Compute net debit / credit
        net_flow = 0.0
        agg_delta = 0.0
        agg_gamma = 0.0
        agg_theta = 0.0
        agg_vega = 0.0
        agg_rho = 0.0

        for leg in request.legs:
            multiplier = 100 if not leg.is_stock_leg else 1
            cost = leg.premium_or_price * leg.quantity * multiplier
            if leg.action == "buy":
                net_flow -= cost  # cash outflow (debit)
            else:
                net_flow += cost  # cash inflow (credit)

            # Aggregate Greeks
            if not leg.is_stock_leg and leg.strike and leg.option_type:
                _, g = self.calculate_bsm_price_and_greeks(
                    s=s,
                    k=leg.strike,
                    t=30.0 / 365.0,
                    r=request.risk_free_rate,
                    sigma=0.35,
                    option_type=leg.option_type,
                )
                sign = 1.0 if leg.action == "buy" else -1.0
                qty = leg.quantity
                agg_delta += g.delta * sign * qty
                agg_gamma += g.gamma * sign * qty
                agg_theta += g.theta * sign * qty * 100
                agg_vega += g.vega * sign * qty * 100
                agg_rho += g.rho * sign * qty * 100
            elif leg.is_stock_leg:
                sign = 1.0 if leg.action == "buy" else -1.0
                agg_delta += 1.0 * sign * leg.quantity

        # Payoff curve range from -30% to +30% of underlying price
        price_steps = [round(s * (0.70 + i * 0.02), 2) for i in range(31)]
        payoff_curve: List[StrategyPayoffPoint] = []
        pnl_values: List[float] = []

        for p in price_steps:
            point_pnl = net_flow  # start with initial cashflow
            for leg in request.legs:
                multiplier = 100 if not leg.is_stock_leg else 1
                qty = leg.quantity * multiplier
                if leg.is_stock_leg:
                    val_at_exp = (p - leg.premium_or_price) * qty if leg.action == "buy" else (leg.premium_or_price - p) * qty
                    point_pnl += val_at_exp - (net_flow if leg.action == "buy" else 0)
                else:
                    strike = leg.strike or s
                    if leg.option_type == "call":
                        intrinsic = max(0.0, p - strike) * qty
                    else:
                        intrinsic = max(0.0, strike - p) * qty

                    if leg.action == "buy":
                        point_pnl += intrinsic
                    else:
                        point_pnl -= intrinsic

            roi = round((point_pnl / abs(net_flow) * 100.0), 2) if net_flow != 0 else 0.0
            payoff_curve.append(
                StrategyPayoffPoint(
                    underlying_price=p,
                    profit_loss=round(point_pnl, 2),
                    roi_pct=roi,
                )
            )
            pnl_values.append(point_pnl)

        max_p = max(pnl_values)
        min_p = min(pnl_values)

        # Detect breakeven prices where PnL crosses zero
        breakevens: List[float] = []
        for i in range(len(payoff_curve) - 1):
            p1 = payoff_curve[i]
            p2 = payoff_curve[i + 1]
            if (p1.profit_loss <= 0 and p2.profit_loss >= 0) or (p1.profit_loss >= 0 and p2.profit_loss <= 0):
                # Linear interpolation for zero crossing
                denom = p2.profit_loss - p1.profit_loss
                if denom != 0:
                    zero_p = p1.underlying_price + (-p1.profit_loss / denom) * (p2.underlying_price - p1.underlying_price)
                    breakevens.append(round(zero_p, 2))

        rr_ratio = round(abs(max_p / min_p), 2) if min_p < 0 and max_p > 0 else None

        thesis = f"{request.strategy_type.replace('_', ' ').title()} strategy on {sym}."
        if net_flow > 0:
            thesis += f" Net credit collected: ${abs(net_flow):.2f}. Max profit capped at initial credit."
        else:
            thesis += f" Net debit invested: ${abs(net_flow):.2f}."

        return OptionsStrategyPayoff(
            strategy_name=request.strategy_type,
            underlying_symbol=sym,
            underlying_price=s,
            net_debit_credit=round(net_flow, 2),
            max_profit=round(max_p, 2) if max_p < 100000 else None,
            max_loss=round(min_p, 2) if min_p > -100000 else None,
            breakeven_points=sorted(list(set(breakevens))),
            risk_reward_ratio=rr_ratio,
            aggregate_greeks=OptionGreeks(
                delta=round(agg_delta, 4),
                gamma=round(agg_gamma, 4),
                theta=round(agg_theta, 2),
                vega=round(agg_vega, 2),
                rho=round(agg_rho, 2),
                implied_volatility=0.35,
            ),
            payoff_curve=payoff_curve,
            summary_thesis=thesis,
        )
