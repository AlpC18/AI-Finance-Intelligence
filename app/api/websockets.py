"""WebSocket layer: JWT-authenticated streaming insights + a broadcast manager.

Single-process, in-memory ``ConnectionManager``. For multi-worker deployments,
back it with Redis pub/sub — the manager interface stays identical, so nothing
else changes. WebSocket auth reuses the exact JWT decode + blocklist path as the
REST bearer flow.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlmodel import Session

from app.core.metrics import record_ws_disconnect
from app.core.ws_broadcaster import presence_key

_PRESENCE_TTL = 3600  # seconds; refreshed on every connect, self-heals on crashes

from app.core.config import get_settings
from app.core.deps import (
    get_ai_service,
    get_connection_manager,
    get_market_service,
    get_news_service,
    get_token_store,
)
from app.core.security import decode_token
from app.core.token_store import TokenBlocklist
from app.db.database import get_session
from app.models.user import User
from app.services.ai_service import AIService
from app.services.market_service import MarketService
from app.services.news_service import NewsService

logger = structlog.get_logger("ws")
router = APIRouter()

_DISCLAIMER = "Bu bir yatirim tavsiyesi degildir."


class ConnectionManager:
    """Tracks live sockets per user and pushes messages to them.

    Every mutation is guarded by an ``asyncio.Lock`` so the scheduler pushing an
    alert and a client connecting can never corrupt the registry. Dead sockets
    are pruned on the first failed send.
    """

    def __init__(self, redis: Optional[Any] = None) -> None:
        self._active: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._redis = redis  # optional: cross-worker presence refcount

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._active[user_id].add(websocket)
        logger.info("ws_connected", user_id=user_id, count=len(self._active[user_id]))
        await self._presence_incr(user_id)

    async def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        removed = False
        async with self._lock:
            conns = self._active.get(user_id)
            if conns and websocket in conns:
                conns.discard(websocket)
                removed = True
                if not conns:
                    self._active.pop(user_id, None)
        logger.info("ws_disconnected", user_id=user_id)
        if removed:
            await self._presence_decr(user_id)

    async def _presence_incr(self, user_id: int) -> None:
        if self._redis is None:
            return
        with contextlib.suppress(Exception):  # presence is best-effort
            key = presence_key(user_id)
            await self._redis.incr(key)
            await self._redis.expire(key, _PRESENCE_TTL)

    async def _presence_decr(self, user_id: int) -> None:
        if self._redis is None:
            return
        with contextlib.suppress(Exception):
            if int(await self._redis.decr(presence_key(user_id))) <= 0:
                await self._redis.delete(presence_key(user_id))

    async def send_to_user(self, user_id: int, message: dict) -> int:
        """Push to every live socket of one user; prune failures. Returns count sent."""
        async with self._lock:
            conns = list(self._active.get(user_id, ()))
        sent = 0
        for ws in conns:
            try:
                await ws.send_json(message)
                sent += 1
            except Exception:  # noqa: BLE001 - drop dead sockets, never raise to caller
                await self.disconnect(user_id, ws)
        return sent

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            targets = [
                (uid, ws) for uid, conns in self._active.items() for ws in conns
            ]
        for uid, ws in targets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                await self.disconnect(uid, ws)

    def user_count(self, user_id: int) -> int:
        return len(self._active.get(user_id, ()))


async def get_ws_user(
    websocket: WebSocket,
    session: Session = Depends(get_session),
    blocklist: TokenBlocklist = Depends(get_token_store),
) -> User | None:
    """Resolve the user from a ``?token=<access JWT>`` query param, else ``None``.

    Browsers cannot set Authorization headers on the WebSocket handshake, so the
    access token is passed as a query parameter and validated with the same rules
    as the REST bearer path: signature valid, ``type == access``, not revoked,
    and the user still exists.
    """
    token = websocket.query_params.get("token")
    if not token:
        return None
    payload = decode_token(token, get_settings())
    if not payload or payload.get("type") != "access":
        return None
    jti = payload.get("jti")
    if jti and await blocklist.is_blocked(jti):
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    return session.get(User, user_id)


@router.websocket("/ws/account")
async def account_ws(
    websocket: WebSocket,
    user: User | None = Depends(get_ws_user),
    manager: ConnectionManager = Depends(get_connection_manager),
) -> None:
    """A push-only channel for account events: fills, order transitions, halts.

    Everything else surfaced only on poll, which is the wrong shape for events
    the user needs to know about the moment they happen - a fill, or an account
    being flattened by its loss limit.

    The socket carries no request protocol. It registers with the
    ConnectionManager and then blocks on receive purely to notice the client
    going away: anything the client sends is ignored rather than interpreted,
    so this channel can never become a second, unaudited command surface.
    """
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await manager.connect(user.id, websocket)
    try:
        await websocket.send_json({"type": "ready", "channel": "account"})
        while True:
            await websocket.receive_text()  # ignored; this is a read-only channel
    except WebSocketDisconnect:
        record_ws_disconnect("client")
    except Exception as exc:  # noqa: BLE001 - never leak a socket
        record_ws_disconnect("error")
        logger.warning("ws_account_error", error=str(exc))
        with contextlib.suppress(Exception):
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        await manager.disconnect(user.id, websocket)


@router.websocket("/ws/insights/{symbol}")
async def insights_ws(
    websocket: WebSocket,
    symbol: str,
    user: User | None = Depends(get_ws_user),
    manager: ConnectionManager = Depends(get_connection_manager),
    market: MarketService = Depends(get_market_service),
    ai: AIService = Depends(get_ai_service),
    news: NewsService = Depends(get_news_service),
) -> None:
    """Stream grounded AI analysis for a symbol, token-by-token, over a WS.

    Protocol: server pushes ``{type: start}``, then N ``{type: token}`` frames,
    then ``{type: end, citations, disclaimer}``. The client may send
    ``{"symbol": "MSFT"}`` afterwards to re-analyze another symbol on the same
    connection. The socket is also registered with the ConnectionManager so the
    alert scheduler can push live notifications to it.
    """
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await manager.connect(user.id, websocket)
    try:
        await _stream_symbol(websocket, symbol, market, ai, news)
        while True:  # let the client request re-analysis of other symbols
            msg = await websocket.receive_json()
            await _stream_symbol(websocket, msg.get("symbol") or symbol, market, ai, news)
    except WebSocketDisconnect:
        record_ws_disconnect("client")  # normal close: the client went away
    except Exception as exc:  # noqa: BLE001 - never leak a socket on unexpected error
        record_ws_disconnect("error")   # a spike here is an operator-grade signal
        logger.warning("ws_stream_error", error=str(exc))
        # Say so before going away. Logging alone left the client holding an
        # open socket with no frame and no close, where a failed stream is
        # indistinguishable from a slow one - so the UI waits instead of
        # surfacing the error or reconnecting. Both sends are best-effort:
        # the socket may already be gone, and that is not a second failure.
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"type": "error", "symbol": symbol, "detail": "stream_failed"}
            )
        with contextlib.suppress(Exception):
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    finally:
        await manager.disconnect(user.id, websocket)


async def _stream_symbol(
    websocket: WebSocket,
    symbol: str,
    market: MarketService,
    ai: AIService,
    news: NewsService,
) -> None:
    symbol = symbol.upper().strip()
    await websocket.send_json({"type": "start", "symbol": symbol})
    try:
        data = await market.get_market_data(symbol)
    except Exception:  # noqa: BLE001 - bad symbol / provider outage
        await websocket.send_json(
            {"type": "error", "symbol": symbol, "detail": "market_data_unavailable"}
        )
        return
    context = await news.retrieve_context(symbol, k=4)
    async for token in ai.stream_insight(
        symbol, data.quote.model_dump(), data.indicators.model_dump(), context
    ):
        await websocket.send_json({"type": "token", "symbol": symbol, "text": token})
    await websocket.send_json(
        {
            "type": "end",
            "symbol": symbol,
            "citations": [c.citation() for c in context],
            "disclaimer": _DISCLAIMER,
        }
    )
