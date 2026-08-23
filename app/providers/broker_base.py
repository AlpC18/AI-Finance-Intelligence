"""Broker execution provider protocol + errors.

Swap paper/live or vendors (Alpaca, IBKR, ...) without touching the service layer.
"""
from typing import Optional, Protocol

from app.core.errors import AppError
from app.models.broker import BrokerAccount, BrokerOrder, BrokerPosition, OrderRequest


class BrokerError(AppError):
    """Raised when the upstream broker rejects or fails a request.

    ``upstream_status`` carries the venue's own HTTP status verbatim. Callers
    need it to tell apart rejections that mean different things - a cancel of an
    order that already filled (422) is not the same event as a cancel of an
    order the venue never heard of (404) - and reading that off the structured
    field is the only way to do it without parsing a human-facing message.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 502,
        upstream_status: Optional[int] = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.upstream_status = upstream_status


class BrokerProvider(Protocol):
    async def get_account(self) -> BrokerAccount: ...
    async def place_order(self, order: OrderRequest) -> BrokerOrder: ...
    async def get_order(self, order_id: str) -> BrokerOrder: ...
    async def get_positions(self) -> list[BrokerPosition]: ...
    async def cancel_order(self, order_id: str) -> None: ...
    async def replace_order(
        self,
        order_id: str,
        quantity: Optional[float] = None,
        limit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> BrokerOrder: ...
