"""Adaptive ML Alpha & Non-Overridable Runtime Execution Guard schemas (FreqAI & nofx)."""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class MLAlphaSignal(BaseModel):
    symbol: str
    predicted_alpha_score: float = Field(ge=-1.0, le=1.0)  # -1.0 = Max Bearish, +1.0 = Max Bullish
    predicted_direction: Literal["UP", "DOWN", "FLAT"]
    model_confidence_pct: float = Field(ge=0.0, le=100.0)
    market_regime: Literal["TRENDING_BULL", "TRENDING_BEAR", "CHOPPY_RANGING", "HIGH_VOLATILITY_PANIC"]
    feature_importances: Dict[str, float]
    model_retrained_at: str
    model_drift_index: float  # < 0.15 is healthy, > 0.35 requires auto-retrain


class HardcodedRuntimeGuardStatus(BaseModel):
    runtime_mode: Literal["ACTIVE_EXECUTION", "OBSERVATION_ONLY_LOCKED"]
    consecutive_failures_count: int
    max_allowed_failures: int = 3
    daily_drawdown_pct: float
    max_daily_drawdown_limit_pct: float = 3.0
    guard_tripped: bool
    trip_reason: Optional[str] = None
    human_override_required: bool
