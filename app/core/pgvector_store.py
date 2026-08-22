"""Persistent, cross-worker RAG vector store backed by PostgreSQL + pgvector.

Unlike ``InMemoryVectorStore`` (per-process, lost on restart), this keeps the
embedded corpus in Postgres so every Uvicorn worker reads/writes ONE shared index
that survives restarts. It satisfies the same ``VectorStore`` protocol, so the
news service and AI layer are unchanged.

Design:
- Embeddings are computed with the offline ``HashingEmbedder`` (numpy) and that
  CPU work is pushed to a threadpool so the event loop never blocks.
- All SQL runs over an ``asyncpg`` pool — non-blocking I/O end to end.
- The pool is injectable (``pool=``) so the query/parse logic is unit-testable
  without a live database.
- De-duplication by ``link`` uses ``ON CONFLICT DO NOTHING``; capacity is bounded
  by trimming the oldest rows after each insert.

Degraded-first: construction never touches the network; the pool is created
lazily on first use, and the caller (``deps``) falls back to the in-memory store
if Postgres is unreachable.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from app.core.vector_store import Embedder, HashingEmbedder, RetrievedChunk, _key


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _safe_identifier(name: str) -> str:
    """Validate a SQL identifier before it is ever interpolated into a statement.

    Table names cannot be passed as bind parameters, so the name is interpolated.
    It is developer-supplied rather than user-supplied, but validating here means
    the class has no string-injection path at all regardless of how it is wired.
    """
    if not _IDENTIFIER.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def _normalize_dsn(url: str) -> str:
    """asyncpg wants a bare postgres DSN, not a SQLAlchemy '+driver' URL."""
    if url.startswith("postgresql+"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgres+"):
        return "postgresql://" + url.split("://", 1)[1]
    return url


def _vector_literal(vec: Any) -> str:
    """pgvector text input format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


class PgVectorStore:
    """Postgres/pgvector implementation of the ``VectorStore`` protocol."""

    def __init__(
        self,
        dsn: str,
        embedder: Optional[Embedder] = None,
        capacity: int = 5000,
        table: str = "rag_chunks",
        pool: Optional[Any] = None,
    ) -> None:
        self._dsn = _normalize_dsn(dsn)
        self._embedder = embedder or HashingEmbedder()
        self._capacity = capacity
        self._table = _safe_identifier(table)
        self._pool = pool  # injectable for tests
        self._ready = False

    async def _get_pool(self) -> Any:
        if self._pool is None:
            import asyncpg  # local import: optional dependency, only in PG mode

            self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=4)
        return self._pool

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        pool = await self._get_pool()
        dim = self._embedder.dim
        async with pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("
                "id BIGSERIAL PRIMARY KEY, "
                "dedup_key TEXT UNIQUE NOT NULL, "
                "text TEXT NOT NULL, "
                "headline TEXT NOT NULL DEFAULT '', "
                "source TEXT NOT NULL DEFAULT '', "
                "link TEXT NOT NULL DEFAULT '', "
                f"embedding vector({dim}) NOT NULL"
                ");"
            )
        self._ready = True

    async def add_texts(self, items: list[RetrievedChunk]) -> None:
        """Embed and persist new chunks (dedup by link, oldest trimmed at capacity)."""
        fresh = [c for c in items if c.text.strip()]
        if not fresh:
            return
        await self._ensure_schema()
        vectors = await run_in_threadpool(
            self._embedder.embed, [c.text for c in fresh]
        )
        rows = [
            (
                _key(c),
                c.text,
                c.headline,
                c.source,
                c.link,
                _vector_literal(vectors[i]),
            )
            for i, c in enumerate(fresh)
        ]
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                f"INSERT INTO {self._table} "  # nosec B608 - identifier validated; values are bound
                "(dedup_key, text, headline, source, link, embedding) "
                "VALUES ($1, $2, $3, $4, $5, $6::vector) "
                "ON CONFLICT (dedup_key) DO NOTHING;",
                rows,
            )
            await conn.execute(
                f"DELETE FROM {self._table} WHERE id NOT IN "  # nosec B608 - identifier validated; capacity is bound
                f"(SELECT id FROM {self._table} ORDER BY id DESC LIMIT $1);",
                self._capacity,
            )

    async def search(
        self, query: str, k: int = 4, min_score: float = 0.1
    ) -> list[RetrievedChunk]:
        """Top-k cosine-nearest chunks above ``min_score`` (empty == insufficient)."""
        if not query.strip():
            return []
        await self._ensure_schema()
        vec = await run_in_threadpool(self._embedder.embed, [query])
        literal = _vector_literal(vec[0])
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            records = await conn.fetch(
                f"SELECT text, headline, source, link, "  # nosec B608 - identifier validated; vector is bound
                "1 - (embedding <=> $1::vector) AS score "
                f"FROM {self._table} ORDER BY embedding <=> $1::vector LIMIT $2;",
                literal,
                k,
            )
        hits: list[RetrievedChunk] = []
        for r in records:
            score = float(r["score"])
            if score < min_score:
                continue
            hits.append(
                RetrievedChunk(
                    text=r["text"],
                    headline=r["headline"] or "",
                    source=r["source"] or "",
                    link=r["link"] or "",
                    score=round(score, 4),
                )
            )
        return hits

    async def size(self) -> int:
        await self._ensure_schema()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return int(await conn.fetchval(f"SELECT COUNT(*) FROM {self._table};"))  # nosec B608 - identifier validated
