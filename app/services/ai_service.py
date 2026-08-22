"""Claude wrapper: async, circuit-broken, strict-validated, RAG-grounded.

Signals and advice are grounded in retrieved news context and MUST cite the
headlines/sources they rely on; when no relevant context exists the model is
instructed to decline rather than hallucinate. Never blocks core quant data.
"""
import json
from typing import AsyncIterator, Optional, Type, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from app.core.circuit_breaker import CircuitBreaker
from app.core.config import Settings
from app.core.vector_store import RetrievedChunk, VectorStore
from app.models.market import SignalOut
from app.models.news import NewsInsightOut
from app.models.portfolio import AdviceOut

logger = structlog.get_logger("ai_service")
T = TypeVar("T", bound=BaseModel)

# Exact phrase the model must emit when retrieval yields no relevant grounding.
INSUFFICIENT = "Insufficient market data for a definitive conclusion."


class AIService:
    def __init__(
        self, settings: Settings, retriever: Optional[VectorStore] = None
    ) -> None:
        self._settings = settings
        self._retriever = retriever
        self._client = None
        self._breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        if settings.ai_enabled:
            try:
                from anthropic import AsyncAnthropic

                self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
            except Exception:  # noqa: BLE001 - missing dep -> degraded mode
                logger.warning("anthropic_client_unavailable")
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def available(self) -> bool:
        """Enabled AND circuit not open."""
        return self.enabled and self._breaker.allow()

    async def _retrieve(self, query: str, k: int = 4) -> list[RetrievedChunk]:
        """Best-effort RAG lookup. A retrieval failure never breaks a signal."""
        if self._retriever is None or not query.strip():
            return []
        try:
            return await self._retriever.search(query, k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rag_retrieval_failed", error=str(exc))
            return []

    def _request_headers(self) -> Optional[dict]:
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        return {"X-Request-Id": request_id} if request_id else None

    async def _complete(
        self, system: str, user: str, schema: Type[T]
    ) -> Optional[T]:
        """Call Claude, parse JSON, STRICT-validate. None on any failure.

        The circuit breaker short-circuits when the dependency is degraded so the
        caller instantly falls back to pure quantitative data.
        """
        if self._client is None or not self._breaker.allow():
            return None
        try:
            msg = await self._client.messages.create(
                model=self._settings.ai_model,
                max_tokens=self._settings.ai_max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                extra_headers=self._request_headers(),
            )
        except Exception as exc:  # noqa: BLE001 - any API error trips the breaker
            self._breaker.record_failure()
            logger.warning(
                "claude_call_failed", error=str(exc), breaker=self._breaker.state
            )
            return None

        self._breaker.record_success()
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        raw = _extract_json(text)
        if raw is None:
            logger.warning("claude_non_json_rejected")
            return None
        try:
            return schema.model_validate(raw)  # STRICT: extra keys forbidden
        except ValidationError as exc:
            logger.warning("claude_schema_validation_failed", error=str(exc))
            return None

    async def market_signal(
        self,
        symbol: str,
        quote: dict,
        indicators: dict,
        fundamentals: Optional[dict] = None,
        events: Optional[list[dict]] = None,
        blocking: Optional[str] = None,
    ) -> Optional[SignalOut]:
        context = await self._retrieve(symbol)
        system = (
            "You are a cautious equity analyst grounded in retrieved news CONTEXT. "
            "Weigh the technical indicators, the FUNDAMENTALS (P/E, EV/EBITDA, "
            "debt/equity, short ratio, earnings), and the categorized EVENTS "
            "(legal/macro/tech/executive) together with the CONTEXT. If a BLOCKING "
            "directive is present you MUST return action=HOLD, confidence<=0.2, and "
            "set rationale to that directive verbatim (DO NOT TRADE). If CONTEXT is "
            "empty or unrelated to the symbol, return action=HOLD, confidence<=0.2, "
            f"citations=[], and set rationale to EXACTLY: '{INSUFFICIENT}'. Return "
            "STRICT JSON ONLY with keys: action (BUY|SELL|HOLD), confidence (0-1 "
            "float), rationale (short Turkish), citations (list of strings). No "
            "extra keys, no prose."
        )
        user = json.dumps(
            {
                "symbol": symbol,
                "quote": quote,
                "indicators": indicators,
                "fundamentals": fundamentals or {},
                "events": events or [],
                "blocking_directive": blocking or "",
                "context": _context_payload(context),
            }
        )
        return await self._complete(system, user, SignalOut)

    async def news_insight(
        self, query: str, titles: list[str]
    ) -> Optional[NewsInsightOut]:
        system = (
            "Summarize financial news. Return STRICT JSON ONLY with exactly keys: "
            "summary (Turkish), sentiment (positive|neutral|negative), key_points "
            "(list of short Turkish strings). No extra keys, no prose."
        )
        user = json.dumps({"query": query, "headlines": titles})
        return await self._complete(system, user, NewsInsightOut)

    async def portfolio_advice(
        self, payload: dict, symbols: Optional[list[str]] = None
    ) -> Optional[AdviceOut]:
        query = " ".join(symbols or payload.get("symbols") or [])
        context = await self._retrieve(query)
        system = (
            "You are a risk-aware portfolio advisor grounded in retrieved news "
            "CONTEXT. Use the numeric risk metrics AND the CONTEXT only. You MUST "
            "cite the headlines/sources you used in `citations`. If CONTEXT is "
            "empty or unrelated, set narrative to EXACTLY: "
            f"'{INSUFFICIENT}' and citations to []. Return STRICT JSON ONLY with "
            "keys: narrative (Turkish), suggestions (list of short Turkish "
            "strings), citations (list of strings). No extra keys, no prose."
        )
        user = json.dumps({**payload, "context": _context_payload(context)})
        return await self._complete(system, user, AdviceOut)

    async def stream_insight(
        self, symbol: str, quote: dict, indicators: dict, context: list[RetrievedChunk]
    ) -> AsyncIterator[str]:
        """Yield a grounded narrative token-by-token for the WebSocket layer.

        Degraded mode (no client / open breaker) yields a single fallback line,
        and an empty CONTEXT yields the insufficient-data phrase, so the socket
        always receives a usable, non-hallucinated message.
        """
        if self._client is None or not self._breaker.allow():
            yield "AI devre disi veya gecici hata - yalnizca sayisal gostergeler mevcut."
            return
        if not context:
            yield INSUFFICIENT
            return
        system = (
            "You are a cautious equity analyst. Stream a concise Turkish analysis "
            "of the symbol using ONLY the given indicators and news CONTEXT, "
            "referencing the specific headlines you rely on inline. End with a "
            "note that this is not investment advice."
        )
        user = json.dumps(
            {
                "symbol": symbol,
                "quote": quote,
                "indicators": indicators,
                "context": _context_payload(context),
            }
        )
        try:
            async with self._client.messages.stream(
                model=self._settings.ai_model,
                max_tokens=self._settings.ai_max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                extra_headers=self._request_headers(),
            ) as stream:
                async for delta in stream.text_stream:
                    yield delta
            self._breaker.record_success()
        except Exception as exc:  # noqa: BLE001 - any stream error trips the breaker
            self._breaker.record_failure()
            logger.warning(
                "claude_stream_failed", error=str(exc), breaker=self._breaker.state
            )
            yield "\n[AI akisi kesildi - gecici hata.]"


def _context_payload(chunks: list[RetrievedChunk]) -> list[dict]:
    return [{"headline": c.headline, "source": c.source} for c in chunks]


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None
