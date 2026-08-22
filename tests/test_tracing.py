"""Tracing must be optional, non-breaking, and actually cover the hot path.

The contract under test:
  1. With tracing off, ``span()`` is a usable no-op — no branch at call sites.
  2. With tracing on, real spans are produced for signal -> order -> fill and
     carry the attributes an operator needs.
  3. Nothing in the telemetry layer can raise into business code.
"""
from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.tracing import (
    configure_tracing,
    current_trace_id,
    reset_for_tests,
    span,
    tracing_enabled,
)


@pytest.fixture(autouse=True)
def _clean_tracer():
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture
def recorded_spans():
    """Activate tracing against an in-memory exporter and yield the span list."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    import app.core.tracing as tracing_module

    tracing_module._TRACER = provider.get_tracer("test")
    yield exporter
    exporter.clear()


# --- 1. Degraded-first: tracing off ---
def test_span_is_a_noop_when_tracing_disabled():
    assert tracing_enabled() is False
    with span("anything", foo="bar") as sp:
        sp.set_attribute("k", "v")   # must not raise
        sp.add_event("e")
        sp.record_exception(ValueError("x"))
    assert current_trace_id() == ""


def test_configure_tracing_off_by_default():
    assert configure_tracing(app=None, settings=Settings()) is False
    assert tracing_enabled() is False


def test_configure_tracing_survives_missing_sdk(monkeypatch):
    """OTEL_ENABLED with the SDK absent must warn and continue, not crash boot."""
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError("simulated: SDK not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert configure_tracing(app=None, settings=Settings(otel_enabled=True)) is False
    assert tracing_enabled() is False


def test_configure_tracing_activates_with_the_sdk_installed():
    """The real activation path: provider installed, tracer resolved, spans usable."""
    assert configure_tracing(app=None, settings=Settings(otel_enabled=True)) is True
    assert tracing_enabled() is True
    with span("smoke") as sp:
        sp.set_attribute("k", "v")
        assert len(current_trace_id()) == 32  # a real span context is active


def test_configure_tracing_is_idempotent():
    settings = Settings(otel_enabled=True)
    assert configure_tracing(app=None, settings=settings) is True

    import app.core.tracing as tracing_module

    first = tracing_module._TRACER
    assert configure_tracing(app=None, settings=settings) is True
    assert tracing_module._TRACER is first  # not rebuilt on a second call


def test_configure_tracing_reads_settings_from_config_when_omitted(monkeypatch):
    import app.core.tracing as tracing_module

    monkeypatch.setattr(
        tracing_module, "get_settings", lambda: Settings(otel_enabled=False), raising=False
    )
    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: Settings(otel_enabled=False)
    )
    assert configure_tracing() is False


def test_no_exporter_is_installed_without_an_endpoint():
    from app.core.tracing import _build_exporter

    assert _build_exporter(Settings(otel_enabled=True)) is None


def test_otlp_exporter_is_built_for_a_configured_endpoint():
    from app.core.tracing import _build_exporter

    exporter = _build_exporter(
        Settings(otel_enabled=True, otel_exporter_otlp_endpoint="http://localhost:4318/v1/traces")
    )
    assert exporter is not None
    assert exporter.__class__.__name__ == "OTLPSpanExporter"


def test_bad_exporter_endpoint_degrades_to_no_exporter(monkeypatch):
    """A broken exporter must not disable tracing or block boot."""
    import app.core.tracing as tracing_module

    real_import = __import__

    def _explode(name, *args, **kwargs):
        if "otlp" in name:
            raise RuntimeError("exporter unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _explode)
    assert tracing_module._build_exporter(
        Settings(otel_enabled=True, otel_exporter_otlp_endpoint="http://x")
    ) is None


def test_configure_tracing_instruments_a_fastapi_app():
    from fastapi import FastAPI

    app = FastAPI()
    assert configure_tracing(app=app, settings=Settings(otel_enabled=True)) is True
    # FastAPIInstrumentor marks the app once instrumented.
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True


def test_configure_tracing_swallows_provider_failure(monkeypatch):
    """Any unexpected telemetry error leaves tracing off but the app alive."""
    import app.core.tracing as tracing_module

    monkeypatch.setattr(
        tracing_module,
        "_build_exporter",
        lambda settings: (_ for _ in ()).throw(RuntimeError("kaboom")),
    )
    assert configure_tracing(app=None, settings=Settings(otel_enabled=True)) is False
    assert tracing_enabled() is False


def test_span_exceptions_propagate_unchanged():
    """Telemetry must not swallow business errors."""
    with pytest.raises(ValueError, match="boom"):
        with span("failing"):
            raise ValueError("boom")


# --- 2. Tracing on: real spans with operator-useful attributes ---
def test_span_records_name_and_attributes(recorded_spans):
    with span("order.execute", symbol="AAPL") as sp:
        sp.set_attribute("order.side", "buy")

    finished = recorded_spans.get_finished_spans()
    assert [s.name for s in finished] == ["order.execute"]
    assert finished[0].attributes["symbol"] == "AAPL"
    assert finished[0].attributes["order.side"] == "buy"


def test_current_trace_id_inside_span(recorded_spans):
    with span("outer"):
        trace_id = current_trace_id()
    assert len(trace_id) == 32 and int(trace_id, 16) != 0


def test_nested_spans_share_one_trace(recorded_spans):
    """signal -> order -> fill must stitch into a single trace for the operator."""
    with span("signal.generate", symbol="AAPL"):
        with span("order.execute", symbol="AAPL"):
            with span("fill.reconcile", source="webhook"):
                pass

    finished = recorded_spans.get_finished_spans()
    assert {s.name for s in finished} == {
        "signal.generate",
        "order.execute",
        "fill.reconcile",
    }
    assert len({s.context.trace_id for s in finished}) == 1  # one trace


@pytest.mark.asyncio
async def test_signal_path_emits_span(recorded_spans, disabled_ai):
    """MarketService.get_signal is the first leg of the traced hot path."""
    from tests.conftest import _FakeMarketProvider, _NoopCache
    from app.services.market_service import MarketService

    service = MarketService(_FakeMarketProvider(), disabled_ai, _NoopCache())
    await service.get_signal("AAPL")

    names = [s.name for s in recorded_spans.get_finished_spans()]
    assert "signal.generate" in names
    signal_span = next(
        s for s in recorded_spans.get_finished_spans() if s.name == "signal.generate"
    )
    assert signal_span.attributes["symbol"] == "AAPL"
    assert signal_span.attributes["signal.degraded"] is True  # AI disabled in tests
