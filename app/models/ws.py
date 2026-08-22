"""Typed WebSocket frame contracts for /ws/insights/{symbol} and alert pushes.

These mirror exactly what the server emits, so the frontend can type its socket
handlers against a single source of truth (exposed via /api/meta/contracts).
"""
from typing import Literal

from pydantic import BaseModel

WS_INSIGHTS_PATH = "/ws/insights/{symbol}"


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
