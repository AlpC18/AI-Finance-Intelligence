"""Broker execution provider protocol + errors.

Swap paper/live or vendors (Alpaca, IBKR, ...) without touching the service layer.
"""
from typing import Protocol

from app.core.errors import AppError
from app.models.broker import BrokerAccount, BrokerOrder, BrokerPosition, OrderRequest


class BrokerError(AppError):
    """Raised when the upstream broker rejects or fails a request."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message, status_code=status_code)


class BrokerProvider(Protocol):
    async def get_account(self) -> BrokerAccount: ...
    async def place_order(self, order: OrderRequest) -> BrokerOrder: ...
    async def get_order(self, order_id: str) -> BrokerOrder: ...
    async def get_positions(self) -> list[BrokerPosition]: ...
