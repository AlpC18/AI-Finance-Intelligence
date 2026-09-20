"""Alpaca broker provider — transport contract and the no-duplicate-order rule.

The single most dangerous bug available to this module is submitting the same
order twice. That is why only *transport* failures are retried: if the broker
answered at all (even with 422 insufficient buying power), the order may already
exist upstream, so a retry is forbidden. That asymmetry is pinned here.

Also covers the numeric coercion helpers, because Alpaca sends every figure as a
string and occasionally as ``null`` or ``""``.
"""
from __future__ import annotations

from decimal import Decimal

import json

import httpx
import pytest
from tenacity import wait_none

from app.models.broker import OrderRequest
from app.providers.alpaca_broker_provider import (
    AlpacaBrokerProvider,
    _error_message,
    _opt_money,
    _to_money,
    _wire,
)
from app.providers.broker_base import BrokerError


def _broker(handler) -> AlpacaBrokerProvider:
    return AlpacaBrokerProvider(
        api_key="k", api_secret="s", transport=httpx.MockTransport(handler)
    )


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(AlpacaBrokerProvider._request.retry, "wait", wait_none())


_ORDER_JSON = {
    "id": "ord-1",
    "symbol": "AAPL",
    "side": "buy",
    "qty": "3",
    "type": "market",
    "status": "accepted",
    "filled_qty": "0",
    "filled_avg_price": None,
    "submitted_at": "2026-01-05T10:00:00Z",
}


# --------------------------- account ----------------------------------------

async def test_get_account_coerces_string_numerics():
    payload = {
        "account_number": "PA123",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "1500.55",
        "buying_power": "3000.10",
    }
    account = await _broker(lambda r: httpx.Response(200, json=payload)).get_account()
    # Exact: "1500.55" from the venue must not become the nearest float.
    assert account.cash == Decimal("1500.55")
    assert account.buying_power == Decimal("3000.10")
    assert account.account_number == "PA123"


async def test_get_account_defaults_missing_fields():
    account = await _broker(lambda r: httpx.Response(200, json={})).get_account()
    assert account.cash == 0.0 and account.currency == "USD" and account.status == ""


async def test_credentials_are_sent_as_alpaca_headers():
    seen: dict = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={})

    await _broker(handler).get_account()
    assert seen["apca-api-key-id"] == "k" and seen["apca-api-secret-key"] == "s"


# --------------------------- orders -----------------------------------------

async def test_place_market_order_omits_limit_price():
    seen: dict = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_ORDER_JSON)

    order = await _broker(handler).place_order(
        OrderRequest(symbol="AAPL", side="buy", quantity=3, order_type="market")
    )
    # A quantity now goes out in canonical decimal form ("3", not "3.0").
    assert "limit_price" not in seen and seen["qty"] == "3"
    assert order.id == "ord-1" and order.status == "accepted"


async def test_place_limit_order_includes_limit_price():
    seen: dict = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={**_ORDER_JSON, "type": "limit"})

    await _broker(handler).place_order(
        OrderRequest(
            symbol="AAPL", side="buy", quantity=2, order_type="limit", limit_price=101.5
        )
    )
    assert seen["limit_price"] == "101.5" and seen["type"] == "limit"


async def test_limit_type_without_price_sends_no_limit_price():
    seen: dict = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_ORDER_JSON)

    await _broker(handler).place_order(
        OrderRequest(symbol="AAPL", side="buy", quantity=1, order_type="limit")
    )
    assert "limit_price" not in seen


async def test_get_order_reads_symbol_and_side_from_the_payload():
    filled = {**_ORDER_JSON, "status": "filled", "filled_qty": "3", "filled_avg_price": "182.4"}
    order = await _broker(lambda r: httpx.Response(200, json=filled)).get_order("ord-1")
    assert order.filled_quantity == Decimal("3")
    assert order.filled_avg_price == Decimal("182.4")
    assert order.symbol == "AAPL" and order.side == "buy"


async def test_get_order_falls_back_when_fields_are_absent():
    order = await _broker(lambda r: httpx.Response(200, json={"id": "x"})).get_order("x")
    assert order.side == "buy" and order.order_type == "market" and order.symbol == ""


# --------------------------- positions --------------------------------------

async def test_get_positions_maps_every_row():
    rows = [
        {"symbol": "AAPL", "qty": "5", "avg_entry_price": "150", "market_value": "800", "side": "long"},
        {"symbol": "MSFT", "qty": "2.5", "avg_entry_price": "300", "market_value": "760"},
    ]
    positions = await _broker(lambda r: httpx.Response(200, json=rows)).get_positions()
    assert [p.symbol for p in positions] == ["AAPL", "MSFT"]
    assert positions[1].quantity == 2.5 and positions[1].side == "long"


async def test_get_positions_tolerates_a_non_list_body():
    """Alpaca returns an object on some error shapes; we must not explode."""
    positions = await _broker(lambda r: httpx.Response(200, json={"oops": 1})).get_positions()
    assert positions == []


# --------------------------- error mapping ----------------------------------

async def test_client_error_maps_to_400_broker_error():
    handler = lambda r: httpx.Response(422, json={"message": "insufficient buying power"})
    with pytest.raises(BrokerError) as exc:
        await _broker(handler).place_order(
            OrderRequest(symbol="AAPL", side="buy", quantity=1)
        )
    assert exc.value.status_code == 400 and "insufficient buying power" in str(exc.value)


async def test_server_error_maps_to_502_broker_error():
    with pytest.raises(BrokerError) as exc:
        await _broker(lambda r: httpx.Response(503, text="maintenance")).get_account()
    assert exc.value.status_code == 502


async def test_status_errors_are_never_retried():
    """The order may already exist upstream — retrying could duplicate it."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(422, json={"message": "rejected"})

    with pytest.raises(BrokerError):
        await _broker(handler).place_order(OrderRequest(symbol="AAPL", side="buy", quantity=1))
    assert calls["n"] == 1  # exactly one submission attempt


async def test_transport_errors_are_retried_then_succeed():
    """A connect failure means the broker never saw it — safe to resend."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("tcp reset")
        return httpx.Response(200, json=_ORDER_JSON)

    order = await _broker(handler).place_order(
        OrderRequest(symbol="AAPL", side="buy", quantity=1)
    )
    assert order.id == "ord-1" and calls["n"] == 3


async def test_transport_errors_give_up_after_three_attempts():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("broker hung")

    with pytest.raises(httpx.ReadTimeout):
        await _broker(handler).get_account()
    assert calls["n"] == 3


# --------------------------- helpers ----------------------------------------

def test_error_message_prefers_message_then_error_then_text():
    assert "boom" in _error_message(httpx.Response(400, json={"message": "boom"}))
    assert "bang" in _error_message(httpx.Response(400, json={"error": "bang"}))
    assert "gateway" in _error_message(httpx.Response(502, text="bad gateway"))


def test_error_message_survives_a_non_json_body_and_is_truncated():
    msg = _error_message(httpx.Response(500, text="x" * 900))
    assert msg.startswith("Alpaca error 500:") and len(msg) == 300


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1.5", Decimal("1.5")),
        (2, Decimal("2")),
        (None, Decimal("0")),
        ("", Decimal("0")),
        ("abc", Decimal("0")),
        ({}, Decimal("0")),
    ],
)
def test_to_money_never_raises(value, expected):
    assert _to_money(value) == expected


def test_a_venue_float_does_not_inherit_binary_error():
    """The reason this boundary exists at all.

    ``Decimal(0.1)`` is 0.1000000000000000055511151231257827..., which would
    carry the float's representation error into the exact ledger. Parsing via
    the string form is what keeps it out.
    """
    assert _to_money(0.1) == Decimal("0.1")
    assert _to_money(0.1) != Decimal(0.1)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1.5", Decimal("1.5")),
        (0, Decimal("0")),
        (None, None),
        ("", None),
        ("abc", None),
        ([], None),
    ],
)
def test_opt_money_distinguishes_absent_from_zero(value, expected):
    assert _opt_money(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("3"), "3"),
        (Decimal("3.00000000"), "3"),      # storage scale must not reach the venue
        (Decimal("19.99"), "19.99"),
        (Decimal("0.5"), "0.5"),
        (Decimal("1E+2"), "100"),          # nor may exponent notation
    ],
)
def test_wire_renders_a_quantity_the_venue_will_accept(value, expected):
    assert _wire(value) == expected
