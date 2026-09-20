"""Inbound Alpaca trade-update webhook payloads (strictly validated at the boundary).

Alpaca emits a trade-update event per order transition; the fields we act on are
the order id, its status, and the cumulative fill. Numerics arrive as strings, so
these models rely on Pydantic's lax numeric coercion. Unknown keys are ignored,
never trusted.
"""
from __future__ import annotations
from decimal import Decimal

from typing import Optional

from pydantic import BaseModel, Field
from app.core.money import Money


class WebhookOrder(BaseModel):
    """The nested ``order`` object of an Alpaca trade-update event."""

    id: str = Field(min_length=1, max_length=128)
    symbol: str = ""
    side: str = ""
    status: str = ""
    filled_qty: Money = Decimal(0)
    filled_avg_price: Optional[Money] = None


class AlpacaTradeUpdate(BaseModel):
    """A single Alpaca trade-update event delivered to /api/trade/webhook."""

    event: str = Field(min_length=1, max_length=64)  # fill | partial_fill | canceled ...
    order: WebhookOrder


class WebhookResult(BaseModel):
    accepted: bool
    reconciled: bool = False
    detail: str = ""
