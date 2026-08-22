"""Ordered failover across market-data providers with in-memory health tracking.

Implements the ``MarketDataProvider`` protocol, so it is a drop-in for a single
provider. Each call tries providers in order; the first success wins and the
provider is marked healthy, a failure is recorded and the next provider is tried.
Only if EVERY provider fails does the call raise — one upstream outage no longer
freezes signal generation.

``health()`` returns the last-known state per provider WITHOUT making network
calls, so it is safe to expose on a health endpoint.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from pydantic import BaseModel

from app.core.errors import ProviderError
from app.core.metrics import record_provider_failover
from app.models.market import Fundamentals
from app.providers.base import MarketDataProvider


class ProviderHealth(BaseModel):
    name: str
    healthy: bool = True
    failures: int = 0
    last_error: Optional[str] = None


class FailoverMarketProvider:
    def __init__(self, providers: list[tuple[str, MarketDataProvider]]) -> None:
        if not providers:
            raise ValueError("FailoverMarketProvider requires at least one provider.")
        self._providers = providers
        self._health: dict[str, ProviderHealth] = {
            name: ProviderHealth(name=name) for name, _ in providers
        }

    async def get_history(self, symbol: str, period: str = "6mo") -> pd.DataFrame:
        return await self._run("get_history", symbol, period)

    async def get_price(self, symbol: str) -> float:
        return await self._run("get_price", symbol)

    async def get_fundamentals(self, symbol: str) -> Fundamentals:
        """Best-effort across providers; empty Fundamentals if all fail (never raises)."""
        for name, provider in self._providers:
            try:
                result = await provider.get_fundamentals(symbol)
                self._mark_healthy(name)
                return result
            except Exception as exc:  # noqa: BLE001 - fundamentals never break the path
                self._mark_failed(name, exc)
        return Fundamentals()

    async def _run(self, method: str, *args: object):
        last_exc: Optional[Exception] = None
        for name, provider in self._providers:
            try:
                result = await getattr(provider, method)(*args)
                self._mark_healthy(name)
                return result
            except Exception as exc:  # noqa: BLE001 - try the next provider
                self._mark_failed(name, exc)
                last_exc = exc
        raise ProviderError(
            f"Tum market veri saglayicilari basarisiz ({method})."
        ) from last_exc

    def _mark_healthy(self, name: str) -> None:
        h = self._health[name]
        h.healthy = True
        h.last_error = None

    def _mark_failed(self, name: str, exc: Exception) -> None:
        h = self._health[name]
        h.healthy = False
        h.failures += 1
        h.last_error = str(exc)[:200]
        record_provider_failover(name)  # operator signal: upstream degraded

    def health(self) -> list[ProviderHealth]:
        return [self._health[name] for name, _ in self._providers]
