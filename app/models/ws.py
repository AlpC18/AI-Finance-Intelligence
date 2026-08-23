"""Typed WebSocket frame contracts for /ws/insights/{symbol} and alert pushes.

These mirror exactly what the server emits, so the frontend can type its socket
handlers against a single source of truth (exposed via /api/meta/contracts).
"""
from typing import Literal

from pydantic import BaseModel

WS_INSIGHTS_PATH = "/ws/insights/{symbol}"
WS_ACCOUNT_PATH = "/ws/account"


class WsStartFrame(BaseModel):
    type: Literal["start"] = "start"
    symbol: str


class WsTokenFrame(BaseModel):
    type: Literal["token"] = "token"
    symbol: str
    text: str


class WsEndFrame(BaseModel):
    type: Literal["end"] = "end"
    symbol: str
    citations: list[str] = []
    disclaimer: str = "Bu bir yatirim tavsiyesi degildir."


class WsErrorFrame(BaseModel):
    type: Literal["error"] = "error"
    symbol: str
    detail: str


class WsAlertFrame(BaseModel):
    type: Literal["alert"] = "alert"
    alert_id: int | None = None
    symbol: str
    condition: str
    threshold: float | None = None
    triggered_at: str


class WsEventFrame(BaseModel):
    type: Literal["event"] = "event"
    category: str
    severity: int
    orientation: str
    title: str
    blocking: bool = False


class WsOrderFrame(BaseModel):
    """An order changed state at the venue.

    Pushed on the account channel so a fill or a rejection surfaces the moment
    reconciliation observes it, instead of on whenever the UI next polls.
    """

    type: Literal["order"] = "order"
    order_id: int | None = None
    broker_order_id: str
    symbol: str
    side: str
    status: str
    filled_quantity: float = 0.0
    filled_avg_price: float | None = None
    reconciled: bool = False


class WsHaltFrame(BaseModel):
    """Trading was halted for this account.

    ``reason`` is the structural label from KillSwitchStatus ("manual" |
    "daily_loss_limit"), never a rendered sentence - the UI decides the wording.
    ``canceled_orders`` reports what the halt actually flattened.
    """

    type: Literal["halt"] = "halt"
    reason: str
    drawdown_pct: float = 0.0
    daily_loss_limit_pct: float = 0.0
    canceled_orders: int = 0
    halted_at: str
