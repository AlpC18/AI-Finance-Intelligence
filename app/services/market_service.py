"""Market service: instant quant data; AI signal is a separate, on-demand call."""
import asyncio

from app.core.cache import CacheBackend
from app.models.market import Indicators, MarketData, Quote, Signal
from app.providers.base import MarketDataProvider
from app.services.ai_service import AIService
from app.services.indicators import compute_indicators


class MarketService:
    def __init__(
        self,
        provider: MarketDataProvider,
        ai: AIService,
        cache: CacheBackend,
        events=None,
    ) -> None:
        self._provider = provider
        self._ai = ai
        self._cache = cache
        self._events_service = events

    async def _history(self, symbol: str):
        async def _factory():
            return await self._provider.get_history(symbol)

        return await self._cache.get_or_set(f"hist:{symbol}", _factory)

    async def _fundamentals(self, symbol: str):
        """Best-effort fundamentals via the provider; None if unavailable."""
        getter = getattr(self._provider, "get_fundamentals", None)
        if getter is None:
            return None

        async def _factory():
            return await getter(symbol)

        try:
            return await self._cache.get_or_set(f"fund:{symbol}", _factory)
        except Exception:  # noqa: BLE001 - fundamentals never break the hot path
            return None

    async def get_market_data(self, symbol: str) -> MarketData:
        """Raw quote + indicators + fundamentals — no AI in the hot path.

        History (required) and fundamentals (optional) are fetched concurrently
        so the extra metrics never serialize behind price data.
        """
        symbol = symbol.upper().strip()
        hist, fundamentals = await asyncio.gather(
            self._history(symbol), self._fundamentals(symbol)
        )
        close = hist["Close"]
        price = float(close.iloc[-1])
        change_pct = None
        if len(close) >= 2:
            prev = float(close.iloc[-2])
            if prev:
                change_pct = round((price - prev) / prev * 100, 2)
        return MarketData(
            quote=Quote(symbol=symbol, price=round(price, 4), change_pct=change_pct),
            indicators=compute_indicators(close),
            fundamentals=fundamentals,
        )

    async def _event_context(self):
        """Best-effort event intelligence for the signal; ([], None) on absence."""
        if self._events_service is None:
            return [], None
        try:
            return await self._events_service.ai_context()
        except Exception:  # noqa: BLE001 - events never break the signal path
            return [], None

    async def get_signal(self, symbol: str) -> Signal:
        """On-demand, RAG-grounded AI signal. Falls back to a safe degraded signal."""
        data = await self.get_market_data(symbol)
        events, blocking = await self._event_context()
        if blocking:
            # Circuit breaker: a high-impact imminent event halts trading BEFORE any
            # AI call — enforcement must not depend on the LLM choosing to comply.
            return Signal(action="HOLD", confidence=0.0, rationale=blocking)
        out = await self._ai.market_signal(
            data.quote.symbol,
            data.quote.model_dump(),
            data.indicators.model_dump(),
            fundamentals=data.fundamentals.model_dump() if data.fundamentals else None,
            events=events,
            blocking=blocking,
        )
        if out is None:
            return Signal(
                action="HOLD",
                degraded=True,
                rationale="AI devre disi/gecici hata - yalnizca gostergeler.",
            )
        return Signal(
            action=out.action,
            confidence=out.confidence,
            rationale=out.rationale,
            citations=out.citations,
        )
