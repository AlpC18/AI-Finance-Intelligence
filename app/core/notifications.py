"""Out-of-band alert delivery sinks: generic webhook POST + SMTP email.

Used as the fallback when a critical alert cannot reach a live WebSocket. Both
sinks are best-effort and fully defensive — a delivery failure is logged and
swallowed, never raised into the alert path. SMTP is blocking, so it is pushed to
a threadpool to keep the event loop free; the webhook uses async httpx.
"""
from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

import httpx
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings

logger = logging.getLogger("notifications")


class NotificationContact(BaseModel):
    """Where to reach a user out-of-band (any field may be empty)."""

    email: str = ""
    webhook_url: str = ""


class NotificationService:
    def __init__(
        self,
        settings: Settings,
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._settings = settings
        self._timeout = timeout
        self._transport = transport  # test seam (httpx.MockTransport)

    @property
    def enabled(self) -> bool:
        s = self._settings
        return bool(s.notify_webhook_url or (s.notify_email_enabled and s.smtp_host))

    async def notify(self, contact: NotificationContact, frame: dict) -> bool:
        """Deliver ``frame`` via any configured sink. Returns True if any succeeded."""
        delivered = False
        url = contact.webhook_url or self._settings.notify_webhook_url
        if url:
            delivered = await self._post_webhook(url, frame) or delivered
        if self._settings.notify_email_enabled and self._settings.smtp_host and contact.email:
            delivered = await self._send_email(contact.email, frame) or delivered
        return delivered

    async def _post_webhook(self, url: str, frame: dict) -> bool:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(url, json=frame)
                resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001 - best-effort sink
            logger.warning("Webhook notification failed: %s", exc)
            return False

    async def _send_email(self, to_addr: str, frame: dict) -> bool:
        try:
            await run_in_threadpool(self._send_email_sync, to_addr, frame)
            return True
        except Exception as exc:  # noqa: BLE001 - best-effort sink
            logger.warning("Email notification failed: %s", exc)
            return False

    def _send_email_sync(self, to_addr: str, frame: dict) -> None:
        s = self._settings
        msg = EmailMessage()
        msg["Subject"] = (
            f"[AI Finance] {frame.get('type', 'alert')}: {frame.get('symbol', '')}"
        ).strip()
        msg["From"] = s.smtp_from
        msg["To"] = to_addr
        msg.set_content(json.dumps(frame, ensure_ascii=False, indent=2))
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=self._timeout) as server:
            if s.smtp_use_tls:
                server.starttls()
            if s.smtp_username:
                server.login(s.smtp_username, s.smtp_password)
            server.send_message(msg)
