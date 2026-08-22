"""RAG pipeline: offline embedder, vector store retrieval, news indexing, grounding."""
import numpy as np
import pytest

from app.core.config import Settings
from app.core.vector_store import HashingEmbedder, InMemoryVectorStore, RetrievedChunk
from app.models.news import Article
from app.services.ai_service import INSUFFICIENT, AIService
from app.services.news_service import NewsService


def test_hashing_embedder_deterministic_and_normalized():
    emb = HashingEmbedder(dim=128)
    a = emb.embed(["Tesla rallies on strong earnings"])
    b = emb.embed(["Tesla rallies on strong earnings"])
    assert a.shape == (1, 128)
    assert np.allclose(a, b)  # deterministic
    assert np.isclose(np.linalg.norm(a[0]), 1.0)  # L2-normalized


def test_hashing_embedder_empty_text_is_safe():
    emb = HashingEmbedder(dim=64)
    v = emb.embed([""])  # no divide-by-zero, returns a finite zero-ish vector
    assert v.shape == (1, 64)
    assert np.all(np.isfinite(v))


@pytest.mark.asyncio
async def test_vector_store_retrieves_relevant_over_irrelevant():
    store = InMemoryVectorStore()
    await store.add_texts(
        [
            RetrievedChunk(text="AAPL soars after record iPhone sales", headline="AAPL soars", link="1"),
            RetrievedChunk(text="Weather forecast sunny in the desert", headline="Weather", link="2"),
        ]
    )
    hits = await store.search("AAPL iPhone sales", k=1)
    assert len(hits) == 1
    assert hits[0].headline == "AAPL soars"
    assert hits[0].score > 0


@pytest.mark.asyncio
async def test_vector_store_empty_query_and_no_match_are_insufficient():
    store = InMemoryVectorStore()
    assert await store.search("anything") == []  # empty index
    await store.add_texts([RetrievedChunk(text="unrelated cooking recipe", link="1")])
    assert await store.search("quantum semiconductor", min_score=0.5) == []


@pytest.mark.asyncio
async def test_vector_store_dedups_by_link():
    store = InMemoryVectorStore()
    chunk = RetrievedChunk(text="NVDA up on AI demand", headline="NVDA up", link="dup")
    await store.add_texts([chunk])
    await store.add_texts([chunk])  # same link -> ignored
    assert await store.size() == 1


@pytest.mark.asyncio
async def test_vector_store_capacity_evicts_oldest():
    store = InMemoryVectorStore(capacity=2)
    for i in range(3):
        await store.add_texts([RetrievedChunk(text=f"headline {i}", link=str(i))])
    assert await store.size() == 2  # bounded, oldest dropped


@pytest.mark.asyncio
async def test_news_service_indexes_on_fetch_and_retrieves():
    class _Provider:
        async def fetch(self, query, limit=10):
            return [Article(title=f"{query} beats revenue estimates", link="x1", source="Wire")]

    class _NoopCache:
        async def get_or_set(self, key, factory, ttl=None):
            return await factory()

    store = InMemoryVectorStore()
    news = NewsService(_Provider(), AIService(Settings(anthropic_api_key="")), _NoopCache(), store=store)
    await news.get_news("TSLA")  # ingest happens here
    hits = await news.retrieve_context("TSLA revenue", k=3)
    assert any("TSLA" in h.headline for h in hits)


@pytest.mark.asyncio
async def test_ai_service_retriever_wired_but_degraded_returns_none():
    store = InMemoryVectorStore()
    await store.add_texts([RetrievedChunk(text="MSFT cloud growth strong", headline="MSFT", link="1")])
    ai = AIService(Settings(anthropic_api_key=""), retriever=store)
    # Retrieval works even while the LLM is disabled...
    assert await ai._retrieve("MSFT") != []
    # ...but a disabled LLM safely yields None (caller falls back to quant only).
    assert await ai.market_signal("MSFT", {"price": 1.0}, {"rsi_14": 50}) is None


@pytest.mark.asyncio
async def test_stream_insight_degraded_and_insufficient_paths():
    ai = AIService(Settings(anthropic_api_key=""))  # no client
    chunks = [c async for c in ai.stream_insight("AAPL", {}, {}, [RetrievedChunk(text="x")])]
    assert chunks and "devre disi" in chunks[0]

    # Enabled-but-no-context should emit the exact insufficient-data phrase.
    ai_enabled = AIService(Settings(anthropic_api_key=""))
    ai_enabled._client = object()  # pretend enabled; breaker allows by default
    out = [c async for c in ai_enabled.stream_insight("AAPL", {}, {}, [])]
    assert out == [INSUFFICIENT]
