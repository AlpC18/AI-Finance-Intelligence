"""Alpaca paper-trading broker over pure async httpx (no vendor SDK).

Only transport-level failures are retried; an HTTP status error (e.g. 422
insufficient buying power) is surfaced immediately so a retry can never duplicate
an order that the broker already accepted.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.models.broker import BrokerAccount, BrokerOrder, BrokerPosition, OrderRequest
from app.providers.broker_base import BrokerError

_RETRYABLE = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
)


class AlpacaBrokerProvider:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://paper-api.alpaca.markets",
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport  # test seam (httpx.MockTransport)
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Content-Type": "application/json",
        }

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _request(self, method: str, path: str, json: Optional[dict] = None) -> Any:
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=self._headers, transport=self._transport
        ) as client:
            resp = await client.request(method, f"{self._base_url}{path}", json=json)
        if resp.status_code >= 400:
            code = 502 if resp.status_code >= 500 else 400
            raise BrokerError(_error_message(resp), status_code=code)
        return resp.json()

    async def get_account(self) -> BrokerAccount:
        data = await self._request("GET", "/v2/account")
        return BrokerAccount(
            account_number=str(data.get("account_number", "")),
            status=str(data.get("status", "")),
            currency=str(data.get("currency", "USD")),
            cash=_to_float(data.get("cash")),
            buying_power=_to_float(data.get("buying_power")),
        )

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        body: dict = {
            "symbol": order.symbol,
            "qty": str(order.quantity),
            "side": order.side,
            "type": order.order_type,
            "time_in_force": order.time_in_force,
        }
        if order.order_type == "limit" and order.limit_price is not None:
            body["limit_price"] = str(order.limit_price)
        data = await self._request("POST", "/v2/orders", json=body)
        return _order_from(data, order.symbol, order.side, order.order_type)

    async def get_order(self, order_id: str) -> BrokerOrder:
        data = await self._request("GET", f"/v2/orders/{order_id}")
        return _order_from(data, str(data.get("symbol", "")), str(data.get("side", "buy")), "market")

    async def get_positions(self) -> list[BrokerPosition]:
        data = await self._request("GET", "/v2/positions")
        items = data if isinstance(data, list) else []
        return [
            BrokerPosition(
                symbol=str(p.get("symbol", "")),
                quantity=_to_float(p.get("qty")),
                avg_entry_price=_to_float(p.get("avg_entry_price")),
                market_value=_to_float(p.get("market_value")),
                side=str(p.get("side", "long")),
            )
            for p in items
        ]


def _order_from(data: dict, symbol: str, side: str, order_type: str) -> BrokerOrder:
    return BrokerOrder(
        id=str(data.get("id", "")),
        symbol=str(data.get("symbol", symbol)),
        side=str(data.get("side", side)),
        quantity=_to_float(data.get("qty", 0.0)),
        order_type=str(data.get("type", order_type)),
        status=str(data.get("status", "")),
        filled_quantity=_to_float(data.get("filled_qty")),
        filled_avg_price=_opt_float(data.get("filled_avg_price")),
        submitted_at=data.get("submitted_at"),
    )


def _error_message(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
        msg = payload.get("message") or payload.get("error") or resp.text
    except Exception:  # noqa: BLE001 - non-JSON error body
        msg = resp.text
    return f"Alpaca error {resp.status_code}: {msg}"[:300]


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _opt_float(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
