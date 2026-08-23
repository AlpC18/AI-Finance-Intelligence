"""Liveness vs readiness — the probe that used to lie.

`/health` returned `{"status": "ok"}` unconditionally: no dependency was ever
touched. Both docker-compose and Render probed exactly that, so
`docker compose up --wait` went green the instant uvicorn bound a port, and a
Render deploy was promoted regardless of whether the instance could reach its
database. An orchestrator would happily route live traffic to a process that
could only answer 500s.

The split defended here:

  liveness  .... never touches a dependency. It decides RESTARTS, and no restart
                 fixes a database outage - wiring one in turns a backend blip
                 into a crash-loop of healthy instances.
  readiness .... decides ROUTING. Postgres is required (503 without it); Redis
                 is not, because every consumer degrades instead of failing, and
                 failing readiness on a SHARED Redis would pull every instance at
                 once and turn a degraded service into an outage.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session

from app.api.routes import health as health_module
from app.core.config import Settings, get_settings
from app.db.database import get_session


class _DeadSession:
    """A session whose every query fails, as one does when the DB is gone."""

    def exec(self, *_args, **_kwargs):
        raise OSError("connection refused")


class _HangingSession:
    def exec(self, *_args, **_kwargs):
        import time

        time.sleep(5)  # longer than the probe's own bound


@pytest.fixture
def memory_backend(monkeypatch):
    """Pin the cache backend to memory for the duration of a test.

    Without this the suite reads whatever CACHE_BACKEND the environment sets -
    CI sets `redis` - so a test asserting on which dependencies get probed would
    quietly mean something different there than it does locally, and would start
    depending on a live Redis to pass.
    """
    monkeypatch.setattr(
        health_module,
        "get_settings",
        lambda: Settings(anthropic_api_key="", cache_backend="memory"),
    )


def _kill_db(client, session_cls=_DeadSession) -> None:
    client.app.dependency_overrides[get_session] = lambda: session_cls()


def _check(body: dict, name: str) -> dict:
    return next(c for c in body["checks"] if c["name"] == name)


# ============================== LIVENESS ===================================

def test_liveness_answers_without_touching_a_dependency(client):
    """The property: a dead database must NOT make this fail, or the
    orchestrator restarts instances that have nothing wrong with them."""
    _kill_db(client)

    for path in ("/health", "/health/live"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.json()["status"] == "ok"


def test_health_keeps_its_long_standing_shape(client):
    """This is the public probe; changing its body breaks existing consumers."""
    body = client.get("/health").json()

    assert body == {"status": "ok", "ai_enabled": False}


# ============================== READINESS ==================================

def test_readiness_is_green_when_the_database_answers(client, memory_backend):
    res = client.get("/health/ready")

    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is True and body["degraded"] is False
    assert _check(body, "database") == {
        "name": "database", "ok": True, "required": True, "detail": ""
    }


def test_readiness_fails_with_503_when_the_database_is_gone(client):
    """The headline fix. This is the case the old probe scored as healthy."""
    _kill_db(client)

    res = client.get("/health/ready")

    assert res.status_code == 503
    body = res.json()
    assert body["ready"] is False
    assert _check(body, "database")["ok"] is False


def test_a_failing_probe_still_names_the_dependency_that_went(client):
    """A 503 with an empty body sends the operator somewhere else to find out."""
    _kill_db(client)

    body = client.get("/health/ready").json()

    assert _check(body, "database")["detail"] == "OSError"


def test_the_database_probe_makes_a_real_round_trip(client):
    """Holding a Session proves nothing - SQLAlchemy connects lazily, so a probe
    that only checks the object exists stays green against a database that is
    gone. Assert a query was actually issued."""
    issued = []

    class _Recording:
        def exec(self, statement, *a, **kw):
            issued.append(str(statement))
            return _Result()

    class _Result:
        def first(self):
            return (1,)

    client.app.dependency_overrides[get_session] = lambda: _Recording()

    assert client.get("/health/ready").status_code == 200
    assert issued and "SELECT 1" in issued[0]


def test_a_hanging_database_times_out_instead_of_hanging_the_probe(client, monkeypatch):
    """An orchestrator sitting on its own timeout learns nothing. Bound it."""
    monkeypatch.setattr(health_module, "_PROBE_TIMEOUT_SECONDS", 0.05)
    _kill_db(client, _HangingSession)

    res = client.get("/health/ready")

    assert res.status_code == 503
    assert _check(res.json(), "database")["detail"] == "timeout"


# ===================== REDIS IS DEGRADED, NOT DOWN =========================

def test_redis_is_not_probed_when_it_is_not_configured(client, memory_backend):
    """An unused dependency is not a check. The default backend is memory."""
    body = client.get("/health/ready").json()

    assert [c["name"] for c in body["checks"]] == ["database"]


async def test_an_unreachable_redis_degrades_without_failing_readiness():
    """The judgement call, pinned. Every consumer of Redis here falls back - the
    cache to memory, the JWT blocklist deliberately fail-open. Since all
    instances share one Redis, failing readiness would pull the whole fleet and
    convert a degraded service into a total outage.
    """
    check = await _redis_check_with(port=1)  # nothing is listening

    assert check is not None
    assert check.ok is False
    assert check.required is False, "a Redis outage must not remove the instance"


async def _redis_check_with(port: int):
    """Run the real Redis probe against a configured-but-dead Redis."""
    import app.api.routes.health as h

    original = h.get_settings
    h.get_settings = lambda: Settings(  # type: ignore[assignment]
        anthropic_api_key="",
        cache_backend="redis",
        redis_url=f"redis://127.0.0.1:{port}/0",
    )
    try:
        return await asyncio.wait_for(h._check_redis(), timeout=10)
    finally:
        h.get_settings = original  # type: ignore[assignment]


def test_readiness_reports_degraded_but_serves_when_only_optional_deps_fail(client):
    """Assembled at the report level: an optional failure sets `degraded` while
    `ready` stays true and the status stays 200."""
    from app.api.routes.health import DependencyCheck

    async def _fake_redis():
        return DependencyCheck(name="redis", ok=False, required=False, detail="timeout")

    client.app.dependency_overrides[get_session] = lambda: _OkSession()
    original = health_module._check_redis
    health_module._check_redis = _fake_redis
    try:
        res = client.get("/health/ready")
    finally:
        health_module._check_redis = original

    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is True, "still serving"
    assert body["degraded"] is True, "but visibly impaired"


class _OkSession:
    def exec(self, *_a, **_kw):
        class _R:
            def first(self):
                return (1,)

        return _R()


def test_a_required_failure_outranks_an_optional_one(client):
    """Both broken is not ready, whatever the optional check says."""
    from app.api.routes.health import DependencyCheck

    async def _fake_redis():
        return DependencyCheck(name="redis", ok=False, required=False)

    _kill_db(client)
    original = health_module._check_redis
    health_module._check_redis = _fake_redis
    try:
        res = client.get("/health/ready")
    finally:
        health_module._check_redis = original

    assert res.status_code == 503
    body = res.json()
    assert body["ready"] is False and body["degraded"] is True
