"""Operator observability: the counters the alert rules fire on, and the rules themselves.

An alert rule that references a metric nobody emits is silently dead. These tests
pin the contract in both directions:

  * every metric name referenced by config/prometheus/alerts.yml is actually
    registered by the application, and
  * the hot path really increments them (reject reason, fill source, failover,
    disconnect, fallback).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from prometheus_client import REGISTRY

from app.core.errors import AppError, NotFoundError, ProviderError
from app.core.metrics import (
    record_alert_fallback,
    record_fill_reconciled,
    record_order_accepted,
    record_order_rejected,
    record_provider_failover,
    record_ws_disconnect,
)

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "prometheus" / "alerts.yml"

# Metrics this app owns. Series from prometheus-fastapi-instrumentator (http_*)
# and Prometheus itself (up) are provided by the scrape, not by our registry.
_APP_METRICS = {
    "ai_circuit_breaker_trips_total",
    "cache_events_total",
    "trade_orders_total",
    "trade_fills_reconciled_total",
    "market_provider_failovers_total",
    "ws_disconnects_total",
    "alert_fallback_deliveries_total",
}
_EXTERNAL_METRICS = {"up", "http_requests_total", "http_request_duration_seconds_bucket"}


def _counter_value(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# --- Alert rules are valid and wired to metrics that exist ---
def test_alert_rules_file_parses():
    rules = yaml.safe_load(RULES_PATH.read_text())
    groups = rules["groups"]
    assert groups, "no alert groups defined"
    for group in groups:
        assert group["name"] and group["rules"]


def test_every_alert_has_severity_and_annotations():
    rules = yaml.safe_load(RULES_PATH.read_text())
    for group in rules["groups"]:
        for rule in group["rules"]:
            assert rule["alert"], "unnamed alert"
            assert rule["labels"]["severity"] in {"critical", "warning"}
            assert rule["annotations"]["summary"]
            assert rule["annotations"]["description"]


def test_alert_expressions_reference_only_real_metrics():
    """Guards against rules that can never fire because the series doesn't exist."""
    rules = yaml.safe_load(RULES_PATH.read_text())
    known = _APP_METRICS | _EXTERNAL_METRICS
    referenced: set[str] = set()
    for group in rules["groups"]:
        for rule in group["rules"]:
            referenced.update(re.findall(r"\b([a-z_][a-z0-9_]*_total)\b", rule["expr"]))
            referenced.update(re.findall(r"\b(up|http_[a-z_]+)\b", rule["expr"]))
    unknown = referenced - known
    assert not unknown, f"alert rules reference unknown metrics: {sorted(unknown)}"


def test_app_metrics_are_registered_on_the_default_registry():
    """A labelled counter has no unlabelled sample, so assert it is *collectable*."""
    collected = {m.name for m in REGISTRY.collect()}
    for name in _APP_METRICS:
        assert name.removesuffix("_total") in collected, f"{name} not registered"


# --- Recorders actually move the counters ---
def test_order_counters_split_accept_and_reject_reason():
    before_ok = _counter_value("trade_orders_total", outcome="accepted", reason="none")
    before_bad = _counter_value(
        "trade_orders_total", outcome="rejected", reason="risk_halt"
    )

    record_order_accepted()
    record_order_rejected("risk_halt")

    assert _counter_value(
        "trade_orders_total", outcome="accepted", reason="none"
    ) == before_ok + 1
    assert _counter_value(
        "trade_orders_total", outcome="rejected", reason="risk_halt"
    ) == before_bad + 1


def test_order_reject_reason_defaults_when_blank():
    before = _counter_value("trade_orders_total", outcome="rejected", reason="unknown")
    record_order_rejected("")
    assert _counter_value(
        "trade_orders_total", outcome="rejected", reason="unknown"
    ) == before + 1


def test_fill_counter_distinguishes_webhook_from_poll():
    before = _counter_value("trade_fills_reconciled_total", source="webhook")
    record_fill_reconciled("webhook")
    assert _counter_value(
        "trade_fills_reconciled_total", source="webhook"
    ) == before + 1


def test_failover_disconnect_and_fallback_counters():
    b1 = _counter_value("market_provider_failovers_total", provider="yahoo")
    b2 = _counter_value("ws_disconnects_total", reason="error")
    b3 = _counter_value("alert_fallback_deliveries_total", result="delivered")
    b4 = _counter_value("alert_fallback_deliveries_total", result="failed")

    record_provider_failover("yahoo")
    record_ws_disconnect("error")
    record_alert_fallback(True)
    record_alert_fallback(False)

    assert _counter_value("market_provider_failovers_total", provider="yahoo") == b1 + 1
    assert _counter_value("ws_disconnects_total", reason="error") == b2 + 1
    assert _counter_value("alert_fallback_deliveries_total", result="delivered") == b3 + 1
    assert _counter_value("alert_fallback_deliveries_total", result="failed") == b4 + 1


# --- Reject reasons are structural, not parsed from localized messages ---
def test_app_error_carries_a_machine_reason():
    assert AppError("mesaj").reason == "app_error"
    assert AppError("mesaj", reason="notional").reason == "notional"
    assert ProviderError("mesaj").reason == "provider"
    assert NotFoundError("mesaj").reason == "not_found"
    assert ProviderError("mesaj").status_code == 502


def test_metrics_endpoint_exposes_app_counters(client):
    record_order_rejected("notional")
    body = client.get("/metrics").text
    assert "trade_orders_total" in body
    assert "ws_disconnects_total" in body or "ws_disconnects" in body


@pytest.mark.asyncio
async def test_rejected_order_increments_reject_counter_with_reason(disabled_ai):
    """End-to-end: a risk rejection in TradeService lands on the operator metric."""
    from sqlmodel import Session, SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    from app.core.config import Settings
    from app.models.broker import TradeRequest
    from app.services.trade_service import TradeService
    from tests.conftest import _FakeMarketProvider, _NoopCache
    from app.services.market_service import MarketService

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    market = MarketService(_FakeMarketProvider(), disabled_ai, _NoopCache())
    service = TradeService(Settings(trade_enabled=False), market)

    before = _counter_value("trade_orders_total", outcome="rejected", reason="disabled")
    with Session(engine) as session:
        with pytest.raises(AppError) as err:
            await service.execute(session, 1, TradeRequest(symbol="AAPL", action="BUY", quantity=1))

    assert err.value.reason == "disabled"
    assert _counter_value(
        "trade_orders_total", outcome="rejected", reason="disabled"
    ) == before + 1
