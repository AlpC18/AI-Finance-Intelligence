"""Institutional Market Impact & Almgren-Chriss Slippage schemas (Nautilus & FreqAI)."""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class AlmgrenChrissImpactResult(BaseModel):
    symbol: str
    order_shares: float
    average_daily_volume_adv: float
    participation_rate_pct: float
    market_price: float
    expected_slippage_bps: float
    expected_slippage_dollars: float
    effective_execution_price: float
    temporary_impact_bps: float
    permanent_impact_bps: float
    liquidity_regime: Literal["HIGH_LIQUIDITY_NEGLIGIBLE_IMPACT", "MODERATE_IMPACT", "SEVERE_LIQUIDITY_PENALTY"]
    recommended_execution_algorithm: Literal["INSTANT_MARKET", "TWAP_30MIN", "VWAP_ALL_DAY", "ICEBERG_DISPATCH"]
