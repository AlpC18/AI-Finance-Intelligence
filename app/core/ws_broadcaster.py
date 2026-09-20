"""Cross-worker WebSocket fan-out via Redis Pub/Sub.

The in-memory ConnectionManager only knows sockets on THIS worker. The broadcaster
publishes alert frames to a Redis channel; every worker runs a subscriber that
delivers incoming frames to its own local sockets — so an alert raised on the
leader worker reaches clients connected to any worker.

Defensive: with a non-redis cache backend, or if Redis is unreachable, it falls
back to local-only delivery (the previous single-process behavior) and never
raises into the caller.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Optional, Protocol

from app.core.config import Settings
from app.core.metrics import record_alert_fallback
from app.core.notifications import NotificationContact, NotificationService

logger = logging.getLogger("ws_broadcaster")

_CHANNEL = "afi:ws:alerts"
_PRESENCE_PREFIX = "afi:ws:presence:"


def presence_key(user_id: int) -> str:
    """Redis key holding the live-socket refcount for a user (cross-worker presence)."""
    return f"{_PRESENCE_PREFIX}{user_id}"


class _Manager(Protocol):
    async def send_to_user(self, user_id: int, message: dict) -> int: ...
    def user_count(self, user_id: int) -> int: ...


class WsBroadcaster:
    def __init__(
        self,
        manager: _Manager,
        settings: Settings,
        redis: Optional[Any] = None,
        notifier: Optional[NotificationService] = None,
    ) -> None:
        self._manager = manager
        self._settings = settings
        self._redis = redis            # injectable for tests
        self._notifier = notifier      # out-of-band fallback sink
        self._pubsub: Optional[Any] = None
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """Subscribe this worker to the alert channel (no-op in local-only mode)."""
        if self._settings.cache_backend != "redis":
            return
        try:
            if self._redis is None:
                self._redis = await self._connect()
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(_CHANNEL)
            self._task = asyncio.create_task(self._listen())
            logger.info("WS broadcaster subscribed to %s", _CHANNEL)
        except Exception as exc:  # noqa: BLE001 - Redis down -> local-only fallback
            logger.warning("WS broadcaster unavailable (%s); local-only delivery.", exc)
            self._redis = None
            self._pubsub = None

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()

    async def publish_alert(self, user_id: int, frame: dict) -> None:
        """Fan an alert frame out to all workers, or deliver locally on failure."""
        if self._redis is None:  # local-only mode
            await self._manager.send_to_user(user_id, frame)
            return
        try:
            payload = json.dumps({"user_id": user_id, "frame": frame})
            await self._redis.publish(_CHANNEL, payload)
        except Exception as exc:  # noqa: BLE001 - deliver locally so it isn't lost
            logger.warning("WS publish failed (%s); local delivery only.", exc)
            await self._manager.send_to_user(user_id, frame)

    async def publish_activity(self, user_id: int, frame: dict) -> None:
        """Publish a non-critical account timeline update without escalation."""
        await self.publish_alert(user_id, frame)

    async def publish_event(self, frame: dict) -> None:
        """Fan a market-wide event frame out to ALL sockets on every worker."""
        if self._redis is None:  # local-only mode
            await self._manager.broadcast(frame)
            return
        try:
            payload = json.dumps({"broadcast": True, "frame": frame})
            await self._redis.publish(_CHANNEL, payload)
        except Exception as exc:  # noqa: BLE001 - broadcast locally so it isn't lost
            logger.warning("WS event publish failed (%s); local broadcast only.", exc)
            await self._manager.broadcast(frame)

    async def deliver_alert(
        self,
        user_id: int,
        frame: dict,
        contact: Optional[NotificationContact] = None,
    ) -> bool:
        """Deliver a CRITICAL alert; fall back to email/webhook if the user is offline.

        Returns True iff the out-of-band fallback fired. The alert is always sent
        to any live WebSocket first (locally or cross-worker); only when no live
        socket is confirmed do we escalate to the notifier so the user isn't left
        unaware of a kill-switch trip or high-severity event.
        """
        online = await self._deliver_and_check(user_id, frame)
        if online or contact is None or self._notifier is None:
            return False
        try:
            delivered = await self._notifier.notify(contact, frame)
        except Exception as exc:  # noqa: BLE001 - fallback must never raise
            logger.warning("Alert fallback notify failed for user %s: %s", user_id, exc)
            record_alert_fallback(False)
            return False
        record_alert_fallback(delivered)
        return delivered

    async def _deliver_and_check(self, user_id: int, frame: dict) -> bool:
        """Send the frame and report whether the user has a confirmed live socket."""
        if self._redis is None:  # local-only: delivery count IS the presence signal
            sent = await self._manager.send_to_user(user_id, frame)
            return sent > 0
        try:
            payload = json.dumps({"user_id": user_id, "frame": frame})
            await self._redis.publish(_CHANNEL, payload)
        except Exception as exc:  # noqa: BLE001 - deliver locally so it isn't lost
            logger.warning("WS deliver failed (%s); local delivery only.", exc)
            sent = await self._manager.send_to_user(user_id, frame)
            return sent > 0
        return await self._presence_online(user_id)

    async def _presence_online(self, user_id: int) -> bool:
        """Cross-worker presence via the Redis refcount. Fail-open: unknown == offline
        so a critical alert still escalates to the out-of-band fallback."""
        try:
            value = await self._redis.get(presence_key(user_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Presence lookup failed for user %s: %s", user_id, exc)
            return False
        try:
            return value is not None and int(value) > 0
        except (TypeError, ValueError):
            return False

    async def _connect(self) -> Any:
        from redis.asyncio import Redis

        return Redis.from_url(self._settings.redis_url)

    async def _listen(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "message":
                    continue
                await self._dispatch(message.get("data"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the worker alive
            logger.warning("WS subscriber loop error: %s", exc)

    async def _dispatch(self, raw: Any) -> None:
        """Parse a published frame and deliver to this worker's local sockets."""
        try:
            data = json.loads(raw)
            frame = data["frame"]
        except (TypeError, ValueError, KeyError):
            return
        if "user_id" in data:
            try:
                await self._manager.send_to_user(int(data["user_id"]), frame)
            except (TypeError, ValueError):
                return
        elif data.get("broadcast"):
            await self._manager.broadcast(frame)
