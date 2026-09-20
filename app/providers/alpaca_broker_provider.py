"""Alpaca paper-trading broker over pure async httpx (no vendor SDK).

Only transport-level failures are retried; an HTTP status error (e.g. 422
insufficient buying power) is surfaced immediately so a retry can never duplicate
an order that the broker already accepted.
"""
from __future__ import annotations

from decimal import Decimal

from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.money import ZERO, to_decimal
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
            raise BrokerError(
                _error_message(resp), status_code=code, upstream_status=resp.status_code
            )
        # A successful cancel is 204 with an empty body; calling .json() on that
        # raises, which would turn a completed cancellation into a reported
        # failure and invite the caller to retry something already done.
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def get_account(self) -> BrokerAccount:
        data = await self._request("GET", "/v2/account")
        return BrokerAccount(
            account_number=str(data.get("account_number", "")),
            status=str(data.get("status", "")),
            currency=str(data.get("currency", "USD")),
            cash=_to_money(data.get("cash")),
            buying_power=_to_money(data.get("buying_power")),
        )

    async def place_order(self, order: OrderRequest) -> BrokerOrder:
        body: dict = {
            "symbol": order.symbol,
            "qty": _wire(order.quantity),
            "side": order.side,
            "type": order.order_type,
            "time_in_force": order.time_in_force,
        }
        if order.order_type == "limit" and order.limit_price is not None:
            body["limit_price"] = str(order.limit_price)
        if order.stop_loss is not None and order.take_profit is not None:
            body["order_class"] = "bracket"
            body["stop_loss"] = {"stop_price": _wire(order.stop_loss)}
            body["take_profit"] = {"limit_price": _wire(order.take_profit)}
        if order.client_order_id:
            # The venue enforces uniqueness on this per account, which is the
            # backstop when two concurrent requests both clear our pre-check.
            body["client_order_id"] = order.client_order_id
        data = await self._request("POST", "/v2/orders", json=body)
        return _order_from(data, order.symbol, order.side, order.order_type)

    async def get_order(self, order_id: str) -> BrokerOrder:
        data = await self._request("GET", f"/v2/orders/{order_id}")
        return _order_from(data, str(data.get("symbol", "")), str(data.get("side", "buy")), "market")

    async def cancel_order(self, order_id: str) -> None:
        """Request cancellation. 204 means the venue ACCEPTED the request.

        Cancellation at Alpaca is asynchronous: the order moves to
        pending_cancel and only later settles as canceled - or fills anyway, if
        it was already resting at the exchange when we asked. So this returns
        nothing; the resulting state is whatever reconciliation next observes.
        """
        await self._request("DELETE", f"/v2/orders/{order_id}")

    async def replace_order(
        self,
        order_id: str,
        quantity: Optional[float] = None,
        limit_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> BrokerOrder:
        """Amend a working order in place, keeping its place in the book.

        Alpaca answers with a NEW order carrying a NEW id; the original moves to
        ``replaced``. So this returns the replacement, and the caller is
        responsible for retiring the order it grew out of - treating the answer
        as an update of the original would silently detach the local row from
        the id the venue is now working.
        """
        body: dict = {}
        if quantity is not None:
            body["qty"] = _wire(quantity)
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if client_order_id:
            body["client_order_id"] = client_order_id
        data = await self._request("PATCH", f"/v2/orders/{order_id}", json=body)
        return _order_from(
            data,
            str(data.get("symbol", "")),
            str(data.get("side", "buy")),
            str(data.get("type", "limit")),
        )

    async def get_positions(self) -> list[BrokerPosition]:
        data = await self._request("GET", "/v2/positions")
        items = data if isinstance(data, list) else []
        return [
            BrokerPosition(
                symbol=str(p.get("symbol", "")),
                quantity=_to_money(p.get("qty")),
                avg_entry_price=_to_money(p.get("avg_entry_price")),
                market_value=_to_money(p.get("market_value")),
                side=str(p.get("side", "long")),
            )
            for p in items
        ]


def _order_from(data: dict, symbol: str, side: str, order_type: str) -> BrokerOrder:
    return BrokerOrder(
        id=str(data.get("id", "")),
        symbol=str(data.get("symbol", symbol)),
        side=str(data.get("side", side)),
        quantity=_to_money(data.get("qty")),
        order_type=str(data.get("type", order_type)),
        status=str(data.get("status", "")),
        filled_quantity=_to_money(data.get("filled_qty")),
        filled_avg_price=_opt_money(data.get("filled_avg_price")),
        submitted_at=data.get("submitted_at"),
    )


def _error_message(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
        msg = payload.get("message") or payload.get("error") or resp.text
    except Exception:  # noqa: BLE001 - non-JSON error body
        msg = resp.text
    return f"Alpaca error {resp.status_code}: {msg}"[:300]


def _wire(value: object) -> str:
    """Render a Decimal for the venue without exponent or trailing zeros.

    ``str(Decimal("3.00000000"))`` keeps the storage scale and
    ``str(Decimal("1E+2"))`` is exponent notation; neither is what an order
    endpoint expects to receive as a quantity.
    """
    normalized = to_decimal(value).normalize()
    sign, digits, exponent = normalized.as_tuple()
    if isinstance(exponent, int) and exponent > 0:  # 1E+2 -> 100
        normalized = normalized.quantize(Decimal(1))
    return format(normalized, "f")


def _to_money(value: object, default: Decimal = ZERO) -> Decimal:
    """Coerce one Alpaca numeric into an exact Decimal.

    The venue is inconsistent about types - the same concept arrives as
    ``"19.99"`` on one field and ``19.99`` on another - and this is the
    outermost edge of the system, so it is where the conversion belongs.
    Going through ``to_decimal`` means a JSON float is parsed via its string
    form and never inherits binary representation error.
    """
    try:
        return to_decimal(value)
    except (TypeError, ValueError):
        return default


def _opt_money(value: object) -> Optional[Decimal]:
    """As ``_to_money``, but keeps None: an unfilled order has no fill price,
    which is a different fact from a fill price of zero."""
    if value is None or value == "":
        return None
    try:
        return to_decimal(value)
    except (TypeError, ValueError):
        return None
