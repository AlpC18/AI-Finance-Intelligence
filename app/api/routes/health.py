"""Liveness and readiness probes.

These answer two different questions and must not be collapsed into one:

- **Liveness** ("is this process alive?") decides whether the orchestrator
  RESTARTS the container. It therefore checks nothing external: a restart
  cannot fix a database outage, and wiring a dependency into liveness turns a
  backend blip into a rolling crash-loop of otherwise-healthy instances.

- **Readiness** ("should this instance be sent traffic?") decides routing, and
  is where dependencies belong.

The dependency split is deliberate. Postgres is REQUIRED: without it almost
every endpoint is a 500, so an instance that cannot reach it should be taken
out of rotation. Redis is OPTIONAL even when configured, because every consumer
of it in this app degrades rather than fails - the cache falls back to memory
and the JWT blocklist deliberately fails open (see ``token_store``) so an
outage cannot lock every user out. Failing readiness on Redis would pull EVERY
instance at once, since they share one Redis, converting a degraded service
into a total outage. So a Redis outage is reported as ``degraded`` and still
serves traffic.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session

from app.core.config import get_settings
from app.db.database import get_session

logger = logging.getLogger("health")
router = APIRouter(tags=["health"])

# A probe that hangs is as useless as one that fails, and worse to diagnose:
# the orchestrator sits on its own timeout learning nothing. Bound every check.
_PROBE_TIMEOUT_SECONDS = 2.0


class DependencyCheck(BaseModel):
    name: str
    ok: bool
    required: bool  # False -> a failure degrades the instance but keeps it serving
    detail: str = ""


class ReadinessReport(BaseModel):
    ready: bool  # every REQUIRED dependency answered
    degraded: bool  # some optional dependency did not
    checks: list[DependencyCheck] = []


@router.get("/health")
def health() -> dict:
    """Liveness. Unchanged shape: this is the long-standing public probe."""
    return {"status": "ok", "ai_enabled": get_settings().ai_enabled}


@router.get("/health/live")
def liveness() -> dict:
    """Explicit alias for /health, for orchestrators that want the pair named."""
    return {"status": "ok", "ai_enabled": get_settings().ai_enabled}


@router.get("/health/ready", response_model=ReadinessReport)
async def readiness(
    response: Response, session: Session = Depends(get_session)
) -> ReadinessReport:
    """Readiness. 503 when a required dependency is unreachable.

    The full report is returned on both paths - an operator reading a failing
    probe needs to know WHICH dependency went, and a 503 with an empty body
    makes them go find out from somewhere else.
    """
    checks = [await _check_database(session)]
    redis = await _check_redis()
    if redis is not None:
        checks.append(redis)

    ready = all(c.ok for c in checks if c.required)
    degraded = any(not c.ok for c in checks if not c.required)
    if not ready:
        response.status_code = 503
    return ReadinessReport(ready=ready, degraded=degraded, checks=checks)


async def _check_database(session: Session) -> DependencyCheck:
    """A real round-trip. Holding a Session object proves nothing: SQLAlchemy
    connects lazily, so a probe that only checks the object exists stays green
    against a database that is gone."""
    try:
        await asyncio.wait_for(
            asyncio.to_thread(lambda: session.exec(text("SELECT 1")).first()),
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - any failure to reach it is the answer
        logger.warning("Readiness: database unreachable: %s", exc)
        return DependencyCheck(
            name="database", ok=False, required=True, detail=_reason(exc)
        )
    return DependencyCheck(name="database", ok=True, required=True)


async def _check_redis() -> DependencyCheck | None:
    """None when Redis is not configured - an unused dependency is not a check."""
    settings = get_settings()
    if settings.cache_backend != "redis":
        return None
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(settings.redis_url)
        try:
            await asyncio.wait_for(client.ping(), timeout=_PROBE_TIMEOUT_SECONDS)
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness: Redis unreachable (degraded, still serving): %s", exc)
        return DependencyCheck(
            name="redis", ok=False, required=False, detail=_reason(exc)
        )
    return DependencyCheck(name="redis", ok=True, required=False)


def _reason(exc: Exception) -> str:
    """A short, bounded cause. Probe output is widely readable, so it names the
    failure class without echoing a driver message that may carry a DSN."""
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    return type(exc).__name__
