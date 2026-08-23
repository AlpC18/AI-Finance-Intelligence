"""WebSocket admission control — the server half of the 1008 rejection.

The browser side of this is already covered by the JS suite (a 1008 close is
terminal and never retried). What was untested is the decision that produces
it. `get_ws_user` is a second, independent authentication path: browsers
cannot set an Authorization header on a WS handshake, so the token arrives as
a query parameter and is validated by code that does NOT share a line with the
REST bearer dependency. A gap between the two is exactly the kind of thing
nobody notices — in particular whether logging out actually closes the socket
door behind you.

Every test here asserts the connection is REFUSED, which in Starlette surfaces
as WebSocketDisconnect on the client.
"""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings, get_settings
from app.core.security import create_access_token, create_refresh_token


def _token(headers: dict) -> str:
    return headers["Authorization"].split(" ", 1)[1]


def _refused(client, url: str) -> None:
    """A handshake the server must not admit."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url):
            pass


# ============================ TOKEN VALIDITY ===============================

def test_a_refresh_token_cannot_open_a_socket(client, auth_headers):
    """Only access tokens admit. A refresh token is for the token endpoint."""
    refresh = create_refresh_token("1", get_settings())

    _refused(client, f"/ws/insights/AAPL?token={refresh}")


def test_a_token_signed_by_someone_else_is_refused(client):
    forged = create_access_token(
        "1", Settings(anthropic_api_key="", jwt_secret="b" * 40)
    )

    _refused(client, f"/ws/insights/AAPL?token={forged}")


def test_an_expired_token_is_refused(client, auth_headers):
    expired = create_access_token(
        "1", Settings(anthropic_api_key="", access_token_expire_minutes=-5,
                      jwt_secret=get_settings().jwt_secret)
    )

    _refused(client, f"/ws/insights/AAPL?token={expired}")


def test_an_empty_token_parameter_is_refused(client):
    _refused(client, "/ws/insights/AAPL?token=")


def test_a_token_for_a_user_that_no_longer_exists_is_refused(client):
    """A valid signature is not enough; the account has to still be there."""
    orphan = create_access_token("999999", get_settings())

    _refused(client, f"/ws/insights/AAPL?token={orphan}")


@pytest.mark.parametrize("sub", ["not-a-number", ""])
def test_a_token_with_an_unusable_subject_is_refused(client, sub):
    bad = create_access_token(sub, get_settings())

    _refused(client, f"/ws/insights/AAPL?token={bad}")


# ========================== REVOCATION ACTUALLY BITES ======================

def test_logging_out_closes_the_websocket_door(client):
    """The property that matters most: revocation must reach the WS path.

    A token blocked on the REST side but still admitted here would leave a
    logged-out session streaming indefinitely.
    """
    client.post("/api/auth/register",
                json={"email": "ws-revoke@example.com", "password": "password123"})
    tokens = client.post(
        "/api/auth/login",
        json={"email": "ws-revoke@example.com", "password": "password123"},
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # The socket opens while the session is live.
    with client.websocket_connect(f"/ws/insights/AAPL?token={tokens['access_token']}") as ws:
        assert ws.receive_json()["type"] == "start"

    client.post("/api/auth/logout",
                json={"refresh_token": tokens["refresh_token"]}, headers=headers)

    _refused(client, f"/ws/insights/AAPL?token={tokens['access_token']}")


def test_one_users_logout_does_not_close_anothers_socket(client, make_user):
    """Revocation is per session, not a global switch."""
    client.post("/api/auth/register",
                json={"email": "ws-a@example.com", "password": "password123"})
    a = client.post("/api/auth/login",
                    json={"email": "ws-a@example.com", "password": "password123"}).json()
    b_headers = make_user("ws-b@example.com")

    client.post("/api/auth/logout", json={"refresh_token": a["refresh_token"]},
                headers={"Authorization": f"Bearer {a['access_token']}"})

    with client.websocket_connect(
        f"/ws/insights/AAPL?token={_token(b_headers)}"
    ) as ws:
        assert ws.receive_json()["type"] == "start", "the other user is unaffected"


# ======================= ADMITTED SESSIONS STILL WORK ======================

def test_an_admitted_socket_is_registered_for_alert_fan_out(client, auth_headers):
    """Admission is not just a gate: the socket has to reach the manager, or
    the alert scheduler has nowhere to push.

    The shared fixture hands out a fresh manager per resolution, so this test
    pins one instance to observe registration on.
    """
    from app.api.websockets import ConnectionManager
    from app.core.deps import get_connection_manager

    manager = ConnectionManager()
    client.app.dependency_overrides[get_connection_manager] = lambda: manager

    with client.websocket_connect(f"/ws/insights/AAPL?token={_token(auth_headers)}") as ws:
        ws.receive_json()  # start
        assert manager.user_count(1) == 1, "the socket registered for fan-out"

    assert manager.user_count(1) == 0, "and deregistered on the way out"


def test_a_symbol_is_normalised_before_it_reaches_the_stream(client, auth_headers):
    with client.websocket_connect(
        f"/ws/insights/  aapl  ?token={_token(auth_headers)}"
    ) as ws:
        assert ws.receive_json() == {"type": "start", "symbol": "AAPL"}
