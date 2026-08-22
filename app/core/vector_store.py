"""Lightweight async in-memory vector store + offline embedder for RAG grounding.

Dependency-free (numpy only) so retrieval works with no external service and no
API key — the same degraded-first philosophy as the rest of the app. `Embedder`
and `VectorStore` are Protocols: swap `HashingEmbedder` for a real embedding API,
or `InMemoryVectorStore` for ChromaDB/Qdrant, without touching the news service.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import deque
from typing import Protocol

import numpy as np
from pydantic import BaseModel


class RetrievedChunk(BaseModel):
    """A grounded, citable piece of context returned from the store."""

    text: str
    headline: str = ""
    source: str = ""
    link: str = ""
    score: float = 0.0

    def citation(self) -> str:
        """Human-readable citation label (headline preferred, source fallback)."""
        return self.headline or self.source or self.text


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Deterministic, offline signed feature-hashing vectorizer.

    No network and no model download — reproducible in tests and good enough for
    headline-level semantic overlap. Rows are L2-normalized so a dot product
    between any two vectors is their cosine similarity.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in _tokenize(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                h = int.from_bytes(digest, "big")
                sign = 1.0 if (h >> 63) & 1 else -1.0  # signed hashing curbs collisions
                vecs[i, h % self.dim] += sign
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0  # avoid divide-by-zero for empty text
        return vecs / norms


def _tokenize(text: str) -> list[str]:
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    return [t for t in cleaned.split() if len(t) > 1]


class VectorStore(Protocol):
    async def add_texts(self, items: list[RetrievedChunk]) -> None: ...
    async def search(self, query: str, k: int = 4) -> list[RetrievedChunk]: ...


class InMemoryVectorStore:
    """Bounded, lock-guarded cosine-similarity store.

    Capacity-bounded (oldest chunks evicted) to keep memory flat under a long-
    running RSS ingest. De-duplicates by link so repeated feed polls don't bloat
    the index. All public methods are async to satisfy the `VectorStore` protocol
    and to stay swap-compatible with a real async vector DB client.
    """

    def __init__(self, embedder: Embedder | None = None, capacity: int = 512) -> None:
        self._embedder = embedder or HashingEmbedder()
        self._capacity = capacity
        self._chunks: deque[RetrievedChunk] = deque(maxlen=capacity)
        self._vectors: deque[np.ndarray] = deque(maxlen=capacity)
        self._seen: set[str] = set()
        self._lock = asyncio.Lock()

    async def add_texts(self, items: list[RetrievedChunk]) -> None:
        fresh = [c for c in items if c.text.strip() and _key(c) not in self._seen]
        if not fresh:
            return
        embeddings = self._embedder.embed([c.text for c in fresh])
        async with self._lock:
            for chunk, vec in zip(fresh, embeddings):
                key = _key(chunk)
                if key in self._seen:
                    continue  # racing add of the same article
                if len(self._chunks) == self._capacity and self._chunks:
                    self._seen.discard(_key(self._chunks[0]))  # about to be evicted
                self._chunks.append(chunk)
                self._vectors.append(vec)
                self._seen.add(key)

    async def search(
        self, query: str, k: int = 4, min_score: float = 0.1
    ) -> list[RetrievedChunk]:
        """Top-k chunks above `min_score`. Empty list == insufficient context."""
        if not query.strip():
            return []
        async with self._lock:
            if not self._vectors:
                return []
            matrix = np.vstack(self._vectors)
            chunks = list(self._chunks)
        scores = matrix @ self._embedder.embed([query])[0]  # cosine (rows normalized)
        order = np.argsort(scores)[::-1][:k]
        return [
            chunks[i].model_copy(update={"score": round(float(scores[i]), 4)})
            for i in order
            if scores[i] >= min_score
        ]

    async def size(self) -> int:
        async with self._lock:
            return len(self._chunks)


def _key(chunk: RetrievedChunk) -> str:
    return chunk.link or chunk.text
