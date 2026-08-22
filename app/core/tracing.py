"""OpenTelemetry distributed tracing — optional, degraded-first.

The platform already emits Prometheus metrics and structured logs, but neither
can answer "where did *this* signal go?". Tracing stitches the hot path together:

    signal.generate  ->  order.execute  ->  fill.reconcile

so one trace id spans an AI signal, the broker order it produced, and the fill
that settled it -- across the request that placed it and the webhook/poll that
closed it.

Design constraints (identical to the rest of the app):

- **Optional dependency.** If ``opentelemetry`` is not installed, or tracing is
  disabled by config, every helper here degrades to a no-op. Nothing in the hot
  path may fail because a telemetry backend is missing.
- **No blocking export.** The SDK's ``BatchSpanProcessor`` exports off-thread.
  With no OTLP endpoint configured we install no exporter at all, so spans are
  created and dropped -- context propagation still works for log correlation.
- **Never raises.** Every entry point is wrapped; a telemetry failure is logged
  once and swallowed.

Usage::

    from app.core.tracing import span

    with span("order.execute", symbol="AAPL", side="buy") as s:
        ...
        s.set_attribute("order.id", broker_order.id)
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator, Optional

logger = logging.getLogger("tracing")

# Resolved once by configure_tracing(); None means "tracing off / unavailable".
_TRACER: Optional[Any] = None


class _NoopSpan:
    """Stand-in span so call sites need no `if tracing_enabled` branches."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D102
        return None

    def add_event(self, name: str, attributes: Optional[dict] = None) -> None:  # noqa: D102
        return None

    def record_exception(self, exc: BaseException) -> None:  # noqa: D102
        return None


_NOOP = _NoopSpan()


def tracing_enabled() -> bool:
    """True iff a real tracer was installed by ``configure_tracing``."""
    return _TRACER is not None


def configure_tracing(app: Any = None, settings: Any = None) -> bool:
    """Install a global tracer provider and instrument FastAPI/httpx.

    Returns True iff real tracing was activated. Safe to call more than once and
    safe to call when the OpenTelemetry packages are absent.
    """
    global _TRACER

    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    if not getattr(settings, "otel_enabled", False):
        return False
    if _TRACER is not None:
        return True

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "OTEL_ENABLED is set but the opentelemetry SDK is not installed; "
            "tracing stays off. Install the 'opentelemetry-*' requirements."
        )
        return False

    try:
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.otel_service_name,
                    "deployment.environment": settings.environment,
                }
            )
        )
        exporter = _build_exporter(settings)
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("ai-finance-intelligence")
        _instrument_libraries(app)
        logger.info(
            "Tracing enabled (service=%s, exporter=%s)",
            settings.otel_service_name,
            "otlp" if exporter is not None else "none",
        )
        return True
    except Exception as exc:  # noqa: BLE001 - telemetry must never block boot
        logger.warning("Tracing setup failed (%s); continuing without traces.", exc)
        _TRACER = None
        return False


def _build_exporter(settings: Any) -> Optional[Any]:
    """OTLP/HTTP exporter when an endpoint is configured, else None (spans dropped)."""
    endpoint = (getattr(settings, "otel_exporter_otlp_endpoint", "") or "").strip()
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=endpoint)
    except Exception as exc:  # noqa: BLE001 - fall back to no exporter
        logger.warning("OTLP exporter unavailable (%s); spans will be dropped.", exc)
        return None


def _instrument_libraries(app: Any) -> None:
    """Auto-instrument inbound FastAPI requests and outbound httpx calls."""
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics,/health")
        except Exception as exc:  # noqa: BLE001
            logger.warning("FastAPI instrumentation skipped: %s", exc)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as exc:  # noqa: BLE001
        logger.warning("httpx instrumentation skipped: %s", exc)


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a span, or yield a no-op when tracing is off/unavailable.

    Exceptions propagate (the caller's error handling is unchanged) but are
    recorded on the span first, so a failed order shows up in the trace.
    """
    tracer = _TRACER
    if tracer is None:
        yield _NOOP
        return
    try:
        cm = tracer.start_as_current_span(name)
    except Exception as exc:  # noqa: BLE001 - never break the caller
        logger.debug("span(%s) failed to start: %s", name, exc)
        yield _NOOP
        return
    with cm as active:
        for key, value in attributes.items():
            with contextlib.suppress(Exception):
                active.set_attribute(key, value)
        yield active


def current_trace_id() -> str:
    """Hex trace id of the active span, or "" — lets logs join traces."""
    if _TRACER is None:
        return ""
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if not ctx.is_valid:
            return ""
        return format(ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001
        return ""


def reset_for_tests() -> None:
    """Drop the installed tracer (test isolation only)."""
    global _TRACER
    _TRACER = None
