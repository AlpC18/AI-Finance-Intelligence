"""Almgren-Chriss Institutional Market Impact & Slippage Service.

Calculates non-linear square-root market impact and optimal execution routing.
"""
import math
from typing import Optional
import structlog

from app.models.market_impact import AlmgrenChrissImpactResult

logger = structlog.get_logger("market_impact_service")


class MarketImpactService:
    def calculate_slippage(
        self,
        symbol: str,
        order_shares: float,
        market_price: float,
        adv_shares: float = 20_000_000.0,
        daily_volatility: float = 0.02,
        gamma_temp_coeff: float = 0.314,
        eta_perm_coeff: float = 0.142,
    ) -> AlmgrenChrissImpactResult:
        """Calculates square-root law market impact."""
        sym = symbol.upper().strip()
        adv = max(1000.0, adv_shares)
        shares = max(1.0, order_shares)
        participation = shares / adv

        # Temporary impact: gamma * sigma * sqrt(Q / ADV)
        temp_impact = gamma_temp_coeff * daily_volatility * math.sqrt(participation)
        # Permanent impact: eta * sigma * (Q / ADV)
        perm_impact = eta_perm_coeff * daily_volatility * participation

        total_impact = temp_impact + (0.5 * perm_impact)
        slippage_bps = round(total_impact * 10_000.0, 2)
        slippage_dollars = round(market_price * total_impact, 3)
        effective_price = round(market_price + slippage_dollars, 2)

        participation_pct = round(participation * 100.0, 4)

        if participation_pct <= 0.05:
            regime = "HIGH_LIQUIDITY_NEGLIGIBLE_IMPACT"
            algo = "INSTANT_MARKET"
        elif participation_pct < 0.50:
            regime = "MODERATE_IMPACT"
            algo = "TWAP_30MIN"
        elif participation_pct < 2.0:
            regime = "MODERATE_IMPACT"
            algo = "VWAP_ALL_DAY"
        else:
            regime = "SEVERE_LIQUIDITY_PENALTY"
            algo = "ICEBERG_DISPATCH"

        return AlmgrenChrissImpactResult(
            symbol=sym,
            order_shares=shares,
            average_daily_volume_adv=adv,
            participation_rate_pct=participation_pct,
            market_price=market_price,
            expected_slippage_bps=slippage_bps,
            expected_slippage_dollars=slippage_dollars,
            effective_execution_price=effective_price,
            temporary_impact_bps=round(temp_impact * 10_000.0, 2),
            permanent_impact_bps=round(perm_impact * 10_000.0, 2),
            liquidity_regime=regime,
            recommended_execution_algorithm=algo,
        )
