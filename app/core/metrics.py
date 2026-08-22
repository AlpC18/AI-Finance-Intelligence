"""Custom Prometheus business metrics (registered on the default registry)."""
from prometheus_client import Counter

AI_CIRCUIT_BREAKER_TRIPS = Counter(
    "ai_circuit_breaker_trips_total",
    "Number of times the Anthropic circuit breaker tripped open.",
)

CACHE_EVENTS = Counter(
    "cache_events_total",
    "Cache hits and misses per backend.",
    labelnames=("backend", "result"),  # result: hit | miss
)


def record_cache_hit(backend: str) -> None:
    CACHE_EVENTS.labels(backend=backend, result="hit").inc()


def record_cache_miss(backend: str) -> None:
    CACHE_EVENTS.labels(backend=backend, result="miss").inc()
