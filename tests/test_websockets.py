"""WebSocket layer: ConnectionManager unit behavior + JWT-guarded streaming route."""
import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.websockets import ConnectionManager


class _FakeWS:
    """Minimal stand-in for a Starlette WebSocket used by ConnectionManager tests."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self._fail = fail

    async def accept(self):
        return None

    async def send_json(self, message):
        if self._fail:
            raise RuntimeError("socket dead")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_manager_send_to_user_and_disconnect():
    manager = ConnectionManager()
    ws = _FakeWS()
    await manager.connect(7, ws)
    assert manager.user_count(7) == 1

    sent = await manager.send_to_user(7, {"type": "alert", "symbol": "AAPL"})
    assert sent == 1 and ws.sent[0]["symbol"] == "AAPL"

    await manager.disconnect(7, ws)
    assert manager.user_count(7) == 0
    assert await manager.send_to_user(7, {"x": 1}) == 0  # no live sockets


@pytest.mark.asyncio
async def test_manager_prunes_dead_socket_on_failed_send():
    manager = ConnectionManager()
    dead = _FakeWS(fail=True)
    await manager.connect(9, dead)
    sent = await manager.send_to_user(9, {"type": "alert"})
    assert sent == 0
    assert manager.user_count(9) == 0  # auto-pruned


def _token(headers: dict) -> str:
    return headers["Authorization"].split(" ", 1)[1]


def test_ws_rejects_without_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/insights/AAPL"):
            pass


def test_ws_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/insights/AAPL?token=not-a-jwt"):
            pass


def test_ws_streams_grounded_insight_for_authenticated_user(client, auth_headers):
    token = _token(auth_headers)
    with client.websocket_connect(f"/ws/insights/AAPL?token={token}") as ws:
        start = ws.receive_json()
        assert start == {"type": "start", "symbol": "AAPL"}

        token_frame = ws.receive_json()
        assert token_frame["type"] == "token"
        assert token_frame["symbol"] == "AAPL"
        assert isinstance(token_frame["text"], str) and token_frame["text"]

        end = ws.receive_json()
        assert end["type"] == "end"
        assert end["symbol"] == "AAPL"
        assert "citations" in end
        assert end["disclaimer"] == "Bu bir yatirim tavsiyesi degildir."


# ===================== STREAM BEHAVIOUR ON A LIVE SOCKET =====================
#
# The client half of these frames is covered by the JS suite; what follows is
# the server that emits them. The through-line is that a socket must fail
# loudly in-band (an `error` frame the UI can render) rather than by vanishing,
# because a dropped socket is indistinguishable from a network blip and the
# client will reconnect into the same failure.

def _tok(headers: dict) -> str:
    return headers["Authorization"].split(" ", 1)[1]


def test_the_client_can_re_analyse_another_symbol_on_the_same_socket(client, auth_headers):
    """The re-analysis loop is what makes the connection worth keeping open."""
    with client.websocket_connect(f"/ws/insights/AAPL?token={_tok(auth_headers)}") as ws:
        assert ws.receive_json()["symbol"] == "AAPL"
        while ws.receive_json()["type"] != "end":
            pass

        ws.send_json({"symbol": "MSFT"})

        assert ws.receive_json() == {"type": "start", "symbol": "MSFT"}


def test_an_empty_re_analysis_request_falls_back_to_the_original_symbol(client, auth_headers):
    with client.websocket_connect(f"/ws/insights/AAPL?token={_tok(auth_headers)}") as ws:
        while ws.receive_json()["type"] != "end":
            pass

        ws.send_json({})

        assert ws.receive_json() == {"type": "start", "symbol": "AAPL"}


def test_a_market_data_outage_is_reported_in_band_not_by_dropping(client, auth_headers):
    """The UI must be able to say WHY; a silent close just triggers a reconnect."""
    from app.core.deps import get_market_service

    class _Down:
        async def get_market_data(self, symbol):
            raise RuntimeError("upstream feed unavailable")

    client.app.dependency_overrides[get_market_service] = lambda: _Down()

    with client.websocket_connect(f"/ws/insights/AAPL?token={_tok(auth_headers)}") as ws:
        assert ws.receive_json() == {"type": "start", "symbol": "AAPL"}
        assert ws.receive_json() == {
            "type": "error", "symbol": "AAPL", "detail": "market_data_unavailable",
        }


def test_an_unexpected_failure_closes_the_socket_instead_of_leaking_it(client, auth_headers):
    """Whatever breaks, the connection must be released, not left half-open."""
    from app.api.websockets import ConnectionManager
    from app.core.deps import get_ai_service, get_connection_manager

    class _Exploding:
        async def stream_insight(self, *a, **kw):
            raise RuntimeError("model transport died mid-stream")
            yield  # pragma: no cover - makes this an async generator

    manager = ConnectionManager()
    client.app.dependency_overrides[get_connection_manager] = lambda: manager
    client.app.dependency_overrides[get_ai_service] = lambda: _Exploding()

    with client.websocket_connect(
        f"/ws/insights/AAPL?token={_tok(auth_headers)}"
    ) as ws:
        assert ws.receive_json() == {"type": "start", "symbol": "AAPL"}
        # The client must be TOLD, not merely dropped: a socket that goes quiet
        # is indistinguishable from a slow one, and the UI would keep waiting.
        assert ws.receive_json() == {
            "type": "error", "symbol": "AAPL", "detail": "stream_failed",
        }

    assert manager.user_count(1) == 0, "the socket was deregistered on the way out"
