"""Binance adapter seam. CCXT is optional and never enables live trading itself."""
from app.models.broker import BrokerAccount, BrokerOrder, BrokerPosition, OrderRequest
from app.providers.broker_base import BrokerError


class BinanceBrokerProvider:
    """Credential-free construction; operational calls require optional CCXT.

    The execution mode is owned by TradeService, not this adapter. This prevents
    a provider URL or a credential from silently turning simulation into live
    trading.
    """

    def __init__(self, api_key: str, api_secret: str, **_: object) -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    async def get_account(self) -> BrokerAccount:
        raise BrokerError("Binance/CCXT adapter configured; exchange execution is disabled.", 503)

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        raise BrokerError("Binance live execution requires TRADING_MODE=live and CCXT setup.", 403)

    async def get_order(self, order_id: str) -> BrokerOrder:
        raise BrokerError("Binance/CCXT adapter is not connected.", 503)

    async def get_positions(self) -> list[BrokerPosition]:
        raise BrokerError("Binance/CCXT adapter is not connected.", 503)

    async def cancel_order(self, order_id: str) -> None:
        raise BrokerError("Binance/CCXT adapter is not connected.", 503)

    async def replace_order(self, order_id: str, **_: object) -> BrokerOrder:
        raise BrokerError("Binance does not expose a portable replace-order operation.", 400)
