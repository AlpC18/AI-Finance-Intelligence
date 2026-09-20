"""Point-in-Time (PIT) & Multi-Timeframe (MTF) Confluence schemas (FinRL & Jesse)."""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class TimeframeReading(BaseModel):
    timeframe: Literal["5m", "15m", "1h", "1d"]
    trend_direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    rsi: float
    macd_signal: Literal["BULLISH_CROSS", "BEARISH_CROSS", "NEUTRAL"]
    sma_alignment: Literal["ABOVE_ALL_MA", "BELOW_ALL_MA", "MIXED"]


class MultiTimeframeConfluenceReport(BaseModel):
    symbol: str
    timeframe_readings: Dict[str, TimeframeReading]
    confluence_score_pct: float = Field(ge=0.0, le=100.0)
    confluence_status: Literal["STRONG_ALIGNMENT_LONG", "STRONG_ALIGNMENT_SHORT", "CONFLICTING_TIMEFRAMES"]
    higher_timeframe_trend: str
    lower_timeframe_trigger: str
    tradeable: bool


class PointInTimeFilingRecord(BaseModel):
    symbol: str
    period_ended: str
    filing_date_published: str
    revenue_m: float
    net_income_m: float
    eps: float


class PITSanitizerResult(BaseModel):
    symbol: str
    evaluation_point_in_time: str
    eligible_historical_filings: List[PointInTimeFilingRecord]
    leaked_future_filings_pruned_count: int
    lookahead_bias_detected: bool = False
    audit_clean: bool = True
