"""Circuit breaker + strict-validation behavior of the AI layer."""
import pytest

from app.core.circuit_breaker import CircuitBreaker
from app.core.config import Settings
from app.services.ai_service import AIService, _extract_json


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=999)
    assert cb.allow()
    cb.record_failure()
    assert cb.allow()  # 1 failure, still closed
    cb.record_failure()
    assert not cb.allow()  # 2 failures -> open
    cb.record_success()
    assert cb.allow()  # reset


def test_extract_json_tolerates_prose_wrapping():
    assert _extract_json('noise {"a": 1} tail') == {"a": 1}
    assert _extract_json("not json") is None


@pytest.mark.asyncio
async def test_disabled_ai_returns_none_not_crash():
    svc = AIService(Settings(anthropic_api_key=""))
    assert svc.enabled is False
    assert await svc.market_signal("AAPL", {}, {}) is None
