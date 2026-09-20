"""IBKR adapter seam; gateway connectivity remains explicitly opt-in."""
from app.models.broker import BrokerAccount, BrokerOrder, BrokerPosition, OrderRequest
from app.providers.broker_base import BrokerError


class IbkrBrokerProvider:
    def __init__(self, api_key: str, api_secret: str, **_: object) -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    async def get_account(self) -> BrokerAccount:
        raise BrokerError("IBKR adapter configured; gateway execution is disabled.", 503)

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        raise BrokerError("IBKR live execution requires TRADING_MODE=live and gateway setup.", 403)

    async def get_order(self, order_id: str) -> BrokerOrder:
        raise BrokerError("IBKR gateway is not connected.", 503)

    async def get_positions(self) -> list[BrokerPosition]:
        raise BrokerError("IBKR gateway is not connected.", 503)

    async def cancel_order(self, order_id: str) -> None:
        raise BrokerError("IBKR gateway is not connected.", 503)

    async def replace_order(self, order_id: str, **_: object) -> BrokerOrder:
        raise BrokerError("IBKR gateway is not connected.", 503)
