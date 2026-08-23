"""Custom Prometheus business metrics (registered on the default registry).

Two audiences are served here:

- **User-facing signals** already exist as alerts pushed over WebSocket.
- **Operator-facing signals** are these counters. They are the series the
  Prometheus rules in ``config/prometheus/alerts.yml`` fire on: order rejects,
  circuit-breaker trips, provider failovers, WebSocket disconnect storms and
  lost alert deliveries.

Every recorder is a thin, exception-free wrapper so instrumentation can be added
to a hot path without adding a failure mode to it.
"""
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

ORDERS = Counter(
    "trade_orders_total",
    "Order submissions by outcome (a sustained reject rate means broken execution).",
    labelnames=("outcome", "reason"),  # accepted | rejected | canceled | replaced
)

RISK_HALTS = Counter(
    "trade_risk_halts_total",
    "Accounts automatically halted by the daily-loss limit and flattened.",
)

FILLS_RECONCILED = Counter(
    "trade_fills_reconciled_total",
    "Fills written to the ledger, by ingestion path (webhook fast path vs poll backstop).",
    labelnames=("source",),  # source: webhook | poll
)

PROVIDER_FAILOVERS = Counter(
    "market_provider_failovers_total",
    "Market-data provider failures that forced a failover to the next source.",
    labelnames=("provider",),
)

WS_DISCONNECTS = Counter(
    "ws_disconnects_total",
    "WebSocket disconnects by reason (a spike means a fan-out or network problem).",
    labelnames=("reason",),  # reason: client | error
)

ALERT_FALLBACKS = Counter(
    "alert_fallback_deliveries_total",
    "Out-of-band alert deliveries attempted when no live socket was reachable.",
    labelnames=("result",),  # result: delivered | failed
)


def record_cache_hit(backend: str) -> None:
    CACHE_EVENTS.labels(backend=backend, result="hit").inc()


def record_cache_miss(backend: str) -> None:
    CACHE_EVENTS.labels(backend=backend, result="miss").inc()


def record_order_accepted() -> None:
    ORDERS.labels(outcome="accepted", reason="none").inc()


def record_order_rejected(reason: str) -> None:
    """``reason`` is a low-cardinality class (risk, notional, provider, ...)."""
    ORDERS.labels(outcome="rejected", reason=reason or "unknown").inc()


def record_order_canceled(source: str) -> None:
    """``source`` separates a routine single cancel from a kill-switch flatten.

    The distinction is the whole point: a burst of ``kill_switch`` cancels is an
    operator pulling the handle, which is a different page for whoever is on
    call than a user tidying up one resting order.
    """
    ORDERS.labels(outcome="canceled", reason=source or "unknown").inc()


def record_order_replaced() -> None:
    """Amendments are counted apart from submissions: a rising replace rate is a
    pricing problem, not a demand one, and averaging them together hides both."""
    ORDERS.labels(outcome="replaced", reason="none").inc()


def record_risk_halt_tripped() -> None:
    """An account hit its daily-loss limit and was flattened automatically.

    Operator-grade: a cluster of these is a market event, and a flat zero while
    limits are configured means the sweep has stopped running."""
    RISK_HALTS.inc()


def record_fill_reconciled(source: str) -> None:
    FILLS_RECONCILED.labels(source=source).inc()


def record_provider_failover(provider: str) -> None:
    PROVIDER_FAILOVERS.labels(provider=provider).inc()


def record_ws_disconnect(reason: str) -> None:
    WS_DISCONNECTS.labels(reason=reason).inc()


def record_alert_fallback(delivered: bool) -> None:
    ALERT_FALLBACKS.labels(result="delivered" if delivered else "failed").inc()
