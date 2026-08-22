"""News service: instant raw articles; RAG indexing on fetch; on-demand AI insight.

Every fetched headline is chunked and embedded into the shared vector store the
moment it arrives, so the AI layer can ground signals/insights in real, citable
news rather than hallucinating.
"""
from app.core.cache import CacheBackend
from app.core.vector_store import InMemoryVectorStore, RetrievedChunk, VectorStore
from app.models.news import Article, NewsInsight, NewsResponse
from app.providers.base import NewsProvider
from app.services.ai_service import AIService


class NewsService:
    def __init__(
        self,
        provider: NewsProvider,
        ai: AIService,
        cache: CacheBackend,
        store: VectorStore | None = None,
    ) -> None:
        self._provider = provider
        self._ai = ai
        self._cache = cache
        self._store: VectorStore = store or InMemoryVectorStore()

    async def _articles(self, query: str, limit: int) -> list[Article]:
        async def _factory():
            return await self._provider.fetch(query, limit)

        articles = await self._cache.get_or_set(f"news:{query}:{limit}", _factory)
        await self._index(articles)  # RAG: chunk + embed every fetched headline
        return articles

    async def _index(self, articles: list[Article]) -> None:
        """Chunk + embed fetched headlines into the vector store (RAG ingest)."""
        chunks = [
            RetrievedChunk(
                text=f"{a.title} ({a.source})" if a.source else a.title,
                headline=a.title,
                source=a.source,
                link=a.link,
            )
            for a in articles
            if a.title
        ]
        if chunks:
            await self._store.add_texts(chunks)

    async def retrieve_context(self, query: str, k: int = 4) -> list[RetrievedChunk]:
        """Top-k grounded, citable chunks for a query (symbol or free text)."""
        return await self._store.search(query, k)

    async def get_news(self, query: str, limit: int = 10) -> NewsResponse:
        query = query.strip()
        return NewsResponse(query=query, articles=await self._articles(query, limit))

    async def get_insight(self, query: str, limit: int = 10) -> NewsInsight:
        query = query.strip()
        articles = await self._articles(query, limit)
        titles = [a.title for a in articles if a.title]
        out = await self._ai.news_insight(query, titles)
        if out is None:
            return NewsInsight(
                summary="AI devre disi/gecici hata - yalnizca ham basliklar.",
                degraded=True,
            )
        return NewsInsight(
            summary=out.summary, sentiment=out.sentiment, key_points=out.key_points
        )
