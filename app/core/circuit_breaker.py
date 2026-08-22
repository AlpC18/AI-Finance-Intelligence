"""Minimal async-safe circuit breaker for guarding a flaky dependency."""
import time
from typing import Literal

from app.core.metrics import AI_CIRCUIT_BREAKER_TRIPS

State = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0) -> None:
        self._threshold = failure_threshold
        self._recovery = recovery_timeout
        self._failures = 0
        self._opened_at = 0.0
        self._state: State = "closed"

    @property
    def state(self) -> State:
        if self._state == "open" and time.monotonic() - self._opened_at >= self._recovery:
            self._state = "half_open"
        return self._state

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold and self._state != "open":
            self._state = "open"
            self._opened_at = time.monotonic()
            AI_CIRCUIT_BREAKER_TRIPS.inc()   # business metric: breaker tripped
