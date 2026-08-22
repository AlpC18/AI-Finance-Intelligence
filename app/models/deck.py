"""Unified UI contracts: the Trade Deck state + a machine-readable API contract."""
from typing import Any

from pydantic import BaseModel

from app.models.risk import KillSwitchStatus, RiskConfigRead
from app.models.ws import WS_INSIGHTS_PATH


class WsContract(BaseModel):
    insights_path: str = WS_INSIGHTS_PATH
    auth: str = "query param 'token' = access JWT (type=access)"
    frames: list[str] = ["start", "token", "end", "error", "alert"]


class TradeDeckState(BaseModel):
    """Everything the trading UI needs to render in one typed payload."""

    has_credentials: bool
    open_orders: int
    kill_switch: KillSwitchStatus
    risk_config: RiskConfigRead
    max_order_notional: float
    min_trade_confidence: float
    trade_enabled: bool
    ws: WsContract = WsContract()


class ApiContracts(BaseModel):
    """JSON-schema contracts for the frontend (WS frames + trade/backtest I/O)."""

    ws: WsContract
    ws_frames: dict[str, Any]
    kill_switch: dict[str, Any]
    trade_request: dict[str, Any]
    backtest_request: dict[str, Any]
    backtest_report: dict[str, Any]
