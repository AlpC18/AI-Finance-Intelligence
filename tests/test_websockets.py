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
